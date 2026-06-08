# Scenario: Legal-research RAG quality audit

Audit of a legal Q&A assistant. Two clean answers, two problematic.

## Expected findings

- HM-GROUND-001 (ungrounded) on 'emotional distress'
- HM-CITE-001 (bad citation) on emotional distress + statute
- HM-CONFLICT-001 (contradicts context) on statute of limitations

## Why this matters

In legal/medical/financial domains, ungrounded answers are malpractice. HALLUMARK is the regression test.
