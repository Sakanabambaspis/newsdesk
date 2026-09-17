Type: grilling
Status: resolved
Blocked by:

## Question

With the Telegram in-chat copy dropped, the Cloudflare host holds the only
copy of every episode. Decide the retention policy: how much history to keep
(days/episodes, and total size at ~5MB/day), who prunes (the publish step
during each daily run), what the RSS feed lists (everything retained vs last
N), and whether pruned enclosure URLs may 404 (apps tolerate vanished old
episodes; GUIDs must never be reused). Any Cloudflare Pages file-count/size
ceilings from the hosting research constrain the answer.

## Answer

**Keep everything, forever — decided 2026-09-18.** No prune code exists in
v1; the publish step only appends (new MP3 + regenerated feed.xml listing
all episodes).

- Rationale: the host cap that motivated this question was GitHub Pages'
  1GB site limit; Cloudflare Pages has no total-size cap that bites at our
  scale (25MiB/file is the only hard limit; ~1.8GB/year, unbilled). Pruning
  would be the only new logic — so it's the part we skip.
- Feed lists all episodes; GUIDs are never reused; enclosure URLs are
  permanent (aligns with ticket 02's finding that apps require stable
  URLs).
- Accepted consequences: unbounded-but-tiny growth; a future feed-URL
  holder can scroll the full backlog (moot under ticket 07's anonymous
  metadata).
- Later-if-annoying: a prune step in the publish plugin (delete older than
  N days, cap the feed listing) can be added without redesign.
