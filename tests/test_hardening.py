"""Tests for hardening: error handling, edge cases, and input validation.

These tests cover the new defensive paths added for production robustness:
  - CLI returning exit code 2 on I/O errors and bad input
  - parse_records giving context on malformed JSONL lines
  - audit_records skipping non-dict entries gracefully
  - audit_record ignoring non-string context items
  - load_records surfacing OS errors with context
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hallumark.cli import main
from hallumark.core import audit_record, audit_records, load_records, parse_records

DEMO = os.path.join(
    os.path.dirname(__file__), "..", "demos", "01-basic", "rag_records.jsonl"
)


# ---------------------------------------------------------------------------
# CLI error paths
# ---------------------------------------------------------------------------

class TestCLIMissingFile(unittest.TestCase):
    def test_missing_file_returns_exit2(self):
        code = main(["audit", "/nonexistent/totally/missing/file.jsonl"])
        self.assertEqual(code, 2)

    def test_missing_file_writes_to_stderr(self):
        buf = io.StringIO()
        old = sys.stderr
        sys.stderr = buf
        try:
            main(["audit", "/nonexistent/totally/missing/file.jsonl"])
        finally:
            sys.stderr = old
        self.assertIn("not found", buf.getvalue())

    def test_directory_as_input_returns_exit2(self):
        # Passing a directory instead of a file should return 2, not traceback.
        with tempfile.TemporaryDirectory() as d:
            code = main(["audit", d])
        self.assertEqual(code, 2)

    def test_empty_file_returns_exit2(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False
        ) as f:
            fname = f.name
        try:
            code = main(["audit", fname])
            self.assertEqual(code, 2)
        finally:
            os.unlink(fname)

    def test_malformed_jsonl_line_returns_exit2(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            f.write('{"question":"q","answer":"a","contexts":["c"]}\n')
            f.write("NOT JSON\n")
            fname = f.name
        try:
            code = main(["audit", fname])
            self.assertEqual(code, 2)
        finally:
            os.unlink(fname)


# ---------------------------------------------------------------------------
# parse_records edge cases
# ---------------------------------------------------------------------------

class TestParseRecords(unittest.TestCase):
    def test_empty_string_returns_empty_list(self):
        self.assertEqual(parse_records(""), [])

    def test_whitespace_only_returns_empty_list(self):
        self.assertEqual(parse_records("   \n  \t  "), [])

    def test_malformed_jsonl_raises_valueerror_with_line_number(self):
        text = '{"q":"a"}\nBAD LINE\n{"q":"b"}'
        with self.assertRaises(ValueError) as ctx:
            parse_records(text)
        self.assertIn("line 2", str(ctx.exception))

    def test_non_dict_entries_skipped_in_json_list(self):
        text = json.dumps(["string", 42, {"question": "q", "answer": "a", "contexts": []}])
        records = parse_records(text)
        self.assertEqual(len(records), 1)
        self.assertIsInstance(records[0], dict)

    def test_wrapped_records_key(self):
        text = json.dumps({"records": [
            {"question": "q", "answer": "a", "contexts": []},
        ]})
        records = parse_records(text)
        self.assertEqual(len(records), 1)


# ---------------------------------------------------------------------------
# audit_record edge cases
# ---------------------------------------------------------------------------

class TestAuditRecordEdgeCases(unittest.TestCase):
    def test_non_string_contexts_ignored(self):
        # None and int values in contexts must not coerce to "None"/"42" strings.
        rec = {
            "id": "t1",
            "question": "q",
            "answer": "some answer about things",
            "contexts": [None, 42, "valid context text here"],
        }
        audit = audit_record(rec)
        # Should complete without error; None/42 contexts must be dropped.
        # Only "valid context text here" should be used.
        self.assertIsNotNone(audit)
        # The valid context has 4 content tokens; None/42 should not
        # silently pad the context (if they were included, context_utilization
        # denominator would be 3 rather than 1).
        self.assertLessEqual(audit.context_utilization, 1.0)

    def test_empty_contexts_list(self):
        rec = {
            "id": "t2",
            "question": "q",
            "answer": "the sky is blue",
            "contexts": [],
        }
        audit = audit_record(rec)
        self.assertEqual(audit.context_utilization, 0.0)
        self.assertFalse(audit.passed)  # no context -> claims are unsupported

    def test_empty_answer(self):
        rec = {
            "id": "t3",
            "question": "q",
            "answer": "",
            "contexts": ["some context text here"],
        }
        audit = audit_record(rec)
        # Empty answer has no claims -> faithfulness defaults to 1.0.
        self.assertEqual(audit.n_claims, 0)
        self.assertEqual(audit.faithfulness, 1.0)


# ---------------------------------------------------------------------------
# audit_records edge cases
# ---------------------------------------------------------------------------

class TestAuditRecordsEdgeCases(unittest.TestCase):
    def test_non_dict_records_skipped(self):
        # A list that includes non-dict items must not raise AttributeError.
        records = [
            {"question": "q", "answer": "the sky is blue", "contexts": ["the sky is blue"]},
            "not a dict",
            42,
            None,
        ]
        report = audit_records(records)  # type: ignore[arg-type]
        # Only the one valid dict should be audited.
        self.assertEqual(report.n_records, 1)

    def test_empty_records_list(self):
        report = audit_records([])
        self.assertEqual(report.n_records, 0)
        self.assertEqual(report.n_passed, 0)
        self.assertFalse(report.has_findings)


# ---------------------------------------------------------------------------
# load_records error paths
# ---------------------------------------------------------------------------

class TestLoadRecordsErrors(unittest.TestCase):
    def test_missing_file_raises_filenotfounderror(self):
        with self.assertRaises(FileNotFoundError):
            load_records("/no/such/path/records.jsonl")

    def test_directory_raises_oserror(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(OSError):
                load_records(d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
