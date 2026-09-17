# 01 — Digest verdict pass + MaterialPack (ADR 0001 B1)

**What to build:** The daily digest becomes the auditable selector ADR 0001
promised. After a collection pass, `newsdesk digest-daily` classifies every
candidate item as `technical | hype | tangential` with a stored, inspectable
reason (its own LLM sub-task, distinct from the prose call). Hype and
tangential items stay visible in the digest dict — marked, never silently
dropped — and the curated `MaterialPack` (the bounded context the
script-writer will consume) is assembled as a first-class artifact with a
mechanical, testable truncation policy. With no LLM configured the digest
still builds, clearly labeled, with the verdict pass skipped rather than
fabricated.

Decided by: wayfinder `morning-audio-cloud` tickets 08/09 and ADR 0001
(the digest selects; the LLM only renders; no opaque filtering).

**Blocked by:** None — can start immediately.

**Status:** done

- [x] With an LLM configured, each candidate item carries `{verdict, reason}`
      in the digest output and the immutable log; no filtering happens without
      a stored reason.
- [x] Keyless/extractive run still completes, labeled as skipping verdicts;
      a "short day" (few candidates) degrades gracefully.
- [x] Verdict output is grounding-guarded: only ids actually provided to the
      model can receive a verdict; unparseable model output falls back safely.
- [x] `MaterialPack` is assembled with a mechanical truncation policy and its
      composition is logged (counts, method) so a script can be traced to
      exactly what the writer saw.
- [x] `newsdesk digest-daily --json` exposes verdicts, reasons, and the pack
      summary for inspection.
- [x] Tests cover the verdict pass, the grounding guard, truncation, and the
      no-LLM fallback.

## Comments

Implemented 2026-09-18 (frontier worker). 173 tests pass.

- **Verdict pass:** new `classify_verdicts` on the LLM adapters (own prompt,
  own sub-task per ADR 0001), with the contract enforced in one place: only
  provided ids, known verdict values, non-empty reasons survive. The digest
  re-grounds ids as the editorial enforcement point. Failures
  (`llm_not_configured`, unparseable, LLM error, no items) become
  `verdict_method: "skipped:<reason>"` — the run never fails.
- **Digest surface:** item cards carry `verdict`/`verdict_reason`;
  top-level `verdict_method`; `material_pack` summary (counts + truncation
  stats). The `daily_digest_built` log entry stores per-item verdicts with
  reasons plus counts, so filtering stays auditable even if console output
  is lost. Markdown output gained a verdict-status line (visible in the
  skipped path too).
- **MaterialPack (`pipeline/material.py`, policy `mechanical-v1`):** verdict
  filter (technical only) → digest rank order → 30-item cap → whole-pack
  24k-char budget taken as a rank-order prefix. Without verdicts the pack
  falls back to relevance order, labeled `verdict_filter: "skipped"`.
- **Note for ticket 02 (script-writer):** pack item text is currently the
  digest snippet (~400 chars). If the writer needs fuller source text for the
  400–450-word deep dive, re-query items by id inside the writer — do not
  widen the pack's injection surface.

