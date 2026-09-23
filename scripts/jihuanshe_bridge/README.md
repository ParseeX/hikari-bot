# 集换社手机桥接

机器人通过本机 HTTP 服务调用已授权 OPPO 上的微信小程序，再与原有 Cardrush SQLite 库合并结果。微信登录态留在手机内；HTTP 不提供任意脚本执行入口。

## 用户使用

发送 `卡价 原石之皇脉`（或 `卡价查询 原石の皇脈`、卡密），等待罕贵列表，再回复编号或 `SR`、`UTR` 等列表中的名称。选择有效期 3 分钟，回复 `取消` 结束。同一罕贵下不同卡包编号分别展示，不合并成一个价格。原来的尾部罕贵和卡包过滤仍支持，例如 `卡价 原石之皇脉 SR LOCR`。

- 集换社：该版本 `min_price` 为人民币最低价，价格历史最新日期的 `price` 为集换价；不使用 `avg_price` 冒充集换价。
- Cardrush：读取原有数据库最新买取记录，保留日元；数据库无记录与接口故障分别提示。最终结果省略括号说明和日期，查询过程只显示选择列表和结果。
- 先用卡片库解析日文原名，原文查询集换社。上游 `name_origin` 实际为中文别名，用中文正式名/别名辅助核对，不能当日文名过滤。
- 原文无结果时，使用 Unicode NFKC 等价写法补查，例如 `The Fallen ＆ The Virtuous` → `The Fallen & The Virtuous`；展示仍保留日文原名，结果继续校验卡名，排除同时命中的卡套。
- Cardrush 按卡片名称、完整编号及罕贵同时核对，避免把日亚版、其他卡包或类似名称混入。

### 日版价格查询

发送 `日版价格查询 原石之皇脉` 或 `日版卡价查询 原石之皇脉`，按相同流程选择罕贵。仅显示人民币日版最低价和 Cardrush 日元买取价，不读取或展示集换价。

按用户指定的字面规则匹配卖家出品备注：包含 `日版`，或单独的 `日` 字（前后为空白、标点或文本边界）。`今日发货`、`日文`、无备注均不匹配。使用 `remark_only`，缺失时读取 `remark`；不根据卡片日文名称、卖家所在地或无备注推断日版。此结果表示备注匹配，不验证实物产地。

完整读取卖家分页后，按卖家最低出品价由低到高读取具体出品，跳过零库存和已下架条目。找到匹配价格后，仅继续检查起价更低的卖家。同一罕贵的不同卡包分别处理；没有匹配与读取失败分别提示。最多读取 100 页，每个版本受查询超时限制；分页不完整、页数变化或低价卖家详情失败时不把部分结果当作最低价。这种查询需要读取备注，通常比普通卡价查询慢。

## 已验证环境与边界

OPPO Android 16、微信 8.0.78（3180）、Frida 17.18.0。运行库与 Frida 服务端有 SHA-256 校验；微信升级后需重新验证运行类与生命周期方法。

服务器通过现有 Tailscale 子网路由连接手机无线 ADB。手机需在线且已授权该服务器的 ADB 公钥，网络不通或无线调试端口改变时会报暂时不可用。服务器有自己的 ADB 私钥，不复制个人电脑私钥。

正常查询前解除该小程序进程冻结并恢复其 JS 循环，完成后若小程序不在前台则暂停循环；微信和小程序无需一直置顶，锁屏也可查。上下文丢失时自动临时唤醒、通过微信正常入口恢复小程序，然后回桌面并恢复原锁屏状态。该恢复可能短暂显示微信，并耗时几十秒；不改 PIN、不关闭锁屏设置。重启后的首次凭据解锁及整夜稳定性尚未验证。

冷恢复使用 `dumpsys activity activities` 中前台 `ActivityRecord` 对应的 `ProcessRecord` 定位小程序 PID，并继续核对进程身份。不依赖 `dumpsys activity top`，因为 Android 16 上该输出可能只包含其他任务。历史小程序任务、主微信进程和缺失的进程记录均不会被选中。

