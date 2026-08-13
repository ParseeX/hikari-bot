# `ocg-ruling-assistant` 接入 hikari-bot 调研

调研日期：2026-08-13（Asia/Tokyo）
上游固定版本：[`5bd3499141e1f3ccfccd0f1efdcbc4125697edad`](https://github.com/coldiceh/ocg-ruling-assistant/tree/5bd3499141e1f3ccfccd0f1efdcbc4125697edad)

## 结论

该项目不是可安装的 Python/NoneBot 插件，而是 Node.js 20+、pnpm、ESM 的完整网页与 RAG 后端应用。[`package.json`](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/package.json#L1-L8) 未声明第三方运行时依赖；可用 `pnpm dev:backend` 启动本地 HTTP 服务。它适合作为 hikari-bot 的**外部裁定服务**，不应把其源码或运行时嵌进 NoneBot 进程。

建议分两步推进：先经作者确认后，以服务端调用上游已部署的 `/api/answer` 做小范围 PoC；验证命令体验、时延、失败处理和成本后，再 fork 并自托管该服务。正式功能要把“AI 裁定、非官方、请以正式裁判/官方资料为准”的免责声明连同来源一并展示。

上游 README 自己也声明项目不是 KONAMI 官方项目、不能替代正式比赛裁判，且可能有错误或误判。[README](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/README.md#L6-L24) [免责声明](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/README.md#L85-L93)

## 当前 hikari-bot 的接入点

hikari-bot 已是 Python 3.10--3.13 的 NoneBot2/OneBot V11 应用，现有依赖包含 `aiohttp` 与 `httpx`，且通过 `hikari_bot/plugins` 自动发现插件。因此初版不需要增加 Python 生产依赖：新增一个独立插件及一个小型 HTTP 客户端即可。[本项目依赖与插件发现配置](../../pyproject.toml) [启动及 OneBot 注册](../../bot.py) [配置加载方式](../../hikari_bot/core/config.py)

建议的本地职责边界：

```text
QQ 用户
  -> NoneBot 命令“裁定 <完整场面和问题>”
  -> hikari-bot 插件：权限、冷却、并发槽、立即确认
  -> 裁定 HTTP 客户端：超时、重试策略、响应规整、短期缓存
  -> OCG Ruling Assistant /api/answer
  -> 插件：短结论、理由、来源链接、风险标记、免责声明
```

不要在 NoneBot 里复刻其卡文/RAG/模型流程，也不要将上游 API key、Relay key、DeepSeek key 发送给 QQ 客户端或写入插件源代码。

## 可用接口及契约

上游公共接口是 `GET /api/answer`（运行能力与可用模型）和 `POST /api/answer`（裁定）。接口实现仅接受 `GET`、`POST`、`OPTIONS`，并配置 CORS。[接口入口](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/api/answer.js#L11-L61)

PoC 请求只发送已公开且稳定的最小字段：

```json
{
  "question": "完整卡名、卡片文本（若新卡）、场面、连锁和需要判断的问题"
}
```

服务端要求 `question` 为非空字符串；请求体最大 64 KiB，问题上限 12,000 字符。[参数校验](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/backend/publicAnswerService.mjs#L26-L98) 源码还读取 `mode`（公开端仅允许 `rag`）、`rulingModelProfile`、`rulingVersion` 与 `engineScenario`；除 `question` 外没有独立 API 文档，hikari-bot 初版不应依赖这些扩展字段。[公共调用路径](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/backend/publicAnswerService.mjs#L163-L199)

成功响应至少应按如下字段渲染：

| 字段 | Bot 展示/处理 |
| --- | --- |
| `shortAnswer` | 先发给用户的结论。 |
| `reasoning` | 有上限地编号列出理由，避免单条 QQ 消息过长。 |
| `usedEvidence` | 展示标题与 `sourceUrl`；来源缺失时不要伪造链接。 |
| `answerLevel`、`confidenceSelfEstimate`、`riskFlags`、`missingInfo` | 明示“官方确认 / 规则分析 / 低置信 / 需要补充资料”等不确定性。 |
| `resolvedCards` | 可作为识别到的卡片清单，不应自行断定它等于用户原意。 |

这些字段由 RAG 管线最终构造；`usedEvidence` 会被规整为包含 `id`、`type`、`title`、`sourceUrl` 的记录。[响应构造](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/backend/ragRulingPipeline.mjs#L364-L417) [结果规整](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/backend/ragRulingPipeline.mjs#L755-L843)

上游部署的 Pages 配置目前指向 `https://ocg-ruling-assistant.vercel.app/api/answer`。[上游 `config.json`](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/config.json#L1-L4) 2026-08-13 实测该地址的 `GET` 返回 `200`，但 `Access-Control-Allow-Origin` 固定为 `https://coldiceh.github.io`。这不阻止 hikari-bot 的服务端请求，却意味着不能把它直接作为另一网页前端的跨域 API；同时它属于作者部署，源码中没有 SLA、对外鉴权或稳定性承诺。

## 时延、成本与滥用防护

这不是适合同步阻塞回复的接口。上游报告中 Sol low 的旧 10 题样本平均 51.6 秒、中位 47.9 秒；报告也特别说明样本不保证泛化正确率。[评测与限制](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/README.en.md#L53-L62) [非保证声明](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/README.en.md#L90-L98) `vercel.json` 给 `/api/answer` 配置的函数上限为 300 秒。[Vercel 配置](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/vercel.json#L3-L11) [函数配置](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/vercel.json#L32-L35)

因此初版应至少具备：

- 先回复“正在查询裁定资料”，将 HTTP 调用放在 matcher 的异步处理内；客户端总超时取 300 秒以上并正确向用户报告网络/503/429/超时，不把失败说成裁定结论。
- 每用户冷却、全局并发槽和群级开关；同一规范化问题做短 TTL 缓存。上游调用会消耗其模型预算，不能成为无配额的公共转发器。
- 限制 QQ 输入到上游 12,000 字符与 64 KiB 以下；不要自动附带群聊历史、QQ 号或其他个人资料。
- 建立最小审计：只保存哈希、时间、成功/失败和耗时；若业务确有需要保存提问内容，先另行确定保存期限与群公告。

## 三种接入选择

| 方案 | 实施内容 | 适用性与边界 |
| --- | --- | --- |
| A. 上游 API PoC | Bot 仅以服务端 HTTP 调用上游 `/api/answer`。 | 最快，但先获得作者许可；上游部署、预算和接口变更均不受本项目控制。仅用于小范围验证。 |
| B. fork + 自托管（推荐生产） | fork 上游，Node 服务作为独立 sidecar；NoneBot 只访问内网/本机 URL。 | 控制模型密钥、预算、升级与限流；需维护 Node、数据同步和模型费用。 |
| C. 复用/重写 RAG 流程 | 将数据与裁定链路深度移植到本项目。 | 维护和验证成本最高；目前没有必要，也不应在首次接入时做。 |

对于 B，最小运行入口是 `pnpm dev:backend`，即 `node backend/server.mjs`；本地监听默认 `127.0.0.1:8787`，提供 `/health`、`/api/engine` 和 `/api/answer`。 [脚本](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/package.json#L20-L35) [本地服务](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/backend/server.mjs#L24-L39) [路由](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/backend/server.mjs#L57-L123)

自托管默认公开裁定 profile 为 Relay GPT-5.6 Sol low；Relay 需要服务端同时设置 `RELAY_API_KEY` 与合法 HTTPS `RELAY_BASE_URL`，资料准备还使用 DeepSeek。环境示例还涵盖 `ALLOWED_ORIGIN`、预算、Upstash 和可选 OCG engine；这些密钥不得进入浏览器配置。 [模型 profile 与可用性](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/backend/publicRulingModelConfig.mjs#L1-L61) [配置条件](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/backend/publicRulingModelConfig.mjs#L82-L135) [环境变量示例](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/.env.example#L1-L36) [预算与持久化配置](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/.env.example#L88-L110) [引擎与 Redis 配置](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/.env.example#L154-L205)

可选 OCG engine 不能放入 Vercel Serverless：上游文档要求 Windows 主机上的 sidecar，并让云端裁定 API 通过 HTTPS + Bearer Token 访问它。[引擎部署边界](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/docs/ocg-engine-integration.md#L159-L210) 初版可保持 engine 关闭；RAG 裁定仍能工作。

## 许可与上线前条件

上游仓库采用 MIT License，可修改、分发和商业使用，但分发源码/副本必须保留版权和许可声明，且软件按“现状”提供、没有担保。[LICENSE](https://github.com/coldiceh/ocg-ruling-assistant/blob/5bd3499141e1f3ccfccd0f1efdcbc4125697edad/LICENSE#L1-L21)

在写入 hikari-bot 代码前仍需要用户决定：

1. 是先采用 A 进行小范围 PoC，还是直接采用 B 自托管？
2. 若采用 A，是否已经获得作者允许 Bot 调用其公共部署接口？
3. 命令名、可使用的群/用户范围、每人冷却/每日额度和最终模型费用由谁承担？

确定后，下一次实现应新增配置项（例如 `RULING_ASSISTANT_API_URL`、`RULING_ASSISTANT_TIMEOUT`、`RULING_ASSISTANT_ENABLED`），客户端、NoneBot 插件和 mock HTTP 测试；不应更改已有卡片查询或 MyCard 功能。
