# 独立卡片主库

本工具维护 HikariBot 和后续网站共用的 SQLite 卡片主库。Bot 的卡名、效果、卡密、随机卡片、共界计算、卡组清单与价格查询名称解析均读取此库。Cardrush 价格库保持独立，卡图文件仍来自原图片源。导入工具只使用 Python 标准库，不导入 NoneBot。

## 数据范围

- `cards`：固定的内部 ID、官方 cid、八位正式卡密、临时卡密、日文读音、类型/属性/种族、攻守、等级/阶级/Link、箭头、灵摆刻度、系列编码及来源原文。
- `card_identifiers`：临时/正式卡密历史；同一卡片可通过旧 ID 搜到。按约定，ID >= 100000000 为临时卡密。正式卡密保留前导零；没有卡密时为空。
- `card_artwork_ids`：MC 明确声明的同名异画编号到内部卡片 ID 的对应关系。这些编号不是正式卡密；只迁入能定位主库原卡、且不会覆盖已有卡密的记录。
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

后续重新执行同一命令即可更新，来源未变时不重新下载整库。Bot 原有的“更新数据库”命令和每日 03:00 定时任务现在调用此基础同步，不再下载 MC 库。时间遵循 Bot 原有调度器时区。官方新商品发现与按发售日期触发集换社同步仍属于后续接入工作。

## Bot 接入和异画编号

`.env.prod` 中设置 `CARD_CATALOG_PATH=/home/xyk/.local/share/hikari-card-catalog/catalog.sqlite3`，未设置时默认使用当前用户的 `~/.local/share/hikari-card-catalog/catalog.sqlite3`。Bot 只读访问主库，资料查询不会回退到旧库或百鸽逐卡 API；文件缺失会报错，不自动创建空库。

首次接入前，执行基础同步及异画迁入：

```bash
python3 scripts/card_catalog/catalog.py \
  --db "$HOME/.local/share/hikari-card-catalog/catalog.sqlite3" \
  import-artworks --mc-db hikari_bot/data/card.cdb
```

异画表按 MC 的 `alias` 和同名校验建立，不凭相邻数字推断。不同名称但规则上视作同名的卡不会合并。已有卡组里的异画编号可以读取原卡资料。“卡图 青眼白龙”直接返回原画和全部已登记异画，按原画、异画编号升序放入一条消息；输入已登记的异画编号也返回该卡的整组图片。不再提供“异画1”等指定画面参数。输出统一为 624×624，非正方形图片保持比例并补边；某张下载失败时在对应位置标明，不丢弃其他图片。异画与集换社商品之间尚无可靠画面映射，不自动关联。

中文各译名、日文、英文均可查询，精确名称优先，随后按卡名子串匹配。数字只按正式卡密、临时卡密历史或已登记异画编号查找，不把内部 ID 当作卡密，也不模糊匹配邻近号码。每次查询重新打开连接，可及时读取同步提交后的数据。

主库目前只覆盖部分集换社版本，所以两种卡价指令先用主库解析名称，仍通过现有桥接获取完整罕贵列表和实时价格。不会用一个卡盒的版本冒充某张卡的完整版本列表。卡价曲线继续读取独立的 Cardrush 历史库。

“随机一卡”按原画及各个已登记异画等概率抽取；“每日一卡”保持原来的基础卡池。

异画迁入是显式操作，不再每日下载 MC。将来出现新异画，需要更新对应映射后重新导入。

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

## 按清单批量采集集换社

在服务器仓库目录运行 `scripts/card_catalog/crawl_jhs.py`。脚本复用当前手机桥接，只采集版本资料，不采集实时价格或卖家出品。

```bash
cd /home/xyk/hikari-bot
python3 scripts/card_catalog/crawl_jhs.py \
  --db "$HOME/.local/share/hikari-card-catalog/catalog.sqlite3" \
  --bridge-env "$HOME/.config/jihuanshe-bridge/env" \
  --packs DBGV ROTA
```

也可以用 `--packs-file /path/to/packs.txt` 代替 `--packs`。文本清单每行一个盒号，允许空行、`#` 注释，自动转为大写并去重。若需要检查卡片/版本数量，可以使用 JSON 清单：

```json
[
  {"prefix": "DBGV", "expected_cards": 45, "expected_versions": 92},
  {"prefix": "ROTA"}
]
```

同一盒号不能同时提供不同校验要求。没有填写预期数量时，只验证桥接完整翻页、数据字段和已有映射，不能据此证明上游包含该盒全部实体版本。预期数量应包含复刻、平行罕贵等实际需要采集的版本。