查询串行经过手机，单次失败有有限重试和一次上下文重建。多用户并发过高会返回忙，请稍后重试。停止服务会清理自己记录的探针小程序进程、Frida 服务端与 ADB 转发；不结束整个微信或其他手机服务。

桥接启动后主动准备一次手机会话，随后才处理查询；服务刚启动时到达的请求仍需等待准备完成。准备失败后继续提供接口，由下一次查询触发有限恢复，不增加定时保活或后台价格刷新。页面打开后检测运行库、业务模块和登录态就绪，立即继续，不再固定等待 8 秒。

## 服务器配置

桥接依赖单独安装，不加入 bot 的生产依赖：系统 `adb`，独立 Python 3.11 虚拟环境及本目录 `requirements.txt`。示例路径：

```text
/home/xyk/.local/share/jihuanshe-bridge/.venv
/home/xyk/.local/share/jihuanshe-bridge/frida-server
/home/xyk/.local/share/jihuanshe-bridge/state
/home/xyk/.config/jihuanshe-bridge/env
```

`env` 权限 600，目录权限 700。内容使用实际地址及随机生成的至少 32 字符密钥，禁止提交到 Git：

```dotenv
JHS_ADB_SERIAL=PHONE_IP:ADB_PORT
JHS_FRIDA_BINARY=/home/xyk/.local/share/jihuanshe-bridge/frida-server
JHS_STATE_DIR=/home/xyk/.local/share/jihuanshe-bridge/state
JHS_ACCESS_TOKEN=REPLACE_WITH_RANDOM_BRIDGE_SECRET
```

bot 的 `.env.prod` 设置 `JIHUANSHE_BRIDGE_URL=http://127.0.0.1:8791`、`JIHUANSHE_BRIDGE_TOKEN`（相同桥接密钥）、`JIHUANSHE_BRIDGE_TIMEOUT=120`。服务只监听 `127.0.0.1`，不开放公网端口。手机 Frida 只监听本机环回，经 ADB 转发访问。

复制本目录 systemd 单元至 `/etc/systemd/system/jihuanshe-bridge.service`，核对用户和路径后执行 `systemctl daemon-reload`、`systemctl enable --now jihuanshe-bridge.service`。部署脚本始终重启 bot；桥接单元存在时，仅在桥接运行文件变化或服务未运行时重启桥接。普通 bot 和文档更新保留手机会话。修改仓库外的桥接配置后仍需手动重启该单元。只有监听成功不代表手机可查，验收需要通过两个实际查询端点。

固定接口均要求 `Authorization: Bearer <桥接密钥>`：

- `POST /v1/versions`，JSON `{"name_jp":"原石の皇脈"}`。
- `POST /v1/prices`，JSON `{"version_ids":[504446]}`，最多 20 个不同正整数。
- `POST /v1/listings`，JSON `{"card_version_id":504446,"pages":[1,2]}`，每批最多 5 页，页码不超过 100。
- `POST /v1/listing-details`，JSON 包含 `card_version_id`、`seller_ids`，每批最多 5 个卖家；只返回卡片标识、出品价格、库存和备注，不返回卖家联系方式。

不要把完整请求头或配置文件写入日志。排障可查看 `journalctl -u jihuanshe-bridge.service`，日志只输出失败类型和查询阶段。

恢复失败会记录固定的 `stage`，例如 `open_miniapp`（打开并定位小程序）、`runtime_version`（运行库检查）、`business_context`（登录后的业务环境），不输出异常正文或登录信息。服务显示 `active` 后仍需调用 `/v1/versions` 确认手机查询可用。

## 回滚

停止并禁用桥接单元，恢复变更前保存的 `.env.prod`，回退对应 bot 代码后重启 `bot.service`。价格数据库没有迁移或写入变更。服务器新增 ADB 授权如需撤销，仅移除服务器对应公钥，保留原电脑公钥；应通过原电脑连接操作，不能盲目覆盖备份导致随后新增的授权丢失。独立环境与状态位于 bot 仓库之外，不受部署的 `git clean` 影响。
