# Scenario: E-commerce support RAG — one ungrounded pricing answer

Most answers grounded but pricing question hallucinated (likely because pricing page wasn't in corpus).

## Expected findings

- HM-GROUND-001 + HM-CITE-001 on Enterprise plan pricing

## Why this matters

Common gotcha: pricing pages aren't in product docs. RAG retrieves something close but fabricates the number.
