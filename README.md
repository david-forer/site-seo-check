# site-seo-check

A weekly site and SEO monitor. It collects six sources into a dated snapshot,
compares that snapshot against last week's, and writes a short delta report
naming what changed and what to do about it.

Built for davidjforer.com and generic enough to point at any site. Python
standard library plus `requests` and the Google API client.

## What it collects

| Source | What it answers |
|---|---|
| Search Console | clicks, impressions, position, this period against the last, and paired page-and-query rows |
| Analytics | sessions, channels, landing pages, key events |
| PageSpeed Insights | Core Web Vitals on a short list of URLs |
| Crawl | every sitemap URL, checked for broken links, redirects, missing or duplicated meta, and noindex |
| Index status | per-URL index state from the URL Inspection API |
| AI Overview | whether a top query carries an AI Overview, which domains it cites, plus the related searches and People Also Ask it returns |

The last two are the ones most tools skip and they are the reason this exists.

Search Console reports position and impressions and says nothing about what sits
above the organic results. On the site this was built for, every one of the top
ten queries carried an AI Overview, so position was a poor proxy for opportunity.
The AI Overview check separates "we rank badly" from "the click was taken before
the organic list started".

Index status is the same idea for a different problem. The Search Console UI
exports a sample of pages that are crawled and not indexed, and it ages the
moment you download it. The API answers per URL, on demand, and distinguishes
three states that need three different fixes: crawled and declined, discovered
and never crawled, and never seen at all.

## Layout

```
directives/   what the check is for and how to judge the output
execution/    the collectors. run_all.py is the only data path
state/        snapshots, one dated folder per run
reports/      the written delta report, one per run
inbox/        drop manual exports here and the next run ingests them
```

The design is deliberate: a directive that defines the job, an orchestrator that
reads the data and writes prose, and deterministic scripts that do the fetching.
If a source breaks, fix the script rather than working around it by hand.

## Getting started

```
pip install -r requirements.txt
py execution/run_all.py
```

The crawl and PageSpeed checks work with no setup at all. Search Console,
Analytics, the index check and the AI Overview column each need credentials.
`SETUP.md` walks through it, about fifteen minutes, and `verify_google.py`
checks every link in the chain and names the single next action rather than
failing somewhere inside a full run.

A full run on a 200 page site takes roughly 20 to 25 minutes. The index check is
most of that, because it is one API call per URL.

## Cost

Everything is free except the AI Overview check, which uses DataForSEO at
roughly $0.002 a query and is capped at 10 queries a run. About two cents a week.
Set `aio_enabled` to false in `config.json` to turn it off entirely.

## Do not commit

`.env` and `secrets/` are gitignored and must stay that way. `secrets/` holds a
Google service account private key. The `state/`, `reports/` and `inbox/`
directories are kept in the repo so a fresh clone can run, but their contents are
ignored, because they are the monitored site's own analytics data.
