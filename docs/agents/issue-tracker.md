# Issue tracker: Local Markdown

Chosen 2026-09-18 as wayfinder's default: the repo's GitHub remote exists but
`gh` is not installed on this machine, so GitHub Issues is not operable yet.
If `gh` gets installed and authed, re-run `/setup-matt-pocock-skills` to
switch.

Issues and specs (you may know a spec as a PRD) for this repo live as markdown
files in `.scratch/`.

## Program level — read before planning anything

`.scratch/program/` is the repo's **single source of todo across efforts**
(one ticket per effort window under `.scratch/program/issues/`). `AGENTS.md`
at the repo root routes every planning session here; this section defines the
mechanics. Program tickets use status vocabulary `open | claimed | resolved`.

1. **Read** `.scratch/program/map.md` — it names the effort order, the
   user-judged gates between windows, and the effort index.
2. **Claim before you plan**: set the effort ticket `Status: claimed` and add
   a `Claimed: YYYY-MM-DD — <actor/session>` line *before* opening or
   creating any effort map. Sessions share this working tree, so a claim is
   visible to every other session immediately; if two sessions claim the
   same window, the first to write wins and the loser re-reads the map.
3. **Stale claims**: a claim idle for more than 7 days with no progress
   recorded may be taken over — note the takeover on the ticket, then
   re-claim with a fresh `Claimed:` line.
4. **Gates**: an effort ticket may only be marked `resolved` with (a) a
   pointer to the deliverable and (b) the user's explicit gate verdict
   recorded in its `## Answer`. A complete deliverable without a gate
   verdict stays `claimed`.
5. **No orphan efforts**: creating any new `.scratch/<effort>/` directory
   requires a program ticket first (claim or create one under
   `.scratch/program/issues/`). Findings docs, reviews, and research files
   are not efforts.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The spec is `.scratch/<feature-slug>/spec.md`
- Implementation issues are one file per ticket at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01` — never a single combined tickets file
- Triage state is recorded as a `Status:` line near the top of each issue file
- Comments and conversation history append to the bottom of the file under a `## Comments` heading

## When a skill says "publish to the issue tracker"

Create a new file under `.scratch/<feature-slug>/` (creating the directory if needed).

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path. The user will normally pass the path or the issue number directly.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a file with one **child** file per ticket.

- **Map**: `.scratch/<effort>/map.md` — the Notes / Decisions-so-far / Fog body.
- **Child ticket**: `.scratch/<effort>/issues/NN-<slug>.md`, numbered from `01`, with the question in the body. A `Type:` line records the ticket type (`research`/`prototype`/`grilling`/`task`); a `Status:` line records `claimed`/`resolved`.
- **Blocking**: a `Blocked by: NN, NN` line near the top. A ticket is unblocked when every file it lists is `resolved`.
- **Frontier**: scan `.scratch/<effort>/issues/` for files that are open, unblocked, and unclaimed; first by number wins.
- **Claim**: set `Status: claimed` and save before any work.
- **Resolve**: append the answer under an `## Answer` heading, set `Status: resolved`, then append a context pointer (gist + link) to the map's Decisions-so-far in `map.md`.