- 每盒结果事务入库，版本 ID 去重；括号、原始罕贵和编号原样保存，未匹配名称的卡片留作待核对。原始返回保存到 `source-snapshots/`。
- 默认串行查询，每盒间隔 10 秒。网络/服务繁忙最多尝试 3 次，重试逐步延长等待；数据校验失败不重复请求同样结果。连续失败 3 盒停止，认证或数据库错误立即停止。可通过 `--interval`、`--attempts`、`--max-failures` 调整。
- 当前桥接一次卡盒查询期间会占用手机通道，Bot 的实时卡价查询可能等待或提示忙；卡片资料、卡图等本地功能不依赖此通道。较大的清单建议在少用 Bot 卡价查询时运行。
- 一轮采集开始前备份一次主库。进度默认保存为主库旁的 `jhs-crawl-state.json`，可用 `--state` 指定独立进度文件。数据库恢复到旧备份后，应使用 `--refresh` 重采，避免沿用较新的完成记录。
- 重跑同一命令会跳过已完成且校验要求未变的盒子，重试失败或中断的盒子。若入库后、保存进度前中断，重跑仍按版本 ID 去重。`Ctrl+C` 或正常终止会尽量保存断点，进程被强制结束后也可以重跑。
- `--refresh` 重新查询本次清单的已完成盒子；不会删除旧版本。同一主库只允许一个批量采集进程运行。
- 成功退出码为 0；仍有失败或主动停止为 1；中断为 130。不要将非零退出码当作采集完成。

预览队列不查询手机、不写入文件：

```bash
python3 scripts/card_catalog/crawl_jhs.py \
  --db "$HOME/.local/share/hikari-card-catalog/catalog.sqlite3" \
  --packs DBGV ROTA --dry-run
```

查看已保存进度：

```bash
python3 scripts/card_catalog/crawl_jhs.py \
  --db "$HOME/.local/share/hikari-card-catalog/catalog.sqlite3" --status
```

这是启动后自动跑完清单的工具，不创建定时任务。大盒超过桥接当前 30 页限制时会失败并留待重试，不导入被截断的数据。

### 从官网整理按日期倒序的清单

`konami_packs.py` 读取日文官方收录目录，默认包括基本卡包、其他卡包、预组、Duel Terminal 和商品套装，不含书籍、赛事、促销和游戏附卡；可用 `--include-promos` 扩大范围。官网日期晚于 `--as-of` 的商品暂不进入队列。

```bash
.venv/bin/python scripts/card_catalog/konami_packs.py \
  --output "$HOME/.local/share/hikari-card-catalog/official-import-20260924/packs.json" \
  --cache-dir "$HOME/.local/share/hikari-card-catalog/official-import-20260924/cache" \
  --as-of 2026-09-24
```

目录里的 pid 是官方商品 ID。工具从卡片详情的收录记录中读取该 pid 对应的实际编号，提取连字符前的前缀，不把商品标题或网页地址猜成盒号。每张卡的详情同时补充其他商品的前缀；尚无证据的商品会核对首、中、末三张卡。此方法可能漏掉复杂套装中未抽查到的子包前缀，报告保留所核对的商品，不能宣称覆盖全部官方印刷版本。

清单按官网标注日期倒序，同一前缀合并一次，采用其最新商品日期。官网有些栏目使用公开日期，日期字段不保证全部是实际上市日。没有编号、格式无法识别等条目写入同目录 `packs.report.json` 的 `unresolved`；不会凭空补编号。原网页保存在 cache 中，后续可复核。官网网络/访问异常会停止整轮，避免把只取到一部分的清单当作完整清单执行。

本次一次性后台任务为 `hikari-jhs-official-import.service`，先完成官网清单整理，再将清单传给采集脚本。它不设置定时重复运行，也不随开机启动。按任务要求固定截至日期，后续批次应改用新的日期和输出目录。凭据仍由桥接配置读取，不写入 unit。

```bash
sudo systemctl status hikari-jhs-official-import.service
sudo journalctl -u hikari-jhs-official-import.service -f
sudo systemctl stop hikari-jhs-official-import.service
# 停止或失败后，从缓存和采集断点继续：
sudo systemctl start hikari-jhs-official-import.service
```

停止任务不会删除已导入资料。单盒失败会继续其他盒子，连续失败达到阈值时停止并保留进度；服务状态为 failed 时需要检查日志后再继续。

## 查询与验收

### 商品名与无编号商品

主库 schema v3 使用 `products.id` 作为商品内部 ID，保存 `jhs_pack_id`、`jhs_name`、`jhs_name_origin` 和发行日期。`product_official_links` 单独保存官方商品 pid、原名、来源地址；不把两个平台的名称相互覆盖。`jhs_version_products` 记录版本与商品的多对多关系，同一版本只保存一份；`jhs_versions.product_id` 仅保留首次确认的商品供旧代码兼容，`number_raw` 保留“无编号”及括号备注。

旧的 `packs` 表保留盒号批次及统计。迁移为每个旧盒号建立一个待核对商品记录（`legacy_prefix` 非空、`jhs_pack_id` 为空），保留全部原版本、时间戳和原始 JSON。历史数据不会仅凭名称自动绑定平台 ID；按平台商品 ID 重采并验证后才写入真实归属。后续旧盒号重采不会覆盖已经核实的商品归属。

先按集换社目录的名称／别名查商品，或通过一个已知卡片版本确定商品：

