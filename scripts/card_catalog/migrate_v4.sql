-- 允许以集换社身份独立收录实体卡；保留所有现有内部 ID。
BEGIN IMMEDIATE;
DROP VIEW IF EXISTS card_catalog;
CREATE TABLE cards_v4 (
    id INTEGER PRIMARY KEY,
    konami_cid INTEGER UNIQUE CHECK(konami_cid IS NULL OR konami_cid > 0),
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

INSERT INTO cards_v4 SELECT * FROM cards;
DROP TABLE cards;
ALTER TABLE cards_v4 RENAME TO cards;
CREATE VIEW IF NOT EXISTS card_catalog AS
SELECT c.*,
    (SELECT name FROM card_names n WHERE n.card_id=c.id AND n.language='ja' ORDER BY n.source='ygocdb:jp_name' DESC,n.source LIMIT 1) AS name_jp,
    (SELECT name FROM card_names n WHERE n.card_id=c.id AND n.language='en' ORDER BY n.source='ygocdb:en_name' DESC,n.source LIMIT 1) AS name_en,
    (SELECT name FROM card_names n WHERE n.card_id=c.id AND n.source IN ('ygocdb:cn_name','jhs:cn_name') ORDER BY n.source='ygocdb:cn_name' DESC LIMIT 1) AS name_cn,
    (SELECT COUNT(*) FROM jhs_versions v JOIN jhs_cards j USING(jhs_card_id) WHERE j.card_id=c.id AND v.object_type='card') AS jhs_version_count
FROM cards c;

UPDATE catalog_meta SET value='4' WHERE key='schema_version';
