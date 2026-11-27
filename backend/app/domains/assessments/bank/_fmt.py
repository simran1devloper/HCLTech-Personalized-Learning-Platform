"""Compact authoring format for the seed question bank.

``Q(concept, difficulty, type, cognitive, text, options, correct, explanation, code=None, sub=None)``
type: sc single_choice | ms multiple_select | co code_output | dbg debugging | scn scenario | con conceptual | prac practical
cognitive: R remember | U understand | Ap apply | An analyze | E evaluate
``correct`` is an option index (or list of indices for ms). ``code`` is a snippet shown with the question."""
from __future__ import annotations

TYPES = {"sc": "single_choice", "ms": "multiple_select", "co": "code_output", "dbg": "debugging", "scn": "scenario", "con": "conceptual", "prac": "practical"}
COG = {"R": "remember", "U": "understand", "Ap": "apply", "An": "analyze", "E": "evaluate"}


_DEFAULT_COG = {"sc": "U", "ms": "U", "co": "Ap", "dbg": "An", "scn": "An", "con": "U", "prac": "Ap"}


def Q(concept, d, t, *args, code=None, sub=None, approach="", example=""):
    """Q(concept, d, type, [cognitive,] text, options, correct, explanation). The cognitive code may be omitted (defaults by type)."""
    cog, rest = (args[0], args[1:]) if args and args[0] in COG else (_DEFAULT_COG[t], args)
    text, options, correct, why = rest
    return dict(concept=concept, subtopic=sub or concept, difficulty=d, question_type=TYPES[t], cognitive_level=COG[cog], question_text=text, options=list(options),
                correct=[correct] if isinstance(correct, int) else list(correct), explanation=why, code_snippet=code, solution_approach=approach, practical_example=example)
