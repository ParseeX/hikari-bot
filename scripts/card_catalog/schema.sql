PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS catalog_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY,
    konami_cid INTEGER NOT NULL UNIQUE CHECK(konami_cid > 0),
    passcode TEXT UNIQUE CHECK(passcode IS NULL OR
        (length(passcode) = 8 AND passcode NOT GLOB '*[^0-9]*')),
    temporary_id TEXT UNIQUE CHECK(temporary_id IS NULL OR
        (temporary_id NOT GLOB '*[^0-9]*' AND CAST(temporary_id AS INTEGER) >= 100000000)),
    japanese_reading TEXT,
    type_code INTEGER CHECK(type_code IS NULL OR (type_code & 16384) = 0),
    race_code INTEGER,
    attribute_code INTEGER,
    atk_raw INTEGER,
    def_raw INTEGER,
    atk_text TEXT,
    def_text TEXT,
    level INTEGER,
    rank INTEGER,
    link_rating INTEGER,
    link_markers INTEGER,
    pendulum_left INTEGER,
    pendulum_right INTEGER,
    level_raw INTEGER,
    setcode TEXT,
    scope_code INTEGER,
    source_hash TEXT NOT NULL,
    source_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 临时/正式卡密历史独立保留；旧卡组仍可定位同一个内部 id。
CREATE TABLE IF NOT EXISTS card_identifiers (
    kind TEXT NOT NULL CHECK(kind IN ('passcode', 'temporary')),
    value TEXT NOT NULL,
    card_id INTEGER NOT NULL REFERENCES cards(id),
    source TEXT NOT NULL,
    PRIMARY KEY(kind, value)
);
CREATE INDEX IF NOT EXISTS idx_identifiers_card ON card_identifiers(card_id);

CREATE TABLE IF NOT EXISTS card_names (
    card_id INTEGER NOT NULL REFERENCES cards(id),
    language TEXT NOT NULL,
    source TEXT NOT NULL,
    name TEXT NOT NULL,
    normalized TEXT NOT NULL,
    PRIMARY KEY(card_id, language, source, name)
);
CREATE INDEX IF NOT EXISTS idx_names_search ON card_names(normalized);

CREATE TABLE IF NOT EXISTS card_texts (
    card_id INTEGER NOT NULL REFERENCES cards(id),
    language TEXT NOT NULL,
    source TEXT NOT NULL,
    type_text TEXT NOT NULL DEFAULT '',
    effect TEXT NOT NULL DEFAULT '',
    pendulum_effect TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(card_id, language, source)
);

CREATE TABLE IF NOT EXISTS card_releases (
    card_id INTEGER NOT NULL REFERENCES cards(id),
    language TEXT NOT NULL,
    release_date TEXT,
    pack_name TEXT,
    source TEXT NOT NULL,
    PRIMARY KEY(card_id, language, source)
);

CREATE TABLE IF NOT EXISTS packs (
    prefix TEXT PRIMARY KEY,
    name TEXT,
    release_date TEXT,
    source_url TEXT,
    expected_cards INTEGER,
    expected_versions INTEGER,
    observed_cards INTEGER NOT NULL,
    observed_versions INTEGER NOT NULL,
    last_synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jhs_cards (
    jhs_card_id INTEGER PRIMARY KEY CHECK(jhs_card_id > 0),
    card_id INTEGER REFERENCES cards(id),
    match_method TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jhs_cards_card ON jhs_cards(card_id);

CREATE TABLE IF NOT EXISTS jhs_versions (
    jhs_version_id INTEGER PRIMARY KEY CHECK(jhs_version_id > 0),
    jhs_card_id INTEGER NOT NULL REFERENCES jhs_cards(jhs_card_id),
    pack_prefix TEXT NOT NULL REFERENCES packs(prefix),
    number_raw TEXT NOT NULL,
    rarity_raw TEXT NOT NULL,
    name_cn TEXT NOT NULL,
    name_jp TEXT NOT NULL,
    source_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_versions_card ON jhs_versions(jhs_card_id);
CREATE INDEX IF NOT EXISTS idx_versions_pack ON jhs_versions(pack_prefix);

CREATE TABLE IF NOT EXISTS import_issues (
    source TEXT NOT NULL,
    source_key TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(source, source_key)
);

CREATE TABLE IF NOT EXISTS sync_state (
    source TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    summary_json TEXT NOT NULL
);

CREATE VIEW IF NOT EXISTS card_catalog AS
SELECT c.*,
    (SELECT name FROM card_names n WHERE n.card_id=c.id AND n.source='ygocdb:jp_name' LIMIT 1) AS name_jp,
    (SELECT name FROM card_names n WHERE n.card_id=c.id AND n.source='ygocdb:en_name' LIMIT 1) AS name_en,
    (SELECT name FROM card_names n WHERE n.card_id=c.id AND n.source='ygocdb:cn_name' LIMIT 1) AS name_cn,
    (SELECT COUNT(*) FROM jhs_versions v JOIN jhs_cards j USING(jhs_card_id) WHERE j.card_id=c.id) AS jhs_version_count
FROM cards c;
