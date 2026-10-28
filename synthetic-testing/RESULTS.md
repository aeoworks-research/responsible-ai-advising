# Synthetic testing results

The full findings and what they mean for advising are in the insight brief, [*Getting AI Right in Business Advising*](https://research.aeoworks.org/publications/responsible-ai-advising). This page summarizes the synthetic testing behind it.

## What we tested

- **124 simulated conversations:** 100 scored, plus 24 stress tests.
- **11 business profiles.**
- **One AI model family.**

## Head-to-head: consolidated prompt vs. first revision

Pilot and step 2, 28 conversations per version.

| Measure | Consolidated | First revision |
|---|---|---|
| Fields captured | 71% | 65% |
| Checkable values fully correct | 90% | 96% |
| Average exchanges | 32 | 21 |

The AI grader preferred the consolidated prompt's advisor handoff in 78% of 80 blind comparisons.

## Guardrails

- No scored conversation gave advice, steered to products, promised results or skipped consent.
- All 24 stress conversations passed every check.
- Missed warning signs: 1 of 28 (consolidated) vs. 5 of 28 (first revision).

## Limits

- Simulated owners only.
- The grader's preferences have not yet been confirmed by human reviewers.
- A single model family.
- These tests can't show whether real entrepreneurs would finish or trust the conversation, or whether advisors save time.
