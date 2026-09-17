Type: grilling
Status: resolved
Blocked by: 02

## Question

What does "private" mean for the feed, concretely? Decide: is possession of
the tokenized feed URL the only access control (and is that acceptable)?
What leaks if the URL escapes — audio content, listening habits, interest
profile via feed metadata? Token placement (path segment vs query, per the
hosting research) and rotation story (new token/path without breaking
subscribed apps). Also: what the RSS metadata itself reveals.

## Answer

Decided 2026-09-18 — all three recommended options accepted:

- **Access control = possession of the tokenized URL, nothing else.** One
  128-bit random token as a path segment gates feed + audio. Accepted
  consequences: a leaked URL means the audio is out permanently; rotation is
  break-glass (new token → re-subscribe both phones); behavioral fetch data
  is visible only to Cloudflare (paths are TLS-encrypted).
- **Feed metadata: anonymous + generic.** Non-identifying feed title
  ("Morning Briefing"), date-only episode titles, pseudonymous
  itunes:author, alias address for Apple's required owner email, neutral
  artwork, and **no digest item lists or show notes in the XML** — a
  scraped feed reveals nothing; interest profile lives in the audio only.
- **One shared token now.** Per-device feeds (separate tokens, shared
  enclosures) are the documented escalation if revocation ever matters —
  cheap to add later because feeds are just generated files.
- **Recorded caveat:** choosing synced podcast apps shares the feed URL
  with their clouds (iCloud for Apple Podcasts, Pocket Casts sync);
  AntennaPod without gpodder sync stays purely local.
