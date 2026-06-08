"""Smoke tests for HALLUMARK: import core, run on the demo, assert real behavior.

Run with:  python -m pytest tests/test_smoke.py
      or:  python tests/test_smoke.py   (no third-party deps required)
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hallumark import (  # noqa: E402
    TOOL_NAME,
    TOOL_VERSION,
    audit_record,
    audit_records,
    load_records,
    split_claims,
)
from hallumark.cli import main  # noqa: E402

DEMO = os.path.join(
    os.path.dirname(__file__), "..", "demos", "01-basic", "rag_records.jsonl"
)


def test_metadata():
    assert TOOL_NAME == "hallumark"
    assert TOOL_VERSION.count(".") == 2


def test_split_claims():
    claims = split_claims("Paris is in France. It is large.")
    assert len(claims) == 2
    assert any("Paris" in c for c in claims)


def test_grounded_answer_passes():
    rec = {
        "id": "g1",
        "question": "Where is the tower?",
        "contexts": ["The tower is located in Paris, France."],
        "answer": "The tower is located in Paris, France.",
    }
    audit = audit_record(rec)
    assert audit.passed is True
    assert audit.n_unsupported == 0
    assert audit.faithfulness == 1.0


def test_fabricated_number_is_flagged():
    rec = {
        "id": "n1",
        "question": "What was revenue?",
        "contexts": ["Acme reported revenue of 2.8 billion dollars."],
        "answer": "Acme reported revenue of 4.2 billion dollars.",
    }
    audit = audit_record(rec)
    assert audit.passed is False
    assert audit.n_unsupported >= 1
    # The unsupported claim must cite the numeric-mismatch reason.
    reasons = " ".join(r for c in audit.claims for r in c.reasons)
    assert "numeric" in reasons.lower()


def test_unrelated_answer_is_unsupported():
    rec = {
        "id": "u1",
        "question": "Who founded it?",
        "contexts": ["The company was founded in 1998 in Seattle."],
        "answer": "It was founded by Albert Einstein in Zurich.",
    }
    audit = audit_record(rec)
    assert audit.passed is False
    assert audit.n_unsupported >= 1


def test_demo_file_loads_and_audits():
    records = load_records(DEMO)
    assert len(records) == 4
    report = audit_records(records)
    assert report.n_records == 4
    # The demo is built so exactly one record (q1) is fully grounded.
    assert report.n_passed == 1
    assert report.n_failed == 3
    assert report.total_unsupported >= 3
    assert report.has_findings is True
    # Per-record expectations.
    by_id = {r.record_id: r for r in report.records}
    assert by_id["q1"].passed is True
    assert by_id["q2"].passed is False
    assert by_id["q3"].passed is False
    assert by_id["q4"].passed is False


def test_report_json_roundtrips():
    report = audit_records(load_records(DEMO))
    d = report.to_dict()
    assert d["has_findings"] is True
    assert isinstance(d["records"], list)
    assert "claims" in d["records"][0]


def test_cli_returns_nonzero_on_findings():
    # Demo has findings -> exit code 1.
    assert main(["audit", DEMO]) == 1
    assert main(["audit", DEMO, "--format", "json"]) == 1


def test_cli_no_args_is_help_and_zero():
    assert main([]) == 0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok", fn.__name__)
    print("all %d tests passed" % len(fns))
