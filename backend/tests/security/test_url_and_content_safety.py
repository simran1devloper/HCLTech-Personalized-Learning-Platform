import pytest
from pydantic import ValidationError
from app.core.exceptions import UnsafeURLError
from app.domains.content_engine.blocks import (CodeBlock, LearningModule, MermaidBlock, TableBlock, TextBlock,
                                               review_module, validate_mermaid)
from app.domains.resource_engine.url_safety import validate_url

PUBLIC = lambda host: ["93.184.216.34"]


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost/admin", "http://169.254.169.254/latest/meta-data/", "http://[::1]/",
    "http://10.0.0.5/", "http://192.168.1.1/", "http://172.16.0.1/", "http://0.0.0.0/", "http://100.64.0.1/",
    "http://[::ffff:127.0.0.1]/", "file:///etc/passwd", "ftp://example.com/x", "gopher://example.com",
    "http://user:pw@example.com/", "http://example.com:22/", "http://metadata.google.internal/", "http://svc.internal/",
    "javascript:alert(1)", "", "http://exa mple.com", "http://example.com/\r\nHost: evil", "http:///nohost",
])
def test_blocked_urls(url):
    with pytest.raises(UnsafeURLError):
        validate_url(url, PUBLIC)


def test_dns_rebinding_to_private_blocked():
    with pytest.raises(UnsafeURLError):
        validate_url("https://evil.example.com/", lambda h: ["93.184.216.34", "127.0.0.1"])
    with pytest.raises(UnsafeURLError):
        validate_url("https://nxdomain.example/", lambda h: (_ for _ in ()).throw(OSError()))


def test_public_url_normalised():
    assert validate_url("https://Docs.Python.org/3/tutorial/#frag", PUBLIC) == "https://docs.python.org/3/tutorial/"


@pytest.mark.parametrize("bad", ["<script>alert(1)</script>", "hi <img src=x onerror=alert(1)>", "[x](javascript:alert(1))",
                                 "<iframe src='//x'></iframe>", "<svg onload=1>"])
def test_unsafe_text_rejected(bad):
    with pytest.raises(ValidationError):
        TextBlock(markdown=bad)


def test_table_cells_checked_and_rectangular():
    with pytest.raises(ValidationError):
        TableBlock(headers=["a"], rows=[["<script>x</script>"]])
    with pytest.raises(ValidationError):
        TableBlock(headers=["a", "b"], rows=[["1"]])


def test_mermaid_validation():
    assert validate_mermaid("flowchart TD\n A[Start] --> B(End)")
    for bad in ["", "not a diagram", "flowchart TD\n A[Start --> B", "flowchart TD\n A-->B\n click A call evil()",
                "%%{init: {}}%%\nflowchart TD\n A-->B", "flowchart TD"]:
        with pytest.raises(ValueError):
            validate_mermaid(bad)
    with pytest.raises(ValidationError):
        MermaidBlock(source="graph TD\n A-->B\n A-->C[<script>]")


def test_module_requires_all_sections_and_review_flags_missing_sources():
    t = TextBlock(markdown="A sufficiently long explanation of the concept.")
    base = dict(skill_id="s", title="t", target_level=3, learning_objectives=["o"], concept_explanation=[t],
                practical_examples=[t], common_mistakes=[t], knowledge_checks=[t])
    m = LearningModule(**base)
    issues = review_module(m)
    assert any("source" in i for i in issues) and any("code block" in i for i in issues)
    base["practical_examples"] = [CodeBlock(language="python", code="print(1)")]
    base["sources"] = [{"title": "docs", "url": "https://docs.python.org/3/"}]
    assert review_module(LearningModule(**base)) == []
    with pytest.raises(ValidationError):
        LearningModule(**{**base, "knowledge_checks": []})
    with pytest.raises(ValidationError):
        LearningModule(**{**base, "concept_explanation": [{"type": "video", "url": "x"}]})
