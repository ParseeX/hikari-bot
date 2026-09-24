# 独立卡片主库

本工具为后续网站建立 SQLite 主库，当前 Bot 不读取它。原 MC、YGOCDB 缓存、Cardrush 价格库和手机桥接均继续按原逻辑运行。只使用 Python 标准库，不新增生产依赖；不导入 NoneBot，不创建定时任务。

## 数据范围

- `cards`：固定的内部 ID、官方 cid、八位正式卡密、临时卡密、日文读音、类型/属性/种族、攻守、等级/阶级/Link、箭头、灵摆刻度、系列编码及来源原文。
- `card_identifiers`：临时/正式卡密历史；同一卡片可通过旧 ID 搜到。按约定，ID >= 100000000 为临时卡密。正式卡密保留前导零；没有卡密时为空。
- `card_names`：各来源的中文译名、日文/英文卡名；保留原文，另存搜索规范化值。
- `card_texts`：按语言和来源存类型文字、效果和灵摆效果。当前百鸽整库提供的是中文效果，不把日/英文名称当作对应语言效果。表结构允许后续补充官方日/英文文本。
- `card_releases`：百鸽提供的各语言首次发行日期与商品名称。不能用临时 ID 简单断定尚未发行；来源允许已发行但卡密尚未补齐。
- `packs`：已同步卡盒及校验数量；此次只加入实际同步过的卡盒，不代表全卡盒目录。
- `jhs_cards`、`jhs_versions`：集换社卡片映射、版本 ID、原始完整编号、原始罕贵，保留异画/原画/代标等括号内容。不同版本 ID 永不按编号和罕贵合并。
- `import_issues`：被排除的衍生物、资料不足或映射不唯一的记录。待核对记录不进入可用卡片表；无法匹配的集换社版本可以先保留，但内部卡片 ID 为空。
- `sync_state`、`sync_runs`：来源校验值、同步结果；不记录凭据。

`card_catalog` 视图便于查看卡名与已导入版本数。集换社价格、卖家出品和 Cardrush 数据不在此库；网站可用版本 ID 另行请求现有桥接。

来源：[百鸽 API](https://ygocdb.com/api)。整库含 `jp_ruby`，无需为读音逐张抓官方。过滤衍生物使用 `type & 0x4000`，其他资料不全或缺少已知实体卡池标记的条目进入待核对。保留所有正式收录的基础资料，包括暂未在集换社匹配的卡；当前集换社查询固定为 ygo/ocg。

## 创建与基础同步

服务器正式文件：`/home/xyk/.local/share/hikari-card-catalog/catalog.sqlite3`。请勿把 `--db` 指向原来的三个数据库。

```bash
python3 scripts/card_catalog/catalog.py \
  --db "$HOME/.local/share/hikari-card-catalog/catalog.sqlite3" sync-base
```

先检查 `cards.zip.md5`，有变化才下载 `cards.zip`；校验的是解压后的 `cards.json`。在本地比较每张卡片的字段，仅更新变化记录。同时读取 `idChangelog.jsonp` 和 `releaseDates.json`；卡密映射冲突保留在待核对表，不自动合并卡片。卡片在新快照消失时不删除已有数据。

后续重新执行同一命令即可更新，来源未变时不重新下载整库。当前没有自动调度；官方新商品发现与按发售日期触发同步仍属于后续接入工作。

## 按卡盒导入集换社版本

```bash
python3 scripts/card_catalog/catalog.py \
  --db "$HOME/.local/share/hikari-card-catalog/catalog.sqlite3" \
  sync-pack DBGV \
  --bridge-env "$HOME/.config/jihuanshe-bridge/env" \
  --expected-cards 45 --expected-versions 92 \
  --release-date 2026-09-05 \
  --source-url https://www.yugioh-card.com/japan/products/dbgv/
```

通过服务器本地现有桥接读取全部分页。凭据只在内存中使用；不会复制到库或源码。桥接目前限制最多 30 页，超过上限会失败，不接受截断结果；大型复刻盒可能需要未来扩展桥接分页能力。

严格筛选 `前缀-` 开头的编号，以版本 ID 更新和去重。可传官方核验的卡片/版本数量，数量不符则整批拒绝，不将旧版本删除。示例数量来自 [DBGV 官方商品规格](https://www.yugioh-card.com/japan/products/dbgv/)，不能用于其他盒。

首次映射只接受已有多语言名称中的唯一精确匹配（允许空白、全半角规范化）；模糊匹配不自动入关联。之后沿用已核实的集换社卡片 ID 映射；如新名字指向另一张卡，事务回滚。版本标记的原文不经过名称规范化。

同盒重跑不会重复插入。上游暂时缺少一个旧版本时仍保留旧记录，通过 `last_seen_at` 识别待复核状态。

## 查询与验收

```bash
python3 scripts/card_catalog/catalog.py --db /path/to/catalog.sqlite3 stats
python3 scripts/card_catalog/catalog.py --db /path/to/catalog.sqlite3 find 89631139
python3 scripts/card_catalog/catalog.py --db /path/to/catalog.sqlite3 find 灰流丽
python3 -m pytest tests/test_card_catalog.py -q
```

`find` 支持精确名称、正式卡密与已知临时卡密，返回基础资料、效果、发行资料和已导入的集换社版本；当前不是模糊搜索 API。`stats` 包含完整性和外键检查、待核对记录及实际覆盖量。

## 回滚

修改已有主库前，工具自动通过 SQLite backup API 在同目录 `backups/` 保存一致性备份；源数据快照存入 `source-snapshots/`。同步事务失败保留之前的数据。若需恢复，先停止所有主库写入者，使用 SQLite backup API 从选定备份恢复，避免仅复制主文件而遗漏 WAL。

当前没有进程依赖新库，可保留或移除整个独立目录；原 Bot 与原数据库不受影响。脚本读取未知 SQLite 文件时会拒绝初始化，以免误改旧库。
