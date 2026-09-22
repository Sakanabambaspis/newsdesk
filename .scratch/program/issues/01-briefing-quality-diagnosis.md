# 01 — Briefing-quality diagnosis

Type: task (effort window — investigate, don't map)
Status: open
Blocked by: —

## What this effort is

A dedicated, evidence-first diagnosis of why the AI morning briefing still
falls short despite prior fixes (latest: `7381cf3` "speak the cluster
breadth"). This is not a wayfinder map: one method, one deliverable.

Method: pull the last ~7 episodes and reconstruct each decision chain —
published audio, script sidecar, digest, MaterialPack, stored rubric reasons,
verdicts, and workflow logs — against the source material actually collected.
Classify every concrete disappointment by pipeline stage: source material →
digest/verdicts → selection strategy → script contract/writer → TTS/render →
delivery. No fixes until the failure catalog is complete.

## Deliverable

- `docs/reviews/briefing-quality-findings-<date>.md` — failure catalog by
  stage, ranked causes with evidence, and the fix plan (catalog-data fixes
  via workflow versions vs. structural tickets).
- Fix tickets published to the tracker (workflow-modules map or a new effort
  directory, depending on where each cause sits).
- A written quality bar: what "good enough on news" means, for the gate.

## Gate (user-judged)

Top-ranked causes fixed and verified over real mornings; the user accepts the
briefing is good enough to feed more sources into.

## Kickoff (fresh session)

> Run a dedicated diagnosis of the morning briefing quality: evidence-first
> over the last ~7 episodes (scripts, sidecars, digests, rubric reasons, logs
> vs. source material), classify failures by pipeline stage, and produce a
> findings doc + ranked fix tickets. No fixes until the failure catalog is
> complete. Program context: `.scratch/program/map.md`.
