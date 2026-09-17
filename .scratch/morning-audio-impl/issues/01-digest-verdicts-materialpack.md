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

**Status:** ready-for-agent

- [ ] With an LLM configured, each candidate item carries `{verdict, reason}`
      in the digest output and the immutable log; no filtering happens without
      a stored reason.
- [ ] Keyless/extractive run still completes, labeled as skipping verdicts;
      a "short day" (few candidates) degrades gracefully.
- [ ] Verdict output is grounding-guarded: only ids actually provided to the
      model can receive a verdict; unparseable model output falls back safely.
- [ ] `MaterialPack` is assembled with a mechanical truncation policy and its
      composition is logged (counts, method) so a script can be traced to
      exactly what the writer saw.
- [ ] `newsdesk digest-daily --json` exposes verdicts, reasons, and the pack
      summary for inspection.
- [ ] Tests cover the verdict pass, the grounding guard, truncation, and the
      no-LLM fallback.