```bash
python3 scripts/card_catalog/catalog.py --db /path/to/catalog.sqlite3 find-products EX --bridge-env /path/to/bridge.env
python3 scripts/card_catalog/catalog.py --db /path/to/catalog.sqlite3 find-products --version-id 119467 --bridge-env /path/to/bridge.env
```

目录查询读取小程序当前公开的分类树，可能不包含隐藏或未放入目录的商品；空结果不代表平台没有收录。第二条路径读取卡片详情中的 `pack`。

给已导入盒号补齐商品名称／ID 时，使用 `{"prefix":"DBGV","backfill":true}`。它会从主库找尚未绑定商品 ID 的版本，通过卡片详情确认所属商品，再读取该商品完整版本列表并验证种子版本确实在其中。若一个盒号对应多个平台商品，会继续处理剩余未绑定版本，不把首张卡的归属套到整盒。每个商品单独事务提交，进度以数据库实际绑定情况为准；中断后已经补齐的版本不会重复查询。原始编号、罕贵、旧盒号和官方信息保留。

补齐任务的断点键为 `backfill:DBGV`，可以与普通盒号重试、无编号商品采集放入同一 JSON 队列，共用原有锁和断点文件。它们串行使用手机通道；不要另开一个采集进程争抢手机。失败保留已完成部分，按既有重试规则继续或停机，日志及状态文件会明确记录，不能将后台运行等同于全量完成。

无编号商品可在 JSON 采集清单中只填写 `jhs_pack_id`：

```json
[
  {"jhs_pack_id": 4404},
  {"prefix": "DBGV"}
]
```

仍使用 `crawl_jhs.py --packs-file` 批量导入，断点分别使用 `jhs:4404`、`DBGV`，兼容旧断点，不会因此重采所有已完成盒号。确认官方对应关系后可加 `konami_pid`、`name`、`source_url`、`release_date`；其中 `name` 始终是官方名称，集换社名称从实际接口获取。

按商品采集使用已核对的 `packId` 参数（普通搜索中的 `pack_id` 会被忽略），普通盒号搜索和按商品搜索均最多 300 页。验证分页稳定、版本 ID 不重复、商品详情中的卡片版本数与实收数量一致，并用商品详情中的版本样本独立核验筛选结果；失败整盒不写入。搜索返回的 `card_object_type` 用于过滤卡册等周边，数量校验只计算 `card`；旧误录周边保留在原表中并标记 `object_type`，不再参与卡片查询。搜索 `total` 可能包含未拆封原盒，不能直接当作卡片版本数量。显式 `expected_cards` / `expected_versions` 仍会额外校验。

在 DbGate 查看集换社名称和卡片版本：

```sql
SELECT p.id, p.jhs_pack_id, p.jhs_name, p.jhs_name_origin,
       p.prefix, COUNT(v.jhs_version_id) AS versions
FROM products p
LEFT JOIN jhs_version_products vp ON vp.product_id = p.id
LEFT JOIN jhs_versions v ON v.jhs_version_id = vp.jhs_version_id AND v.object_type='card'
GROUP BY p.id
ORDER BY p.jhs_pack_id IS NULL, p.id DESC;
```

首次使用新工具打开 v1 / v2 库会先备份再事务迁移；旧版本数据保留，已确认的商品归属自动复制到关联表。Bot 的读取入口兼容 v1、v2、v3。DbGate 可直接打开 `product_card_versions` 视图查看每个商品的卡片，或打开 `jhs_version_products` 查看关联。回滚 schema 时先停止所有写入者，恢复迁移前的 SQLite 备份及配套断点，再回退代码。迁移后的新增导入应另行保留，不能直接覆盖掉。

```bash
python3 scripts/card_catalog/catalog.py --db /path/to/catalog.sqlite3 stats
python3 scripts/card_catalog/catalog.py --db /path/to/catalog.sqlite3 find 89631139
python3 scripts/card_catalog/catalog.py --db /path/to/catalog.sqlite3 find 灰流丽
python3 -m pytest tests/test_card_catalog.py -q
```

`find` 支持精确名称、正式卡密与已知临时卡密，返回基础资料、效果、发行资料和已导入的集换社版本；当前不是模糊搜索 API。`stats` 包含完整性和外键检查、待核对记录及实际覆盖量。

## 回滚

修改已有主库前，工具自动通过 SQLite backup API 在同目录 `backups/` 保存一致性备份；源数据快照存入 `source-snapshots/`。同步事务失败保留之前的数据。若需恢复，先停止所有主库写入者，使用 SQLite backup API 从选定备份恢复，避免仅复制主文件而遗漏 WAL。

Bot 已依赖此库，运行期间不要删除目录。回滚应用时恢复切换前代码及 `.env.prod` 备份并重启 Bot，保留的 MC 库与 YGOCDB 缓存可供旧代码继续使用。新增异画表不会影响旧代码；数据库恢复只在确认需要撤销数据修改时执行。脚本读取未知 SQLite 文件时会拒绝初始化，以免误改旧库。
