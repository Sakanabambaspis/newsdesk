# Phone notification routes for the daily MP3 digest

Question: the GitHub Action publishes an MP3 at a stable URL each morning. How does the
phone get a notification the user can tap to listen immediately — with the Action as the
only backend? Compared: podcast RSS, ntfy.sh, PWA + Web Push, Telegram bot, email.
Date: 2026-09-18. Method: primary docs of each service; every load-bearing claim cited.

Applies to ALL routes: GitHub Actions cron is not punctual. "The `schedule` event can be
delayed during periods of high loads of GitHub Actions workflow runs"; high load includes
"the start of every hour", and in a public repo scheduled workflows are "automatically
disabled when no repository activity has occurred in 60 days"
([Actions docs](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)).
Schedule off the hour; a notification arriving 10-30 min late is normal, regardless of route.

## (a) Private podcast RSS feed in a podcast app

Notification behavior per app:

- **Apple Podcasts** — following a show means episodes "automatically download and be
  notified of all new episodes"; "by default, followers are notified of all new episodes",
  though "if a follower is not engaged with a show, automatic downloads may be paused"
  ([Apple Podcasts for Creators](https://podcasters.apple.com/support/3298-follow-on-apple-podcasts)).
  Per-show/global notification toggles live under Podcasts → profile → Notifications
  ([iPhone User Guide](https://support.apple.com/guide/iphone/follow-your-favorite-podcasts-iph92ddcc196/ios)).
  Because Apple auto-downloads, the episode is already on-device when you tap — the best
  "plays instantly" result of any route. **Uncertain:** whether a feed that is not in
  Apple's catalog can be followed by URL (required for a private feed) — verify on-device.
- **Pocket Casts** — global toggle Profile → Settings → Notifications → New Episodes, plus
  a per-podcast bell; on Android the notification itself offers Play/Download actions;
  iOS notifications are "controlled by your device's OS"
  ([Pocket Casts support](https://support.pocketcasts.com/knowledge-base/episode-notifications-2/)).
  Refresh is server-synced; iOS background behavior governed by Background App Refresh
  ([support](https://support.pocketcasts.com/knowledge-base/background-app-refresh/)).
- **Overcast** — has per-podcast notification settings in-app, but there is **no reachable
  public documentation** (overcast.fm/faq returns nothing without the app); reliability
  claims here would be anecdotal. Flagged uncertain.
- **AntennaPod** — client-side polling: "by default, all podcasts are refreshed with a
  12-hour interval"; interval configurable under Settings → Downloads → Refresh podcasts
  ([AntennaPod docs](https://antennapod.org/documentation/automation/refreshing-podcasts)).
  12h default is the wrong cadence for a daily morning digest; needs a shorter interval,
  and even then the poll can land hours off your publish time.

Tap → listen: tap notification → episode screen → play (2 taps). Setup: one-time feed
publication (separate doc) + one "add by URL" in the app. Maintenance: near zero — the
apps are consumer products; only the feed URL must stay stable. Cost: free.

## (b) ntfy.sh

The Action does a plain `curl -d` POST to `https://ntfy.sh/<topic>`
([publish docs](https://docs.ntfy.sh/publish/)). The phone runs the ntfy app, subscribes
to the topic; topics are created on first publish/subscribe.

- Notification can carry a URL: the `X-Click` header "define[s] which URL to open when a
  notification is clicked", plus up to three action buttons including a `view` button that
  opens a URL ([publish docs](https://docs.ntfy.sh/publish/)). So: tap → browser opens the
  MP3 → plays. Two taps.
- Audio attachment? Possible but not for a digest: on ntfy.sh "the max attachment size is
  15 MB (with 100 MB total per visitor)" and "attachments expire after 3 hours"; externally
  hosted URL attachments (`X-Attach`) bypass those limits, they just decorate the
  notification with a downloadable link ([publish docs](https://docs.ntfy.sh/publish/)).
  Stream the MP3 via Click/Attach URL instead; whether ntfy's app plays audio in-app is
  unverified — assume browser handoff.
- Free-tier limits ([publish docs, Limitations](https://docs.ntfy.sh/publish/)): burst of
  60 requests replenished at one per 5 s; "on ntfy.sh, the daily message limit is 250";
  30 concurrent subscriptions. One message per morning is ~4 orders of magnitude below the
  cap. Message cache default is 12 h ([config docs](https://docs.ntfy.sh/config/)), so a
  phone offline overnight still receives the message when it reconnects within 12 h.
- iOS delivery: ntfy.sh itself delivers instantly over APNS. The quirk bites only
  self-hosted servers: without `upstream-base-url: "https://ntfy.sh"` relaying, "delivery
  can take hours" ([config docs](https://docs.ntfy.sh/config/)). Since our constraint pins
  us to ntfy.sh anyway, this is a non-issue.
- Privacy/access control: ntfy.sh "is configured" open — "everyone can read and write to
  any topic" ([config docs](https://docs.ntfy.sh/config/)); the docs say "because there is
  no sign-up, the topic is essentially a password, so pick something that's not easily
  guessable" ([publish docs](https://docs.ntfy.sh/publish/)). Use a long random topic name;
  anyone who guesses it can read (and spoof) your digest.
- Maintenance risk: ntfy.sh is free infrastructure run effectively by one developer;
  there are paid tiers (pricing page is JS-rendered, unverified). If it disappears, the
  Action side is a single curl — migrating the "poke" to Telegram/email is a one-line
  change. Cost: free.

## (c) PWA + Web Push

Sending from the Action: yes, no server needed at send time.
[pywebpush](https://pypi.org/project/pywebpush/) encrypts and POSTs a push given only a
stored `PushSubscription` JSON (`endpoint` + `keys.p256dh`/`keys.auth`) and VAPID keys
(generated once with `py_vapid`). Its own README notes the library is "maintained by a
single person" — a dependency-risk datum.

The catch is registration and storage: the PWA on the phone must POST its subscription
JSON somewhere writable, and something must serve that JSON back to the Action. Realistic
zero/low-cost options:

- **Cloudflare Worker + KV** — free Workers plan: 100,000 requests/day; KV free: 1,000
  writes/day, 100,000 reads/day, 1 GB stored
  ([Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/)).
  A 30-line Worker (PUT subscription, GET subscription) covers it permanently; best option
  if this route is chosen.
- **Supabase** — free plan: 500 MB DB, but "free projects are paused after 1 week of
  inactivity" ([Supabase pricing](https://supabase.com/pricing)); whether a daily read by
  the Action counts as activity is undocumented — flag. Riskier than Worker+KV.
- **GitHub gist via API** — create/update gists over REST with a token holding the `gist`
  scope ([Gists REST docs](https://docs.github.com/en/rest/gists/gists)). Embedding that
  PAT in the PWA means shipping a credential that acts as *you*; and "secret" gists are
  unlisted rather than access-controlled (semantics not verified — GitHub's current docs
  page could not be located). Not recommended.
- Others (Vercel/Netlify functions, Firebase) have the same shape as Worker + KV.

Endpoint secrecy: the subscription's "endpoint ... is a unique capability URL: knowledge
of the endpoint is all that is necessary to send a message to your application. The
endpoint URL therefore needs to be kept secret"
([MDN Push API](https://developer.mozilla.org/en-US/docs/Web/API/Push_API)). The endpoint
is an opaque URL from the push service (reveals nothing about the user), but the stored
JSON is itself a bearer secret — the store must be access-controlled, not a public gist.

iOS reality: Web Push works only on iOS/iPadOS 16.4+, only for a web app "added to the
Home Screen" (manifest `display: standalone`), permission must be user-initiated; it uses
APNS internally, no Apple Developer membership needed
([WebKit blog](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)).
Tap → listen: tap notification → PWA opens → play (2 taps).

Effort, honestly: PWA player page + service worker + subscribe flow + storage endpoint +
`pywebpush` step in the Action ≈ 1-2 days of build, plus standing edge cases (subscription
endpoints can expire/rotate, requiring a re-subscribe path; per-browser permission decay).
Highest effort of the five routes; the payoff over ntfy is a first-party notification with
no third-party push app.

## (d) Telegram bot

Setup (one-time, ~10 min): create a bot with @BotFather — "each bot is given a unique
authentication token when it is created"
([Bot API](https://core.telegram.org/bots/api)); open `https://t.me/<bot_username>` ("each
bot has a link that opens a conversation with it in Telegram",
[bot features](https://core.telegram.org/bots/features)), send `/start`, then read the chat
id from `getUpdates` and store bot token + chat id as Action secrets. (A bot can only
message a chat it already knows; the initial `/start` is the standard handshake.)

- The Action then calls `sendMessage` with a link, or better `sendAudio`: "your audio must
  be in the .MP3 or .M4A format" and "bots can currently send audio files of up to 50 MB
  in size"; it renders in Telegram's music player
  ([Bot API](https://core.telegram.org/bots/api)). Upload the file from the job workspace
  (multipart) — a daily digest at podcast bitrate fits far under 50 MB. Sending a bare
  link instead is a one-parameter change if you'd rather not upload.
- Notification UX: bot messages arrive as ordinary Telegram messages with system
  notifications; tap → chat → inline play button streams immediately (progressive
  playback, no full download). 2 taps, arguably 1.5 — the play control is right in the
  message. Bonus: the chat becomes a permanent, searchable archive of past episodes.
- Reliability: "bots are able to message their users at no cost"
  ([Bot FAQ](https://core.telegram.org/bots/faq)); Telegram push is carrier-grade. The
  broadcast limits (30 msg/s) are irrelevant at 1 msg/day.
- Maintenance: Bot API is famously backward-compatible; risk is platform-level (Telegram
  blocked in some networks/countries; trusting Telegram with your digest audio). Cost: free.

## (e) Email

The pipeline emails a link (or the MP3 itself) each morning; the mail app's push provides
the notification. Tap → email → tap link → browser player: 3 taps, worst friction of the
five. Gmail personal accounts cap attachments at "25 MB"
([Gmail attachment limits](https://support.google.com/mail/answer/6584)) — a 64 kbps mono
MP3 digest (~15-30 MB/hour) usually fits; other providers' caps were not verified.
Reliability is high, but automated morning mail can trip spam filters — check the first
week. Note the repo's existing mail capability is **read-only IMAP** (`BODY.PEEK`, never
marks seen — README §Linked accounts); sending needs a new SMTP credential path, a small
but real addition. Maintenance: one SMTP app password. Cost: free.

## Comparison

| Route | One-time setup | Taps to play | Latency / reliability | Dependency risk | Cost |
|---|---|---|---|---|---|
| (a) Podcast RSS | feed hosting (other doc) + add-by-URL | 2 (Apple: on-device, instant) | app-controlled refresh; can lag hours; no push from publisher | lowest — consumer apps | free |
| (b) ntfy.sh | install app, subscribe topic; 1 curl in Action | 2 (via browser) | seconds via FCM/APNS; 12 h offline buffer | ntfy.sh longevity (one-dev free service) | free |
| (c) PWA + Web Push | PWA + SW + Worker/KV + pywebpush ≈ 1-2 days | 2 (in PWA) | seconds; subscription rotation edge cases | many small parts; pywebpush single-maintainer | free |
| (d) Telegram bot | BotFather + `/start` + chat id ≈ 10 min; 1 API call in Action | 2 (inline player, streams) | seconds; very reliable | Telegram availability/privacy stance | free |
| (e) Email | SMTP credential + compose step | 3 (via browser) | seconds-minutes; spam-filter risk | provider attachment caps; filters | free |

## Implications for newsdesk

Ranked for a solo user whose literal request is "wake up, tap the notification, listen":

1. **Telegram bot** — closest match to the request: `sendAudio` streams inline, 2 taps,
   ~10 min setup, one HTTPS call in the Action, 50 MB headroom, and the chat doubles as an
   episode archive. Main trade-offs: requires a Telegram account and trusting Telegram
   with digest content. A `NEWSDESK_TELEGRAM_BOT_TOKEN/CHAT_ID` notify step is a natural
   M4 "digests/alerts" feature.
2. **Combination: podcast feed as source of truth + ntfy as the poke** — arguably beats
   every single route. The feed (separate doc) is the durable, app-agnostic archive with
   native player UX and offline auto-download; ntfy's morning `curl` with `X-Click` set to
   the episode URL supplies the punctual push that podcast apps can't. Each half is
   independently replaceable (drop the poke, you still have the feed; the feed's hosting
   changes, the poke still works). Recommended if the user dislikes Telegram or wants the
   digest to outlive any one notification service.
3. **ntfy alone** — simplest of all: one curl, ample free limits, instant on iOS via
   ntfy.sh. Accept an unguessable-topic-name-as-password model, browser-not-app playback,
   and one-dev-service longevity risk.
4. **Email** — zero new backend, aligns with existing mail opinions, but 3 taps and spam
   risk make it the fallback, not the recommendation. Fine as a redundant belt-and-braces
   notification (a link costs nothing to also send).
5. **PWA + Web Push** — the only route with zero third-party push middleman and a
   first-party player, but 1-2 days of build plus standing edge cases to reach what
   ntfy/Telegram give for free. Only worth it if first-party control is the goal.

Uncertainties flagged inline: Apple Podcasts follow-by-URL for feeds outside its catalog;
Overcast has no reachable primary docs; whether the Action's daily Supabase read counts as
"activity" against pausing; secret-gist visibility semantics; ntfy in-app audio playback;
ntfy.sh paid-tier details (JS-rendered pricing page); other email providers' size caps.

Sources: [ntfy publish](https://docs.ntfy.sh/publish/),
[ntfy config](https://docs.ntfy.sh/config/),
[Telegram Bot API](https://core.telegram.org/bots/api),
[Telegram Bot FAQ](https://core.telegram.org/bots/faq),
[Telegram bot features](https://core.telegram.org/bots/features),
[pywebpush (PyPI)](https://pypi.org/project/pywebpush/),
[MDN Push API](https://developer.mozilla.org/en-US/docs/Web/API/Push_API),
[Cloudflare Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/),
[Supabase pricing](https://supabase.com/pricing),
[GitHub Gists REST](https://docs.github.com/en/rest/gists/gists),
[WebKit: Web Push on iOS](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/),
[Apple Podcasts for Creators](https://podcasters.apple.com/support/3298-follow-on-apple-podcasts),
[Apple iPhone User Guide (Podcasts)](https://support.apple.com/guide/iphone/follow-your-favorite-podcasts-iph92ddcc196/ios),
[Pocket Casts notifications](https://support.pocketcasts.com/knowledge-base/episode-notifications-2/),
[Pocket Casts background refresh](https://support.pocketcasts.com/knowledge-base/background-app-refresh/),
[AntennaPod refreshing podcasts](https://antennapod.org/documentation/automation/refreshing-podcasts),
[Gmail attachment limits](https://support.google.com/mail/answer/6584),
[GitHub Actions schedule event](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
