-- 003_private_detection.sql — premises-level private-charging inference
-- There is no public register of private depot charge points. This table holds the
-- stacked-evidence verdict per business premises, with reasons + source URLs.
-- Idempotent.

CREATE TABLE IF NOT EXISTS premises_scores (
    company_key    text NOT NULL,
    place_key      text NOT NULL DEFAULT '',   -- postcode, else city, else ''
    operator       text,
    city           text,
    postcode       text,
    address        text,
    tier           text,                    -- A verified | B probable | C candidate
    score          numeric NOT NULL DEFAULT 0,
    channels       jsonb NOT NULL DEFAULT '{}'::jsonb,
    reasons        jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence       jsonb NOT NULL DEFAULT '[]'::jsonb,
    power_kw       numeric,
    n_points       integer,
    hgv_capable    boolean NOT NULL DEFAULT false,
    first_seen_at  date,
    last_seen_at   date,
    scored_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_key, place_key)
);
CREATE INDEX IF NOT EXISTS premises_tier_idx ON premises_scores (tier, score DESC);

-- Read model for the API/report: only rows a human can act on.
CREATE OR REPLACE VIEW private_depots AS
SELECT operator, city, postcode, address, tier, score, power_kw, n_points,
       hgv_capable, reasons, evidence, first_seen_at, last_seen_at
FROM premises_scores
WHERE tier IN ('A', 'B')
ORDER BY tier, score DESC;

-- Sites gain an explicit tier + geocoding precision so city-centroid guesses are never
-- mistaken for verified addresses.
ALTER TABLE sites ADD COLUMN IF NOT EXISTS tier text;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS geo_precision text;

-- News signals are the primary discovery channel for private sites: allow them into the
-- clustering core, but only when they carry a real address (city centroids would merge
-- unrelated depots).
CREATE OR REPLACE VIEW news_signals AS
SELECT signal_id, operator, city, postcode, address, power_kw, n_points, status_hint,
       doc_url, observed_at, raw_state->>'lifecycle' AS lifecycle,
       raw_state->>'geo_precision' AS geo_precision
FROM signals
WHERE kind = 'news';
