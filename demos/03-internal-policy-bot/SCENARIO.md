# Scenario: HR policy bot with stale documents

Bot has both old and new policy versions in the corpus. Citations valid but contradict the retrieved newer chunks.

## Expected findings

- HM-CONFLICT-001 × 2 (vacation, remote work)

## Why this matters

Telltale sign of needing a doc cleanup or recency-weighted retrieval.
