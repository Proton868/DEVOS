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
