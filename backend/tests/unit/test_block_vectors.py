"""Shared contract vectors: the Python schema and the TypeScript schema must agree on every case."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domains.content_engine.schema import SCHEMA_VERSION, validate_block

DOC = json.loads((Path(__file__).resolve().parents[3] / "contracts" / "block-vectors.json").read_text())


def test_vector_file_matches_the_schema_version_and_is_not_trivial():
    assert DOC["schema_version"] == SCHEMA_VERSION and len(DOC["vectors"]) >= 40
    assert {v["valid"] for v in DOC["vectors"]} == {True, False}


@pytest.mark.parametrize("v", DOC["vectors"], ids=[v["name"] for v in DOC["vectors"]])
def test_python_schema_agrees_with_the_shared_vector(v):
    if v["valid"]:
        validate_block(v["block"])
    else:
        with pytest.raises(ValidationError):
            validate_block(v["block"])
