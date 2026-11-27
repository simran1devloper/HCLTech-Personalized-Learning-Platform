"""LaTeX validation, Layer 2: authoritative KaTeX parse in a persistent Node worker (ADR 0007).

Layer 1 (policy lint, pure Python) always runs; Layer 2 runs when Node and KaTeX are available. When Layer 2 is not
available the report is flagged ``partial`` ("latex_katex_parse") and is never presented as fully validated.
"""
from __future__ import annotations

import json
import os
import re
import select
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Sequence

from app.domains.content_engine.findings import Finding, ValidationReport
from app.domains.content_engine.latex_policy import lint_latex

_BACKEND = Path(__file__).resolve().parents[4]
DEFAULT_KATEX = _BACKEND.parent / "web" / "node_modules" / "katex"
WORKER_SCRIPT = _BACKEND / "tools" / "katex_worker.mjs"
_INLINE_MATH = re.compile(r"\$\$(.+?)\$\$|(?<![\\$])\$(?!\s)([^$\n]+?)(?<!\s)\$(?!\d)", re.S)


class KatexRunner:
    """Persistent child process speaking JSON lines. Thread-safe; restarts after a crash or timeout."""

    def __init__(self, katex_path: str | Path | None = None, timeout_s: float = 5.0, worker_cmd: list[str] | None = None):
        self.katex_path = str(katex_path or os.environ.get("KATEX_MODULE_PATH") or DEFAULT_KATEX)
        self.timeout_s = timeout_s
        self._cmd = worker_cmd
        self._proc: subprocess.Popen | None = None
        self._buf = b""
        self._lock = threading.Lock()
        self._next = 0
        self.restarts = 0

    # ---- availability
    def available(self) -> bool:
        if self._cmd:
            return True
        return bool(shutil.which("node")) and Path(self.katex_path, "package.json").exists() and WORKER_SCRIPT.exists()

    def _start(self) -> None:
        cmd = self._cmd or [shutil.which("node") or "node", "--max-old-space-size=128", str(WORKER_SCRIPT), self.katex_path]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._buf = b""
        banner = self._readline(self.timeout_s * 4)       # first start loads Node + KaTeX
        if banner is None or b'"ready"' not in banner:
            self.close()
            raise RuntimeError("KaTeX worker did not start")

    def close(self) -> None:
        p, self._proc = self._proc, None
        if p and p.poll() is None:
            p.kill()
        if p:
            try:
                p.wait(timeout=2)
            except Exception:  # noqa: BLE001
                pass

    def _readline(self, timeout: float) -> bytes | None:
        assert self._proc and self._proc.stdout
        deadline, fd = time.monotonic() + timeout, self._proc.stdout.fileno()
        while b"\n" not in self._buf:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            if select.select([fd], [], [], left)[0]:
                chunk = os.read(fd, 65536)
                if not chunk:
                    return None
                self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line

    # ---- API
    def check(self, tex: str, display: bool = True) -> tuple[bool, str]:
        """Returns (ok, message). Raises RuntimeError if the worker cannot be (re)started."""
        with self._lock:
            for attempt in (1, 2):
                if self._proc is None or self._proc.poll() is not None:
                    if attempt == 2 or self._proc is not None:
                        self.restarts += 1
                    self._start()
                self._next += 1
                rid = self._next
                try:
                    assert self._proc and self._proc.stdin
                    self._proc.stdin.write((json.dumps({"id": rid, "tex": tex, "display": display}) + "\n").encode())
                    self._proc.stdin.flush()
                    line = self._readline(self.timeout_s)
                except (BrokenPipeError, OSError):
                    line = None
                if line is None:                       # crash or timeout: kill, restart once, retry once
                    self.close()
                    if attempt == 2:
                        return False, "KaTeX worker timed out or crashed"
                    continue
                reply = json.loads(line)
                if reply.get("id") == rid:
                    return bool(reply["ok"]), reply.get("error", "")
            return False, "KaTeX worker did not answer"


def _math_sources(block: dict) -> list[tuple[str, bool]]:
    t = block.get("type")
    if t == "math":
        return [(block.get("latex", ""), bool(block.get("display", True)))]
    out: list[tuple[str, bool]] = []
    texts = []
    if t in ("text", "callout"):
        texts.append(block.get("markdown") or block.get("text") or "")
    if t in ("list",):
        texts += block.get("items", [])
    for text in texts:
        for m in _INLINE_MATH.finditer(text):
            display = m.group(1) is not None
            out.append(((m.group(1) or m.group(2)).strip(), display))
    return out


def validate_latex(blocks: Sequence[dict], runner: KatexRunner | None = None) -> ValidationReport:
    rep = ValidationReport()
    use_node = runner is not None and runner.available()
    if not use_node:
        rep.partial.append("latex_katex_parse")
    for b in blocks:
        for tex, display in _math_sources(b):
            lint = lint_latex(tex, b.get("id"))
            rep.extend(lint)
            if use_node and not any(f.severity == "critical" for f in lint):   # never hand forbidden input to the parser
                try:
                    ok, msg = runner.check(tex, display)  # type: ignore[union-attr]
                except RuntimeError:
                    if "latex_katex_parse" not in rep.partial:
                        rep.partial.append("latex_katex_parse")
                    continue
                if not ok:
                    rep.findings.append(Finding("latex_parse_error", "error", msg or "KaTeX could not parse the expression", b.get("id")))
    return rep
