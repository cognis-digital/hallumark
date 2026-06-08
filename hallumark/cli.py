"""HALLUMARK command-line interface.

Usage examples:
    hallumark audit demos/01-basic/rag_records.jsonl
    hallumark audit records.json --format json
    hallumark audit records.json --threshold 0.35 --min-faithfulness 0.9
    hallumark --version

Exit codes:
    0  all records passed, no unsupported claims
    1  hallucination findings (unsupported claims / failed records)
    2  usage / input error
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import TOOL_NAME, TOOL_VERSION
from .core import AuditReport, audit_records, load_records


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "HALLUMARK - audit LLM/RAG answers for hallucinations by checking "
            "whether each claim is grounded in the retrieved context."
        ),
        epilog=(
            "Input is JSON or JSONL where each record has: question, answer, "
            "and contexts (a list of retrieved chunks). Returns non-zero exit "
            "when unsupported claims are found."
        ),
    )
    p.add_argument(
        "--version", action="version", version="%(prog)s " + TOOL_VERSION
    )
    sub = p.add_subparsers(dest="command", metavar="<command>")

    audit = sub.add_parser(
        "audit",
        help="Audit a file of RAG records for ungrounded / hallucinated claims.",
        description="Audit RAG records and report grounding/faithfulness findings.",
    )
    audit.add_argument(
        "input",
        help="Path to a .json or .jsonl file of RAG records (- for stdin).",
    )
    audit.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table).",
    )
    audit.add_argument(
        "--threshold",
        type=float,
        default=0.3,
        help="Per-claim support threshold to count as grounded (default: 0.3).",
    )
    audit.add_argument(
        "--min-faithfulness",
        type=float,
        default=0.8,
        dest="min_faithfulness",
        help="Minimum record faithfulness to PASS (default: 0.8).",
    )
    audit.add_argument(
        "--show-grounded",
        action="store_true",
        help="In table mode, also list claims that ARE grounded.",
    )
    return p


def _read_input(path: str) -> List[dict]:
    if path == "-":
        from .core import parse_records

        return parse_records(sys.stdin.read())
    return load_records(path)


def _print_table(report: AuditReport, show_grounded: bool, out) -> None:
    w = out.write
    w("HALLUMARK grounding audit\n")
    w("=" * 60 + "\n")
    w(
        "records: {n}  passed: {p}  failed: {f}  "
        "unsupported claims: {u}\n".format(
            n=report.n_records,
            p=report.n_passed,
            f=report.n_failed,
            u=report.total_unsupported,
        )
    )
    w(
        "mean faithfulness: {ff:.2f}   ctx-utilization: {cu:.2f}   "
        "answer-relevance: {ar:.2f}\n".format(
            ff=report.mean_faithfulness,
            cu=report.mean_context_utilization,
            ar=report.mean_answer_relevance,
        )
    )
    w("threshold: {t}\n".format(t=report.threshold))
    w("-" * 60 + "\n")

    for r in report.records:
        status = "PASS" if r.passed else "FAIL"
        label = r.record_id or "(no-id)"
        w(
            "[{s}] {rid}  faithfulness={ff:.2f}  "
            "claims={nc} unsupported={nu}\n".format(
                s=status,
                rid=label,
                ff=r.faithfulness,
                nc=r.n_claims,
                nu=r.n_unsupported,
            )
        )
        q = r.question.strip()
        if q:
            w("     Q: {q}\n".format(q=(q[:90] + "...") if len(q) > 90 else q))
        for c in r.claims:
            if c.grounded and not show_grounded:
                continue
            mark = "ok" if c.grounded else "XX"
            ct = c.text
            ct = (ct[:80] + "...") if len(ct) > 80 else ct
            w("     [{m}] ({sup:.2f}) {ct}\n".format(m=mark, sup=c.support, ct=ct))
            for reason in c.reasons:
                w("            -> {reason}\n".format(reason=reason))
        w("\n")

    if report.has_findings:
        w(
            "FINDINGS: {u} unsupported claim(s) across {f} failing record(s).\n".format(
                u=report.total_unsupported, f=report.n_failed
            )
        )
    else:
        w("No hallucination findings. All answers are grounded.\n")


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    if args.command == "audit":
        if not (0.0 <= args.threshold <= 1.0):
            parser.error("--threshold must be between 0.0 and 1.0")
        if not (0.0 <= args.min_faithfulness <= 1.0):
            parser.error("--min-faithfulness must be between 0.0 and 1.0")
        try:
            records = _read_input(args.input)
        except FileNotFoundError:
            sys.stderr.write("error: input file not found: %s\n" % args.input)
            return 2
        except (json.JSONDecodeError, ValueError) as exc:
            sys.stderr.write("error: could not parse input: %s\n" % exc)
            return 2

        if not records:
            sys.stderr.write("error: no records found in input\n")
            return 2

        report = audit_records(
            records,
            threshold=args.threshold,
            pass_faithfulness=args.min_faithfulness,
        )

        if args.format == "json":
            sys.stdout.write(json.dumps(report.to_dict(), indent=2) + "\n")
        else:
            _print_table(report, args.show_grounded, sys.stdout)

        return 1 if report.has_findings else 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
