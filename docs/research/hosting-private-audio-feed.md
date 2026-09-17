# Hosting a private podcast feed (GitHub Action → phone)

Research for the newsdesk daily-MP3 digest pipeline. Sources fetched 2026-09-18.
Method: primary sources (docs.github.com, developers.cloudflare.com, Apple support/docs,
rssboard.org, app support pages); every load-bearing claim cited. Uncertainty flagged inline.

## 1. GitHub Pages on a private repository: plan requirement

**Pages on a private repo requires a paid plan. Free plan = public repos only.**

> "GitHub Pages is available in public repositories with GitHub Free and GitHub Free for
> organizations, and in public and private repositories with GitHub Pro, GitHub Team,
> GitHub Enterprise Cloud, and GitHub Enterprise Server."
> — https://docs.github.com/en/pages/quickstart ("Who can use this feature?")

So for a private repo on the free plan, Pages is not available at all; you'd need GitHub Pro
(paid; current price on https://github.com/pricing — the pricing page did not render for this
fetch, so exact dollar figure unverified here).

## 2. GitHub Pages limits, and is a growing MP3 archive within them?

Current limits page (https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits):

- **Bandwidth**: "a soft bandwidth limit of 100 GB per month"
- **Published site size**: "may be no larger than 1 GB"
- **Source repo size**: "a recommended limit of 1 GB"
- **Builds**: "a soft limit of 10 builds per hour" — explicitly does not apply to a custom
  GitHub Actions workflow
- **Deployments** "will timeout if they take longer than 10 minutes"
- Suggested remedy for exceeding limits: "putting a third-party content distribution network
  (CDN) in front of your site"

**The 2 GB accumulation does NOT fit.** A 5 MB/day MP3 reaches the 1 GB published-site cap in
~200 days (~60 days at 10 MB/day). Bandwidth (one listener) and build frequency are non-issues
for this use case; the 1 GB site ceiling is the binding constraint, so GitHub Pages would force
a sliding window (keep only the last N episodes). No explicit "no large media/audio" prohibition
was found on the current limits page — the old "not a free web-hosting service" wording lives in
GitHub's Acceptable Use Policies, which I did not re-verify on this pass (low risk for a
personal, non-commercial digest; flagged).

## 3. Free alternatives deployable from a GitHub Action

The source repo stays private in all options below.

### (a) Separate PUBLIC repo holding feed + audio, token in path

