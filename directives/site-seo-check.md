# Directive: weekly site and SEO check for davidjforer.com

Goal: catch regressions and surface opportunities weekly, with deltas against the
previous snapshot, in one short report David can act on in under 10 minutes.

Inputs: state\snapshots\<date>\{gsc,ga4,psi,crawl,status}.json (produced by
execution\run_all.py), the previous snapshot folder, any files David dropped into
inbox\ (Labrika or Ahrefs exports, notes, docs).

Tools: execution\run_all.py (deterministic, one command). Never scrape GSC or GA4
through a browser, the API scripts are the only data path.

Outputs: reports\YYYY-MM-DD.md plus a short summary for David.

Edge cases:
- Missing credentials: gsc/ga4 report "skipped". Report what ran, list the setup
  step once, never fail the run.
- PSI 429: keyless quota exhausted, note it, continue.
- First run of a month: additionally mine GSC queries for content opportunities
  and append them to the Blog Engine backlog as queued supporting articles.
- A source erroring twice in a row is a fix-the-tool signal (self-annealing loop):
  fix the script, test, update this directive.

Update 2026-09-22: credentials landed, all four sources live. gsc.json now also
carries `page_queries`, paired page and query rows from a two-dimension
searchAnalytics call. That pairing is the only way to answer "which page owns
this query", so the weekly compare should use it for cannibalisation (one query
served by two pages) and for confirming an owning page before any rewrite is
queued. The UI export cannot produce it.

Update 2026-09-22, AI Overview layer. `aio.json` is a fifth source. It checks the
top 10 queries by impressions for an AI Overview, records which domains that
block cites, and captures the related searches, People Also Ask questions and top
10 organic URLs that arrive in the same paid response.

Read it this way in the weekly compare:

- An AI Overview above the organic list means position is a weak proxy for
  opportunity. Say so rather than treating rank as the whole story.
- `cited` is three-state. true, false, or null when the block came back as an
  asynchronous stub with no references. Null is unknown and must never be
  reported as uncited.
- `organic_rank` is null when the site sits outside the top 20. That is a depth
  limit, not a ranking of zero.
- `related_searches` are the modifiers Google attaches to a query. They are the
  cheapest content signal in the whole run, because GSC only shows queries the
  site already surfaces for.
- `top_organic` feeds serp_gap.py, which does free local term analysis against
  real competitors. Do not pay for a second SERP call to get those URLs.

Never pool related searches across queries. The neighbourhoods do not mix.

Update 2026-09-22, index status. `index.json` is a sixth source, from the Search
Console URL Inspection API, one call per sitemap URL. It replaces the manual
"Crawled - currently not indexed" export, which was a sample and went stale the
moment it was written.

Read the week-over-week set difference on `not_indexed_urls`, not just the count.
Naming the pages that recovered is the point. On 2026-09-22 the site had 139
indexed and 37 not, and a link graph repair shipped the same day, so the next few
runs are a live test of whether internal links were the constraint.

Keep the three states separate. Crawled-not-indexed is a content judgement,
discovered-not-crawled is a crawl budget and link problem, unknown-to-Google
means the URL is not discoverable at all. Collapsing them hides the fix.
