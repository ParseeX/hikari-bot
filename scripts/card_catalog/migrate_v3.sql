-- 仅新增关联与分类；保留旧单商品字段，支持兼容读取和备份恢复。
BEGIN IMMEDIATE;
ALTER TABLE jhs_versions ADD COLUMN object_type TEXT NOT NULL DEFAULT 'card';
CREATE TABLE jhs_version_products (
    jhs_version_id INTEGER NOT NULL REFERENCES jhs_versions(jhs_version_id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    source_json TEXT NOT NULL,
    PRIMARY KEY(jhs_version_id, product_id)
);
CREATE INDEX idx_version_products_product ON jhs_version_products(product_id);
INSERT INTO jhs_version_products
SELECT v.jhs_version_id,v.product_id,v.first_seen_at,v.last_seen_at,v.source_json
FROM jhs_versions v JOIN products p ON p.id=v.product_id WHERE p.jhs_pack_id IS NOT NULL;
DROP VIEW IF EXISTS card_catalog;
UPDATE catalog_meta SET value='3' WHERE key='schema_version';

CREATE VIEW IF NOT EXISTS card_catalog AS
SELECT c.*,
    (SELECT name FROM card_names n WHERE n.card_id=c.id AND n.source='ygocdb:jp_name' LIMIT 1) AS name_jp,
    (SELECT name FROM card_names n WHERE n.card_id=c.id AND n.source='ygocdb:en_name' LIMIT 1) AS name_en,
    (SELECT name FROM card_names n WHERE n.card_id=c.id AND n.source='ygocdb:cn_name' LIMIT 1) AS name_cn,
    (SELECT COUNT(*) FROM jhs_versions v JOIN jhs_cards j USING(jhs_card_id) WHERE j.card_id=c.id AND v.object_type='card') AS jhs_version_count
FROM cards c;
