Type: research
Status: resolved
Blocked by:

## Question

Which notification + tap-to-listen route works on both iPhone and Android
with zero always-on backend?

Candidates to compare: podcast app on a private feed (Apple Podcasts /
Overcast / Pocket Casts / AntennaPod notification + streaming behavior),
ntfy.sh (Action POSTs a topic; phone app pings; can it carry a playable
link/attachment?), PWA + Web Push sent by the Action itself (pywebpush/VAPID —
and where per-device subscriptions register with no backend), Telegram bot
(Bot API from the Action), plain email. Judge: one-time setup effort,
tap→listen friction, notification reliability/latency, dependency risk, cost.

Findings: `docs/research/phone-notification-routes.md`

## Answer

Ranked for "wake up → tap → listening", iPhone + Android, zero backend
(evidence in `docs/research/phone-notification-routes.md`, all cited):

1. **Telegram bot** — `sendAudio` (≤50MB) renders in the in-chat music player
   and streams progressively; ~2 taps; ~10 min setup (BotFather token + chat
   id); bots message users at no cost; the chat doubles as a browsable
   archive. Best literal tap-to-listen.
2. **Podcast feed (source of truth) + ntfy poke** — the recommended
   combination if Telegram is unwanted: each half is independently
   replaceable. ntfy `X-Click` opens the episode URL on tap; free tier is
   fine for 1 msg/day (250/day cap on ntfy.sh); iOS delivery is instant on
   ntfy.sh (the hours-late APNS quirk is self-hosted-only); **the topic name
   is "essentially a password"** per ntfy docs.
3. ntfy alone. 4. Email (3 taps; SMTP send is new surface — repo's mail
   capability is read-only IMAP). 5. PWA + Web Push (viable but highest
   effort: needs a writable subscription store, e.g. Cloudflare Worker+KV;
   iOS needs 16.4+ and home-screen install).

Podcast-app nuances that feed ticket 05: Apple Podcasts follows arbitrary
URLs and auto-downloads + notifies; Pocket Casts has per-podcast notify
toggles; **AntennaPod's default refresh is client-side 12h polling — wrong
cadence for a morning digest** unless refresh is reconfigured. Overcast has
no reachable public docs (uncertain). ntfy.sh attachments cap at 15MB →
stream via URL, don't attach.
