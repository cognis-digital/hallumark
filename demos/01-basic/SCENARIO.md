# Demo 01 - Basic RAG grounding audit

This demo runs HALLUMARK over a small batch of RAG records captured from a
question-answering pipeline. Each record contains the user's `question`, the
retrieved `contexts` (the chunks the retriever returned), and the model's
`answer`.

The batch is deliberately mixed so you can see HALLUMARK catch real failure
modes that plague RAG systems:

| id   | what it demonstrates                                              | expected |
|------|-------------------------------------------------------------------|----------|
| q1   | answer fully supported by context                                 | PASS     |
| q2   | a fabricated numeric value (the "$4.2 billion" is not in context) | FAIL     |
| q3   | a hallucinated entity/claim with no overlap to any chunk          | FAIL     |
| q4   | answer flips the polarity of the context (negation mismatch)      | FAIL     |

## Input

`rag_records.jsonl` - one JSON object per line.

## Run it

```bash
# Human-readable table (exits 1 because findings exist):
python -m hallumark audit demos/01-basic/rag_records.jsonl

# Machine-readable JSON for CI / pipelines:
python -m hallumark audit demos/01-basic/rag_records.jsonl --format json

# Also show the claims that passed:
python -m hallumark audit demos/01-basic/rag_records.jsonl --show-grounded
```

## What to look for

- `q1` passes: every claim has support above the threshold.
- `q2` fails with a `numeric value(s) not found in context` reason.
- `q3` fails with `no meaningful overlap with any retrieved context chunk`.
- `q4` fails with `negation/polarity mismatch with best-matching context`.

The process exits non-zero whenever any unsupported claim is found, so you can
drop HALLUMARK straight into a CI gate for your RAG eval set.
