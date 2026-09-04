# Web Intelligence (public crawl platform)

Web Intelligence accesses **publicly available resources only**. It does **not** bypass authentication, paywalls, CAPTCHA, access controls, private profiles, or security mechanisms.

## Architecture

```
PERSONA → NUHA → MISSION → UCIP → CRAWLER → EVIDENCE → VERIFICATION
```

UCIP authorizes network access. The crawler is an executor, not an authority.

## Components

- **Frontier**: normalized URL queue with depth and dedup
- **robots.txt**: Allow/Disallow/Sitemap, default obey
- **Sitemap**: limited nested index support
- **Budgets**: max_pages, max_depth, max_bytes, max_requests
- **SSRF**: block private/loopback/metadata; re-check redirect targets
- **Durable store**: `data/web_intel.sqlite3` (crawls, pages, events)
- **Outbox**: lifecycle events (not the execution queue)

## Statuses

Crawl: QUEUED → DISCOVERING → CRAWLING → COMPLETED | PARTIAL | FAILED | CANCELLED  
Page: DISCOVERED/QUEUED → FETCHING → EXTRACTED | DUPLICATE | BLOCKED | FAILED

PARTIAL means a budget/limit stopped the crawl — not site completeness.

## Web Intelligence Cache

Authoritative durable cache: `data/web_intel.sqlite3` table `web_cache`.

- States: FRESH / STALE / MISSING
- Conditional: If-None-Match / If-Modified-Since → 304 revalidation
- TTL: WEB_CACHE_TTL_SECONDS (default 86400), min/max bounds
- Provenance: fetched_at vs last_validated_at vs cache_used_at
- Cache hits skip network budget; refresh re-applies SSRF/robots/UCIP
- Never bypasses safety on refresh



## Acceptance

See [ACCEPTANCE_CONTRACT.md](ACCEPTANCE_CONTRACT.md) for final closure status rules,
test entry points, and security evidence requirements.

Production crawl execution is **Jobs-only** (`job_type=web_crawl`).  
Inline crawl is **test-only** via `DEVOS_WEB_CRAWL_INLINE_TEST=1`.
