# One-time setup, about 15 minutes

The crawl and Core Web Vitals checks work with no setup at all. This unlocks
Search Console, Analytics, the per-URL index check and the AI Overview column.

All paths below are relative to the repo root. Windows examples use backslashes,
everything works the same on macOS and Linux.

## 1. Google Cloud project and service account

1. Go to https://console.cloud.google.com and create a project. Any name works,
   `site-seo-check` is a reasonable one
2. APIs and Services > Library, enable these three
   - Google Search Console API
   - Google Analytics Data API
   - PageSpeed Insights API
3. APIs and Services > Credentials > Create Credentials > Service account
   - Any name. No roles are needed. Create and continue, then Done
4. Open the service account > Keys > Add key > Create new key > JSON
   - Save the downloaded file as `secrets\service-account.json` in this repo.
     That folder is gitignored, and the file holds a private key, so it must
     never be committed
5. Still in Credentials, Create Credentials > API key. Copy it, this is the
   PageSpeed key

## 2. Grant the service account read access

The service account's address is inside the JSON file as `client_email` and
looks like `<name>@<project>.iam.gserviceaccount.com`. Step 4 below prints it
for you, so you do not have to open the key file.

1. Search Console, https://search.google.com/search-console, pick your property
   then Settings > Users and permissions > Add user. Paste the address and
   choose Full, not Owner
2. Google Analytics, https://analytics.google.com, Admin > Property access
   management > plus > Add users. Same address, role Viewer
3. While in GA4 Admin, copy the Property ID from Admin > Property details. It is
   a plain number

## 3. Fill in .env

Copy `.env.example` to `.env` and fill it in:

```
GOOGLE_APPLICATION_CREDENTIALS=secrets\service-account.json
GA4_PROPERTY_ID=<the number from step 2.3>
PSI_API_KEY=<the API key from step 1.5>
```

An absolute path works too if you prefer one. `.env` is gitignored.

## 4. Point it at your site

In `config.json`:

- `site_url` and `gsc_property` are the site being monitored. If your Search
  Console property is URL-prefix rather than domain, `gsc_property` is
  `https://example.com/` rather than `sc-domain:example.com`
- `psi_pages` are the three URLs checked for Core Web Vitals

Filesystem paths live in `.env`, not in `config.json`, because the config is
committed and a path is specific to one machine. Both of these are optional:

- `SITE_REPO` is a checkout of the website's own source. Only `gsc_inbox.py` and
  `merge_candidates.py` need it, because they read the site's markdown.
  Everything else runs without it
- `SHARED_ENV_FILE` points at another `.env` so a shared `DATAFORSEO_API_KEY`
  can live in one place across projects

## 5. Verify

Run the credential doctor first. It checks each link in the chain and prints the
single next action, rather than failing silently inside a full run:

```
py execution\verify_google.py
```

It prints the service account address, so you can run it straight after step 1.4
to get what you need for step 2 without opening the key file. Exit code 0 means
Search Console is ready.

Then run the full collection:

```
py execution\run_all.py
```

All six sources should report ok. A full run takes roughly 20 to 25 minutes on a
200 page site, and the index check is most of that, because it is one Search
Console call per URL.

## 6. DataForSEO, optional, for the AI Overview column

The AI Overview check is the only collector that spends money, roughly $0.002 a
query, capped at 10 queries a run, so about two cents a week.

It reads `DATAFORSEO_API_KEY` from this project's `.env`, then from the file
named by `SHARED_ENV_FILE` if that is set. If neither has it, the check reports
skipped and the run continues.

Controls in `config.json`:

- `aio_enabled` false turns it off entirely
- `aio_max_queries` hard cap on calls per run, default 10
- `aio_min_impressions` floor for a query to be worth checking, default 20

It takes its query list from the Search Console pull in the same run, so it
costs nothing when Search Console is skipped.

## 7. The index check

`index_check_enabled` and `index_check_limit` in `config.json` control the
per-URL index status pull. Quota is 2000 URLs a day and 600 a minute per
property. If a run ever approaches your timeout, lower `index_check_limit`
rather than turning the check off, because the week-over-week index delta is the
most useful number this tool produces.
