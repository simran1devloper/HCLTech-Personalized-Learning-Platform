"""Tutor persistence beyond create/get/add (which stay in LearnerStore): conversation management, reply lifecycle, feedback,
tool executions and usage. Every public method takes the learner id and scopes by it."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, exists, func, insert, or_, select, update

from app.infrastructure.database import tables as T
from app.infrastructure.database.engine import Db
from app.infrastructure.database.session import insert_for, utc_iso

C, M, META, FB, TX = T.tutor_conversations, T.tutor_messages, T.tutor_message_metadata, T.tutor_feedback, T.tutor_tool_executions
HIDDEN = "superseded"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TutorStore:
    def __init__(self, db: Db):
        self.db = db

    def _owned_message(self, learner_id: str, message_id: int):
        return select(M).join(C, C.c.conversation_id == M.c.conversation_id).where(M.c.message_id == message_id, C.c.learner_id == learner_id)

    # ---- conversations ----
    def list_conversations(self, learner_id: str, q: str | None = None, limit: int = 100) -> list[dict]:
        """Pinned first, then most recent. `q` searches titles and message text. Each row carries a preview of its last message."""
        cond = [C.c.learner_id == learner_id]
        term = (q or "").strip().lower()
        if term:
            hit = exists(select(M.c.message_id).where(M.c.conversation_id == C.c.conversation_id, M.c.status != HIDDEN,
                                                      func.lower(M.c.content).contains(term, autoescape=True)))
            cond.append(or_(func.lower(C.c.title).contains(term, autoescape=True), hit))
        with self.db.begin() as c:
            rows = [dict(r._mapping) for r in c.execute(select(C).where(*cond).order_by(C.c.pinned.desc(), C.c.updated_at.desc()).limit(limit))]
            ids = [r["conversation_id"] for r in rows]
            previews: dict[str, str] = {}
            if ids:
                last = (select(func.max(M.c.message_id)).where(M.c.conversation_id.in_(ids), M.c.status != HIDDEN, M.c.content != "")
                        .group_by(M.c.conversation_id))
                for cid, text in c.execute(select(M.c.conversation_id, func.substr(M.c.content, 1, 240)).where(M.c.message_id.in_(last))):
                    previews[cid] = " ".join(text.split())[:120]
        return [{**r, "preview": previews.get(r["conversation_id"], "")} for r in rows]

    def update_conversation(self, learner_id: str, conv_id: str, *, title: str | None = None, pinned: bool | None = None) -> bool:
        vals: dict = {}
        if title is not None:
            vals["title"] = title
        if pinned is not None:
            vals["pinned"] = pinned
        if not vals:
            return False
        with self.db.begin() as c:   # updated_at is left alone: renaming or pinning must not reorder the history
            return c.execute(update(C).where(C.c.conversation_id == conv_id, C.c.learner_id == learner_id).values(**vals)).rowcount == 1

    def delete_conversation(self, learner_id: str, conv_id: str) -> bool:
        with self.db.begin() as c:
            if not c.execute(select(C.c.conversation_id).where(C.c.conversation_id == conv_id, C.c.learner_id == learner_id)).first():
                return False
            ids = select(M.c.message_id).where(M.c.conversation_id == conv_id)
            for child in (META, FB, TX):   # explicit, so deletion does not depend on the database enforcing cascades
                c.execute(delete(child).where(child.c.message_id.in_(ids)))
            c.execute(delete(M).where(M.c.conversation_id == conv_id))
            c.execute(delete(C).where(C.c.conversation_id == conv_id))
        return True

    # ---- messages ----
    def thread(self, learner_id: str, conv_id: str, limit: int = 400) -> list[dict]:
        """Visible messages (oldest first) with metadata, the learner's feedback and tool executions."""
        with self.db.begin() as c:
            if not c.execute(select(C.c.conversation_id).where(C.c.conversation_id == conv_id, C.c.learner_id == learner_id)).first():
                return []
            rows = c.execute(select(M).where(M.c.conversation_id == conv_id, M.c.status != HIDDEN).order_by(M.c.message_id.desc()).limit(limit)).all()[::-1]
            msgs = [{"message_id": r.message_id, "role": r.role, "content": r.content, "created_at": r.created_at, "status": r.status,
                     "client_message_id": r.client_message_id} for r in rows]
            ids = [m["message_id"] for m in msgs]
            meta = {r.message_id: r for r in c.execute(select(META).where(META.c.message_id.in_(ids)))} if ids else {}
            fb = {r.message_id: r.rating for r in c.execute(select(FB.c.message_id, FB.c.rating).where(FB.c.message_id.in_(ids), FB.c.learner_id == learner_id))} if ids else {}
            tools: dict[int, list[dict]] = {}
            if ids:
                for r in c.execute(select(TX).where(TX.c.message_id.in_(ids), TX.c.learner_id == learner_id).order_by(TX.c.created_at)):
                    tools.setdefault(r.message_id, []).append(self._execution(r))
        for m in msgs:
            x = meta.get(m["message_id"])
            m["meta"] = None if x is None else {"intent": x.intent, "provider": x.provider, "model": x.model, "latency_ms": x.latency_ms, "first_token_ms": x.first_token_ms,
                                                "error_code": x.error_code, **(x.details or {})}
            m["feedback"] = {1: "up", -1: "down"}.get(fb.get(m["message_id"]))
            m["tools"] = tools.get(m["message_id"], [])
        return msgs

    def get_message(self, learner_id: str, message_id: int) -> dict | None:
        with self.db.begin() as c:
            r = c.execute(self._owned_message(learner_id, message_id)).first()
        return dict(r._mapping) if r else None

    def find_question(self, learner_id: str, client_message_id: str) -> dict | None:
        q = (select(M).join(C, C.c.conversation_id == M.c.conversation_id)
             .where(C.c.learner_id == learner_id, M.c.client_message_id == client_message_id, M.c.role == "user").order_by(M.c.message_id.desc()).limit(1))
        with self.db.begin() as c:
            r = c.execute(q).first()
        return dict(r._mapping) if r else None

    def set_message(self, message_id: int, *, content: str | None = None, status: str | None = None) -> None:
        """Internal (the stream that owns the reply). Callers have already checked ownership."""
        vals = {k: v for k, v in (("content", content), ("status", status)) if v is not None}
        if not vals:
            return
        with self.db.begin() as c:
            c.execute(update(M).where(M.c.message_id == message_id).values(**vals))
            cid = select(M.c.conversation_id).where(M.c.message_id == message_id).scalar_subquery()
            c.execute(update(C).where(C.c.conversation_id == cid).values(updated_at=utc_iso()))

    def supersede_after(self, conv_id: str, question_id: int) -> int:
        """Hide replies that followed a question before generating a new one (kept in the database, never shown or sent to the model)."""
        with self.db.begin() as c:
            return c.execute(update(M).where(M.c.conversation_id == conv_id, M.c.message_id > question_id, M.c.role == "assistant", M.c.status != HIDDEN)
                             .values(status=HIDDEN)).rowcount

    def save_meta(self, message_id: int, **values) -> None:
        values.setdefault("details", {})
        stmt = insert_for(self.db, META).values(message_id=message_id, **values).on_conflict_do_update(index_elements=["message_id"], set_=values)
        with self.db.begin() as c:
            if c.execute(select(M.c.message_id).where(M.c.message_id == message_id)).first():   # the conversation may have been deleted mid-stream
                c.execute(stmt)

    # ---- feedback ----
    def set_feedback(self, learner_id: str, message_id: int, rating: int | None, comment: str | None = None) -> bool:
        m = self.get_message(learner_id, message_id)
        if not m or m["role"] != "assistant":
            return False
        with self.db.begin() as c:
            c.execute(delete(FB).where(FB.c.message_id == message_id, FB.c.learner_id == learner_id))
            if rating is not None:
                c.execute(insert(FB).values(message_id=message_id, learner_id=learner_id, rating=rating, comment=comment, created_at=_now()))
        return True

    # ---- tool executions ----
    @staticmethod
    def _execution(r) -> dict:
        return {"execution_id": r.execution_id, "message_id": r.message_id, "tool": r.tool_name, "status": r.status, "args": r.args, "result": r.result}

    def add_execution(self, learner_id: str, message_id: int, tool: str, args: dict, status: str, result: dict | None = None) -> dict:
        eid, now = str(uuid.uuid4()), _now()
        with self.db.begin() as c:
            c.execute(insert(TX).values(execution_id=eid, message_id=message_id, learner_id=learner_id, tool_name=tool, status=status, args=args, result=result,
                                        created_at=now, updated_at=now))
        return {"execution_id": eid, "message_id": message_id, "tool": tool, "status": status, "args": args, "result": result}

    def get_execution(self, learner_id: str, execution_id: str) -> dict | None:
        with self.db.begin() as c:
            r = c.execute(select(TX).where(TX.c.execution_id == execution_id, TX.c.learner_id == learner_id)).first()
        return self._execution(r) if r else None

    def move_execution(self, learner_id: str, execution_id: str, from_status: tuple[str, ...], to: str, result: dict | None = None) -> bool:
        """Compare-and-set, so a double-clicked confirmation executes once."""
        vals: dict = {"status": to, "updated_at": _now()}
        if result is not None:
            vals["result"] = result
        with self.db.begin() as c:
            return c.execute(update(TX).where(TX.c.execution_id == execution_id, TX.c.learner_id == learner_id, TX.c.status.in_(from_status)).values(**vals)).rowcount == 1

    # ---- usage / evaluation ----
    def usage(self, learner_id: str) -> dict:
        """Measured figures from stored replies. Relevance and groundedness are not measured automatically and are not reported."""
        mine = select(M.c.message_id).join(C, C.c.conversation_id == M.c.conversation_id).where(C.c.learner_id == learner_id, M.c.role == "assistant")
        with self.db.begin() as c:
            statuses = dict(c.execute(select(M.c.status, func.count()).where(M.c.message_id.in_(mine)).group_by(M.c.status)).all())
            agg = c.execute(select(func.coalesce(func.sum(META.c.input_tokens), 0), func.coalesce(func.sum(META.c.output_tokens), 0), func.avg(META.c.first_token_ms),
                                   func.avg(META.c.latency_ms), func.count()).where(META.c.message_id.in_(mine))).one()
            ratings = dict(c.execute(select(FB.c.rating, func.count()).where(FB.c.learner_id == learner_id).group_by(FB.c.rating)).all())
            tools = dict(c.execute(select(TX.c.status, func.count()).where(TX.c.learner_id == learner_id).group_by(TX.c.status)).all())
            conversations = c.execute(select(func.count()).select_from(C).where(C.c.learner_id == learner_id)).scalar_one()
        ok, failed = statuses.get("complete", 0), statuses.get("failed", 0) + statuses.get("interrupted", 0)
        ran = tools.get("succeeded", 0) + tools.get("failed", 0)
        return {"conversations": int(conversations), "replies_by_status": statuses, "answer_success_rate": round(ok / (ok + failed), 3) if ok + failed else None,
                "input_tokens": int(agg[0]), "output_tokens": int(agg[1]), "avg_first_token_ms": round(agg[2], 1) if agg[2] is not None else None,
                "avg_completion_ms": round(agg[3], 1) if agg[3] is not None else None, "tool_executions": tools,
                "tool_success_rate": round(tools.get("succeeded", 0) / ran, 3) if ran else None,
                "feedback": {"up": ratings.get(1, 0), "down": ratings.get(-1, 0)}}
