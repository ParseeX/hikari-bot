-- 在单个事务内迁移；调用者在执行前通过 SQLite backup API 备份。
BEGIN IMMEDIATE;
DROP VIEW IF EXISTS card_catalog;
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    legacy_prefix TEXT UNIQUE REFERENCES packs(prefix),
    prefix TEXT,
    jhs_pack_id INTEGER UNIQUE CHECK(jhs_pack_id > 0),
    jhs_name TEXT,
    jhs_name_origin TEXT,
    release_date TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product_official_links (
    product_id INTEGER NOT NULL REFERENCES products(id),
    konami_pid TEXT NOT NULL,
    name TEXT,
    source_url TEXT,
    release_date TEXT,
    PRIMARY KEY(product_id, konami_pid)
);

INSERT INTO products(legacy_prefix,prefix,release_date,updated_at)
SELECT prefix,prefix,release_date,last_synced_at FROM packs ORDER BY prefix;
ALTER TABLE jhs_versions RENAME TO jhs_versions_v1;
DROP INDEX IF EXISTS idx_versions_card;
DROP INDEX IF EXISTS idx_versions_pack;
CREATE TABLE IF NOT EXISTS jhs_versions (
    jhs_version_id INTEGER PRIMARY KEY CHECK(jhs_version_id > 0),
    jhs_card_id INTEGER NOT NULL REFERENCES jhs_cards(jhs_card_id),
    pack_prefix TEXT REFERENCES packs(prefix),
    product_id INTEGER NOT NULL REFERENCES products(id),
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
CREATE INDEX IF NOT EXISTS idx_versions_product ON jhs_versions(product_id);

INSERT INTO jhs_versions
SELECT v.jhs_version_id,v.jhs_card_id,v.pack_prefix,p.id,
       v.number_raw,v.rarity_raw,v.name_cn,v.name_jp,v.source_json,
       v.first_seen_at,v.last_seen_at,v.updated_at
FROM jhs_versions_v1 v JOIN products p ON p.legacy_prefix=v.pack_prefix;
DROP TABLE jhs_versions_v1;
UPDATE catalog_meta SET value='2' WHERE key='schema_version';
CREATE VIEW IF NOT EXISTS card_catalog AS
SELECT c.*,
    (SELECT name FROM card_names n WHERE n.card_id=c.id AND n.source='ygocdb:jp_name' LIMIT 1) AS name_jp,
    (SELECT name FROM card_names n WHERE n.card_id=c.id AND n.source='ygocdb:en_name' LIMIT 1) AS name_en,
    (SELECT name FROM card_names n WHERE n.card_id=c.id AND n.source='ygocdb:cn_name' LIMIT 1) AS name_cn,
    (SELECT COUNT(*) FROM jhs_versions v JOIN jhs_cards j USING(jhs_card_id) WHERE j.card_id=c.id) AS jhs_version_count
FROM cards c;
