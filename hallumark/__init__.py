"""HALLUMARK - LLM hallucination & grounding auditor for RAG systems.

HALLUMARK inspects RAG records (question, retrieved context, model answer) and
scores whether each answer is *grounded* in the supplied context. It splits the
answer into atomic claims, checks each claim's lexical/numeric support against the
retrieved context, and surfaces unsupported claims as hallucination findings.

Standard library only. No network. No install.
"""
from .core import (
    Claim,
    RecordAudit,
    AuditReport,
    audit_record,
    audit_records,
    load_records,
    split_claims,
)

TOOL_NAME = "hallumark"
TOOL_VERSION = "1.0.0"

__all__ = [
    "TOOL_NAME",
    "TOOL_VERSION",
    "Claim",
    "RecordAudit",
    "AuditReport",
    "audit_record",
    "audit_records",
    "load_records",
    "split_claims",
]
