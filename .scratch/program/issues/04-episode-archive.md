# 04 — Episode archive: the morning's script lands in the repo, version-aware

Type: task
Status: claimed
Claimed: 2026-09-22 — ZCode session (morning run archiving the episode script)
Blocked by: —

## Why now / routing note

Direct user instruction (2026-09-22): "Grab the content of the podcast every
morning … by grabbing the pre-TTS script … Store the artifact where suitable.
Make the content version aware." This precedes the effort-01 frontier by
explicit user order; effort 01 stays open and unclaimed for its own window.

## The decision: script, not the feed

The feed route is wrong by construction: the feed is anonymous on purpose and
carries audio only — "no digest item lists or show notes anywhere in the XML"
(`newsdesk/morning/feed.py`); content via subscription would mean audio
transcription (lossy, slow, model-dependent). The pre-TTS script is the exact
content. But nothing on GitHub holds it today: the sidecar
(`<date>-script.json`) is written under `NEWSDESK_HOME` on the CI runner
(`newsdesk/morning/script.py:sidecar_path`) and evaporates with the runner;
the sidecar is "never published" by decision (morning-audio ticket 07 — that
decision is about the public site and stays respected; the private repo is
not the site).

So: the morning run archives the script into the repo and CI commits it back.
"Every morning" rides the existing 07:07 HKT cron — no second scheduler.

## What ships

- `newsdesk/morning/archive.py` — bundle writer: copies the sidecar verbatim
  to `<archive_dir>/<station>/<date>/script.json` and writes `meta.json`
  (version-aware): workflow name@version that ran, code commit, digest/script
  render facts, episode GUID, relative episode path. Token-free by
  construction (the token appears in exactly one place: the feed).
- `newsdesk morning --archive <dir>` — opt-in hook, local opt-in, CI always.
- `morning.yml` — passes `--archive briefing`; a commit-and-push step lands
  the bundle on main (rebase-retry: matrix legs race, paths are disjoint);
  `permissions: contents: write`.
- Contract + unit tests; `docs/morning-actions.md` section.

## Deliverable

`briefing/<station>/<date>/{script.json,meta.json}` in the repo, one commit
per station leg per morning.

## Gate (user-judged)

The user accepts the archive layout and the version metadata as the answer
to "which version owns the podcast".
