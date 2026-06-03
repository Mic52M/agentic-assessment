from agentic_orchestrator.privacy import scan_and_redact


def test_detects_and_redacts_email():
    text = "contact me at john.doe@acme.com please"
    result = scan_and_redact(text)
    kinds = {f["kind"] for f in result.findings}
    assert "email" in kinds
    assert "john.doe@acme.com" not in result.redacted_text
    assert "[EMAIL]" in result.redacted_text


def test_no_findings_on_clean_text():
    result = scan_and_redact("the app crashes when I click save")
    assert result.findings == []
    assert result.redacted_text == "the app crashes when I click save"


def test_preview_is_masked():
    result = scan_and_redact("write to a@b.com")
    (finding,) = result.findings
    assert finding["value_preview"] != "a@b.com"
    assert "*" in finding["value_preview"]
