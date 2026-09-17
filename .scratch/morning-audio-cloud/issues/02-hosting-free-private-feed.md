Type: research
Status: resolved
Blocked by:

## Question

Where do the daily MP3 and its RSS podcast feed live, given the free-only
budget and a private source repo?

Sub-questions: Is GitHub Pages on a private repo paywalled (current policy)?
Pages size/bandwidth limits vs an audio archive growing ~5MB/day. Free
alternatives deployable from an Action: a separate public "broadcast" repo
holding only generated feed+audio behind unguessable token paths (what
leaks?), Cloudflare Pages / Netlify direct upload from Actions, others. Token
placement: path segment vs query parameter — which survives podcast apps?
Minimum RSS shape (enclosure/guid/pubDate/itunes tags) for apps to behave.

Findings: `docs/research/hosting-private-audio-feed.md`

## Answer

Ranked free routes (evidence in `docs/research/hosting-private-audio-feed.md`,
270 lines, cited to primary docs):

1. **Cloudflare Pages** (recommended) — free tier makes static-asset requests
   free and unlimited with no egress charges; 25MiB/file fits briefings;
   deploy from the private repo via `wrangler-action@v3` + one `Pages:Edit`
   API token; public `*.pages.dev` HTTPS site, gateable by a path token.
2. **Separate public repo** — audio URLs can be unguessable, but the repo
   itself is publicly browsable, so the *feed metadata* (what news you
   consume) leaks. Acceptable only if metadata is judged non-sensitive.
3. R2 — 10GB free, zero egress, but `r2.dev` is rate-limited
   "development purposes" only without a paid domain.

Ruled out: GitHub Pages (private-repo Pages needs a paid plan — and its 1GB
site cap would force episode pruning after ~200 days at 5MB/day anyway);
Netlify (new credit plan: daily production deploys alone exceed 300
credits/month).

Feed mechanics that feed tickets 05/07: token must be a **path segment**
(static hosts ignore query strings; some tooling strips them); the feed URL
is a **permanent secret** (Pocket Casts: changing it breaks subscriptions)
and enclosure URLs must live forever; GUID never changes; Apple requires
artwork 1400–3000px, HEAD + byte-range support. App behavior: Apple Podcasts
add-by-URL, notifies + auto-downloads by default; Pocket Casts notifications
default OFF for private shows (one-time toggle); AntennaPod streams by
default, auto-download opt-in; Overcast docs thin.
