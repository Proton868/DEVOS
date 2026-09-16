-- Durable web intelligence pages and events
CREATE TABLE IF NOT EXISTS web_crawl_pages (
  page_id text PRIMARY KEY,
  crawl_id text NOT NULL,
  url text,
  normalized_url text,
  depth integer,
  status text,
  parent_page_id text,
  meta jsonb DEFAULT '{}'::jsonb,
  created_at double precision,
  updated_at double precision
);
CREATE INDEX IF NOT EXISTS idx_web_crawl_pages_crawl ON web_crawl_pages (crawl_id);
CREATE INDEX IF NOT EXISTS idx_web_crawl_pages_status ON web_crawl_pages (status);
CREATE INDEX IF NOT EXISTS idx_web_crawl_pages_norm ON web_crawl_pages (normalized_url);

CREATE TABLE IF NOT EXISTS web_crawl_events (
  id text PRIMARY KEY,
  crawl_id text NOT NULL,
  event_type text,
  payload jsonb DEFAULT '{}'::jsonb,
  created_at double precision
);
CREATE INDEX IF NOT EXISTS idx_web_crawl_events_crawl ON web_crawl_events (crawl_id);
CREATE INDEX IF NOT EXISTS idx_web_crawl_events_type ON web_crawl_events (event_type);
