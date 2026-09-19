# Placeholder site for the Cloudflare Pages project

The pipeline deploys over this — these files exist only so the project
isn't empty before the first `newsdesk morning` run.

**Upload all four files** (`index.html`, `style.css`, `favicon.svg`,
`404.html`) via the Cloudflare dashboard: Workers & Pages →
`morning-briefing` → Create deployment → drag the files in.

`404.html` is the important one: without it, Cloudflare Pages treats the
site as a single-page app (because of the root `index.html`) and answers
every unknown path with the index page at HTTP 200. The publisher's
archive guard reads that as a corrupt `episodes.json` and refuses to
deploy. With a `404.html` present, unknown paths return a real 404 and
the first publish proceeds cleanly.

Once the pipeline deploys for real, these files are replaced by the
generated site (feed, episodes, artwork under the token path).