- **Mechanics**: the default `GITHUB_TOKEN` cannot reach another repo — it "is scoped to the
  current repository" (https://github.com/actions/checkout README) and "events triggered by the
  GITHUB_TOKEN will not create a new workflow run"
  (https://docs.github.com/en/actions/using-workflows/triggering-a-workflow). So: build in the
  private repo, then `actions/checkout` the public repo with a fine-grained PAT
  (contents:write there only), copy in feed + MP3s, `git push`; Pages publishes free from the
  public repo. **Works with a private source repo?** Yes, via PAT.
- **What leaks**: with a ≥128-bit random token path, feed/episode URLs are unguessable — but the
  repo itself is public: anyone browsing it (the repo name links to your profile) reads the feed
  XML — the news titles/summaries you generated — and every MP3 with commit dates. URL-token
  privacy holds only against people who never find the repo. The audio is capability-secured
  (acceptable); the feed metadata — the most revealing part of a news digest — is fully public.

### (b) Cloudflare Pages, direct upload via wrangler from Actions — recommended

- **Mechanics** (official CI recipe,
  https://developers.cloudflare.com/pages/how-to/use-direct-upload-with-continuous-integration/):
  create an API token with permission "Account, Cloudflare Pages and Edit"; store
  `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` as GitHub repo secrets; deploy step is
  `cloudflare/wrangler-action@v3` with `command: pages deploy <dir> --project-name=<name>`.
  The private source repo never touches GitHub Pages; Cloudflare gives a free
  `<project>.pages.dev` subdomain over HTTPS.
- **Free-tier limits** (https://developers.cloudflare.com/pages/platform/limits/): 500
  builds/month (free), 100 custom domains, **20,000 files per deployment, 25 MiB max file
  size** (our 2–10 MB MP3s fit). Bandwidth: the current Workers-platform pricing page states
  "Requests to static assets are free and unlimited" and "There are no additional charges for
  data transfer (egress) or throughput (bandwidth)"
  (https://developers.cloudflare.com/workers/platform/pricing/, footnotes on static assets).
  I could not re-find a standalone "Pages bandwidth: unlimited" page (docs were consolidated
  into Workers docs; older Pages FAQ now 404s) — but no bandwidth cap appears anywhere in the
  current Pages limits doc. Verdict: single-user audio streaming is comfortably within the
  free tier.
- **Token-gating**: yes — pure static file server; put the secret in the **path**
  (see finding 4). Works fine with a private source repo. Needs a one-time free Cloudflare
  account; thereafter zero-maintenance.

### (c) Netlify free tier — effectively ruled out

Netlify replaced its fixed free allowance with a credit model; the pricing page lists the Free
plan as "Credits: 300 credit limit" with "20 credits per GB" of bandwidth, "2 credits per 10k
requests", and "15 credits" per production deploy
(https://www.netlify.com/pricing/, as rendered 2026-09-18). Daily production deploys alone cost
~30 × 15 = 450 credits/month — over the 300-credit cap before any bandwidth. Flag: pricing model
changed in 2025 and may shift again; but on today's terms a daily-podcast pipeline does not fit
free.

### (d) Other sane options

- **Cloudflare R2 public bucket**: free tier "10 GB-month / month" storage, 1M Class A / 10M
  Class B requests/month, egress "Free" including via `r2.dev`
  (https://developers.cloudflare.com/r2/pricing/). Catch: "Public access through `r2.dev`
  subdomains is rate-limited and should only be used for development purposes"; production use
  requires a custom domain (https://developers.cloudflare.com/r2/buckets/public-buckets/). Only
  sane if you already own a domain (domain costs money → fails free-only).
- **Podcast-specific free hosts** (Spotify for Creators, Buzzsprout free tier, etc.): they host
  and generate the feed for you, but there is no Action-deployable upload path — they assume
  you upload through their app/API. Out of scope for a GitHub-Action pipeline.

## 4. Token-in-URL and phone podcast apps

**Path token vs query parameter: use a path segment.**

- Decisive practical reason: static hosts (GitHub Pages, Cloudflare Pages, R2) resolve files by
  path and ignore query strings entirely — `?token=...` authenticates nothing, the file is
  served regardless. The gate must live in the path.
- App-side query-string handling is also historically unreliable: Blubrry PowerPress shipped a
  change to "Allow query strings in media from trusted hosts"
  (https://wordpress.org/plugins/powerpress/ changelog), evidence that podcast tooling has
  stripped or mishandled enclosure query strings. Industry private-feed tokens are appended to
  feed *and* enclosure URLs (ART19: tokens are "appended to the end of the feed and enclosure
  URLs", https://art19.zendesk.com/hc/en-us/articles/4403934590477); Patreon's private feeds
  carry tokens in the URL and Pocket Casts' own docs show a Patreon example with `?auth=...`
  (https://support.pocketcasts.com/knowledge-base/private-or-members-only-feeds/) — so major
  apps do generally pass query strings through today. Uncertainty flag: I found no Apple first-
  party statement guaranteeing query strings survive on enclosure URLs; the path-token design
  sidesteps the question entirely.

**Apple Podcasts (iOS)**

- **Add arbitrary feed by URL: yes.** "Add a Show by URL" under Library → (three-dot/Edit) →
  paste feed URL → Follow. Feature-level verification is from multiple independent secondary
  walkthroughs (e.g. Transistor and ASSH guides surfaced in search) because Apple's iPhone User
  Guide pages truncated on fetch; the option has existed since iOS 15. Flag: could not cite an
  Apple page verbatim.
- **New-episode notifications: yes.** "…follow it to add it to your library so you can get
  notified about new episodes"; per-show: "Settings > Notifications, tap Podcasts, turn on
  Notifications, then tap Followed Shows"
  (https://support.apple.com/guide/iphone/follow-your-favorite-podcasts-iph92ddcc196/ios).
  Default state of the per-show toggle not stated by Apple (flag).
- **Streaming vs download: auto-downloads by default, streaming on tap.** "Podcasts you follow
  are automatically downloaded to your Apple device" and played episodes are auto-deleted;
  auto-downloads pause for shows you haven't played in a while
  (https://support.apple.com/guide/iphone/change-download-settings-iph2ae70a294/ios). Tapping an
  episode streams immediately; downloads need Wi-Fi/cellular permission per system settings.

**Pocket Casts**

- **Arbitrary URL feeds: yes, first-party documented.** "Copy a private RSS feed URL and paste
  it into the search bar on the Podcasts or Discover screen in Pocket Casts. Subscribe to the
  podcast that appears"; private shows can be marked "Private" so they "won't appear in search"
  (https://support.pocketcasts.com/knowledge-base/private-or-members-only-feeds/).
- **Notifications: OFF by default for private/RSS-added shows.** Pocket Casts' earlier support
  article "Private shows" (support.pocketcasts.com/article/1784-private-shows, verified via
  search-engine extract; page since restructured — medium confidence) states notifications are
  automatically off for private shows and must be enabled per show. One-time toggle, then
  push notifications work.
- **Streaming/download**: tap plays (streams); auto-download respects per-podcast settings;
  private shows are excluded from Up Next auto-play (1784 article). Flag: exact defaults not
  first-party documented.

**Overcast**

- **Arbitrary URL feeds: yes** — search screen accepts a pasted RSS URL (documented by
  secondary guides; Overcast publishes no user-facing docs I could verify — overcast.fm/faq
  404s. Flag: secondary-sourced).
- **Notifications**: per-podcast push toggle exists; **default state unverified**.
- **Streaming vs download**: streams on tap by default, per-podcast auto-download optional.
  Flag: unverified, product-knowledge level.

**AntennaPod (Android)**

- **Arbitrary URL feeds: yes, first-party documented**: "+ Add Podcast … add a podcast with its
  RSS address"; also accepts RSS/Atom URLs and `pcast://`/`itpc://` schemes
  (https://antennapod.org/documentation/getting-started/subscribe).
- **Notifications**: polling-based local notifications (no server push); works, fires on refresh
  when new episodes land. Flag: stated behavior inferred from architecture (open-source client);
  docs page not directly quoted.
- **Streaming vs download: streaming is the explicit default mental model**: "You can later
  directly play the episode without having to download"; auto-download is an opt-in per podcast —
  once enabled "all of its future new episodes are automatically downloaded and added to the
  queue" (https://antennapod.org/documentation/getting-started).

## 5. Minimum RSS feed shape for podcast apps

**RSS 2.0 core** (https://www.rssboard.org/rss-specification):

- Channel: required `<title>`, `<link>`, `<description>`.
- Item: "at least one of title or description must be present".
- `<enclosure>`: "three required attributes — url … length says how big it is in bytes … type
  says what its type is, a standard MIME type"; "The url must be an http url." Use
  `type="audio/mpeg"`, `length` = exact byte size.
- `<guid>`: string, `isPermaLink` defaults true; set `isPermaLink="false"` for opaque GUIDs.
- `<pubDate>`: RFC 822 format ("Wed, 6 Jul 2014 13:00:00 -0700").

**Apple's requirements** (https://podcasters.apple.com/support/829-validate-your-podcast and
https://podcasters.apple.com/support/823-podcast-requirements):

- XML declaration + `<rss version="2.0">` declaring the itunes (`podcast-1.0.dtd`) and content
  namespaces.
- Channel: `<title>`, non-empty `<description>`, `<language>` (ISO code),
  `<itunes:explicit>` "with an either true or false value", at least one `<itunes:category>`
  (correct casing, `&` escaped as `&amp;`).
- Item: "a valid `<enclosure>` tag, along with its three required components (URL, length, and
  type)"; "All episodes must contain a globally unique identifier (GUID), which never changes";
  "Apple Podcasts will ignore duplicate `<enclosure>` URLs"; dates per RFC 2822; "Use only ASCII
  filenames and URLs that include a-z, A-Z, or 0-9"; feeds "must be publicly addressable (not
  password-protected)".
- Servers "must be enabled for HTTP HEAD requests and byte-range requests" (streaming/seeking
  depends on range support — GitHub Pages, Cloudflare Pages and R2 all support HEAD/ranges).
- Artwork: show cover "1400 x 1400 to 3000 x 3000 pixels", PNG or JPG, no transparency
  (https://podcasters.apple.com/support/5514-show-cover-template). Strictly enforced only for
  Apple catalog submission; a private feed still needs `<itunes:image>` for decent app display.
- Commonly expected extras for good rendering: `<itunes:author>`, `<itunes:owner><itunes:email>`
  (ownership verification — only matters if you ever submit to Apple's catalog),
  `<itunes:type>episodic`, per-item `<itunes:title>`/`<itunes:summary>`/`<itunes:duration>`.

**Pitfalls with private/tokenized feeds**

- **Feed URL is a permanent secret**: "changing your feed URL will break the subscription —
  you'd need to unsubscribe and re-add the new URL" (Pocket Casts support, private-shows
  article). The feed token can never be rotated casually.
- **Enclosure URLs must stay valid forever** once published (apps re-verify/re-download old
  episodes); don't regenerate paths or expire episodes unless you accept missing-file errors.
- **`length` must match actual byte size** — mismatches corrupt progress/resume in some apps
  (practitioner-reported; not first-party documented — flag).
- HTTPS everywhere (Apple: feeds "must be publicly addressable"; iOS blocks plain HTTP).
- No redirects through HTTP; keep byte-range support through any redirect.
- RFC 822 `pubDate` (naive ISO-8601 breaks ordering/dedup in some apps); stable GUIDs — a new
  GUID for the same episode duplicates it.

## Implications for newsdesk

Given (i) free-only, (ii) unguessable-but-not-encrypted is acceptable, (iii) zero-maintenance:

1. **Primary recommendation: Cloudflare Pages via wrangler direct upload from the Action.**
   Free forever at this scale (500 builds/mo ≫ 30 needed; 25 MiB file limit ≫ 10 MB MP3s; static
   asset requests free/unlimited), no source-repo exposure at all (nothing public on GitHub),
   no PAT to babysit (one Cloudflare API token scoped to Pages Edit), HTTPS `*.pages.dev` host
   that Apple Podcasts/Pocket Casts/AntennaPod all accept. Design: deploy into
   `feed/<TOKEN>/feed.xml` and `feed/<TOKEN>/<date>.mp3` with one shared 128-bit random token
   (stored as a GitHub secret, generated once, never rotated — Pocket Casts warning above), so
   the feed and all enclosures share the same capability secret. Keep MP3s under 25 MiB; prune
   nothing (R2/Pages has no 1 GB site cap like GitHub Pages).
2. **Fallback if avoiding a Cloudflare account: separate public repo + GitHub Pages with the
   same path-token scheme.** Cheapest mechanically (git push from the Action), but a PAT is
   required, and the tradeoff is real: the repo is publicly browsable, so episode dates, file
   names and — worse for a news digest — the generated feed text (what news you consume) are
   public to anyone who finds the repo. Acceptable only if the user treats feed *content* as
   non-sensitive and the audio link as the only secret.
3. **Cloudflare R2** is the right shape if the user ever owns a domain (custom domain is required
   for production; `r2.dev` is explicitly dev-only), but fails free-only otherwise.
4. **Netlify is out** on today's credit-based free plan (daily deploys alone exceed the monthly
   credits).
5. **Phone side**: if the user is on iPhone, Apple Podcasts is the zero-config end: Add a Show by
   URL → notifications for followed shows + auto-download by default, stream-on-tap. On Pocket
   Casts the feed works but notifications default OFF for RSS-added shows (one-time toggle). Use
   path tokens (not query tokens) for host- and app-proof URLs.
6. **Privacy posture to state to the user**: the feed URL is a bearer capability — unguessable
   (128-bit), served over HTTPS, but not access-controlled and visible to any CDN logs; anyone
   who obtains the URL can download all episodes. Feed metadata on option 1 is as private as the
   URL; on option 2 it is public.

### Uncertainty summary

- GitHub Pro price not re-verified (pricing page did not render).
- GitHub Pages "acceptable use" re: media not re-verified against current policy text.
- Cloudflare "unlimited bandwidth" verified via the consolidated Workers pricing page, not a
  standalone Pages pricing page (Pages docs mid-migration).
- Netlify numbers from a single fetch of netlify.com/pricing (credit model, changed 2025).
- Apple "Add a Show by URL" and Overcast behaviors confirmed via secondary sources (Apple/Overcast
  first-party pages truncated or absent); Pocket Casts notifications-off default is from its
  (restructured) 1784 article via search extract.
- Enclosure `length`-mismatch and query-string stripping behaviors are practitioner-reported,
  not first-party documented.
