-- Beat Manager database schema

CREATE TABLE IF NOT EXISTS artists (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,              -- official spelling
    key           TEXT NOT NULL UNIQUE,       -- normalized (lowercase alnum)
    aliases       TEXT[] DEFAULT '{}',
    city          TEXT,
    lastfm_listeners INTEGER,
    discovered_via TEXT,                      -- 'seed','yt_title','lastfm',...
    title_mentions INTEGER DEFAULT 0,         -- times seen in type beat titles
    priority      SMALLINT DEFAULT 5,         -- 1 = highest scan priority
    first_seen    TIMESTAMPTZ DEFAULT now(),
    last_scanned  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS artists_scan_idx ON artists (priority, last_scanned NULLS FIRST);

-- genre / scene / mood weights (0..1), one row per artist+tag
CREATE TABLE IF NOT EXISTS artist_tags (
    artist_id INTEGER REFERENCES artists(id) ON DELETE CASCADE,
    tag       TEXT NOT NULL,
    weight    REAL NOT NULL,
    source    TEXT NOT NULL,                  -- 'lastfm','yt_title','neighbors','audio','web'
    updated   TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (artist_id, tag, source)
);

-- relations between artists (similarity / co-occurrence in titles / collabs)
CREATE TABLE IF NOT EXISTS artist_links (
    a INTEGER REFERENCES artists(id) ON DELETE CASCADE,
    b INTEGER REFERENCES artists(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,                       -- 'lastfm_similar','title_cooc','collab'
    weight REAL NOT NULL,
    PRIMARY KEY (a, b, kind)
);

-- YouTube type beat market measurements (history kept)
CREATE TABLE IF NOT EXISTS yt_metrics (
    id         BIGSERIAL PRIMARY KEY,
    artist_id  INTEGER REFERENCES artists(id) ON DELETE CASCADE,
    measured   TIMESTAMPTZ DEFAULT now(),
    rel_n      SMALLINT,     -- matching results in relevance search
    sup7       SMALLINT,     -- uploads last 7 days (cap ~20)
    sup30      SMALLINT,     -- uploads last 30 days (cap ~20)
    vpd_med    REAL,         -- median views/day of <1y videos
    vpd_top    REAL,
    med_month  INTEGER,      -- median views of this month's uploads
    top5_avg   INTEGER
);
CREATE INDEX IF NOT EXISTS yt_metrics_artist_idx ON yt_metrics (artist_id, measured DESC);

-- business emails (public, collab/booking)
CREATE TABLE IF NOT EXISTS contacts (
    id        SERIAL PRIMARY KEY,
    email     TEXT NOT NULL UNIQUE,
    name      TEXT,
    role      TEXT,                           -- artist / a&r / manager / producer
    artist_id INTEGER REFERENCES artists(id),
    source    TEXT,                           -- where it was found / 'own_list'
    status    TEXT DEFAULT 'unverified',      -- valid / invalid / risky / unknown / bounced
    last_checked TIMESTAMPTZ,
    opened_count INTEGER DEFAULT 0,
    replied   BOOLEAN DEFAULT false,
    unsubscribed BOOLEAN DEFAULT false,
    added     TIMESTAMPTZ DEFAULT now()
);

-- beats
CREATE TABLE IF NOT EXISTS beats (
    id          SERIAL PRIMARY KEY,
    file_path   TEXT NOT NULL,
    original_name TEXT,
    name        TEXT,
    producers   TEXT[] DEFAULT '{conkretus}',
    bpm         REAL,
    bpm_given   REAL,
    musical_key TEXT,
    key_alt     TEXT,
    tuning_hz   REAL,
    tuning_raw  REAL,
    needs_review BOOLEAN DEFAULT false,
    review_note TEXT,
    status      TEXT DEFAULT 'new',           -- new/analyzed/queued/uploaded
    added       TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS uploads (
    id          SERIAL PRIMARY KEY,
    beat_id     INTEGER REFERENCES beats(id),
    platform    TEXT NOT NULL,                -- youtube/telegram/tiktok
    title       TEXT,
    formula     TEXT,                         -- e.g. 'YN Jay x FWC Big Key'
    scheduled   TIMESTAMPTZ,
    done        TIMESTAMPTZ,
    external_id TEXT,
    status      TEXT DEFAULT 'scheduled'
);

CREATE TABLE IF NOT EXISTS email_packs (
    id        SERIAL PRIMARY KEY,
    sent_at   TIMESTAMPTZ,
    beat_ids  INTEGER[],
    recipients INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (           -- audit log / alerts
    id      BIGSERIAL PRIMARY KEY,
    at      TIMESTAMPTZ DEFAULT now(),
    level   TEXT,                             -- info/warn/alert
    kind    TEXT,
    message TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value JSONB
);
INSERT INTO settings(key, value) VALUES ('killed', 'false') ON CONFLICT DO NOTHING;
