# ADR 0001 — The daily digest is the selector for the morning briefing script

Date: 2026-09-16 · Status: accepted

## Context

The morning audio pipeline needs a stage that turns collected material into a
spoken script. Two designs were considered:

- **A:** the LLM agent reads raw scraped materials and writes the script in one
  hop (selection + prose in one call).
- **B:** the deterministic daily digest (`build_daily_digest`) selects and
  structures; the LLM only renders prose from a curated context pack.

The briefing's editorial contract is "novel engineering solutions to open
problems in selected areas, plus hot topics; no personality/CEO/funding hype."

## Decision

Design B, with a Signal/Hype verdict pass (B1): the digest's LLM call also
classifies each candidate item (`technical | hype | tangential`, with a stored
reason). Hype items are excluded from the script but stay in the digest dict.

## Consequences

- Selection stays deterministic and auditable (ranking scores, matched terms,
  logged digests); the model never decides what is in the briefing without a
  stored, inspectable reason — preserving the no-opaque-filtering posture of
  DESIGN.md §1.2.
- The writer's prompt-injection surface is bounded to the curated context pack
  (~30 vetted item texts), not the whole day's published words.
- The "clear context window" is a first-class artifact (`MaterialPack`) with a
  mechanical, testable truncation policy.
- Cost: two LLM sub-tasks (verdicts + prose) instead of one; acceptable at
  ~30 items/day. The no-LLM fallback skips verdicts and degrades to
  extractive prose without failing.
