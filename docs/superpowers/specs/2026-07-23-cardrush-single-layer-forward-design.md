# Cardrush 单层合并转发设计

## 背景

Cardrush 图报当前将每一页作为独立 QQ 图片消息发送。页数增加时，接收方会连续收到
多条消息，聊天窗口较杂。

Git 历史中的 FAQ 裁定功能使用了中转群，但它解决的是两层转发：先在中转群生成每条
Q&A 的内层合并转发并取得 `message_id`，再把这些引用节点组成外层合并转发。Cardrush
只需要一层合并转发，不需要引用已经存在的消息。

当前 OneBot V11 适配器提供
`MessageSegment.node_custom(user_id, nickname, content)`，其中 `content` 可以直接是
包含图片段的 `Message`。因此每页图报可以直接成为一个自定义节点。

## 目标

- 一份 Cardrush QQ 图报只产生一条合并转发消息。
- 每一页图片作为合并转发中的一个节点，并标记当前页码和总页数。
- 手动图报在私聊中发送私聊合并转发，在群聊中发送群聊合并转发。
- 自动日报给每位管理员各发送一条私聊合并转发。
- 自动日报同时向 `PUBLIC_GROUP_ID` 配置的群发送一条群聊合并转发。
- 不使用中转群，不增加群号配置，也不产生中转群垃圾消息。
- 保留 200KB WebP 压缩、页面顺序和 B 站原图发布。
- 最终合并转发返回 `retcode=1200` 时静默视为结果未知但允许继续；其他错误照常抛出。

## 结构

新增 `hikari_bot/plugins/monitors/cardrush_forward.py`，作为 OneBot 专用适配器：

```python
async def send_qq_forward(
    bot: Bot,
    pages: Sequence[bytes],
    *,
    user_id: int | None = None,
    group_id: int | None = None,
    log_prefix: str,
) -> bool:
    ...
```

调用方必须且只能提供 `user_id` 或 `group_id` 之一。函数将每页编码为
`base64://` 图片段，构造昵称为 `Cardrush 图报 1/N`、`Cardrush 图报 2/N` 等的
自定义节点，然后只调用一次：

- 私聊：`send_private_forward_msg`
- 群聊：`send_group_forward_msg`

返回 `True` 表示 API 明确成功，返回 `False` 表示 API 返回 1200、结果未知但已静默
处理。业务调用方不根据该返回值向聊天追加错误提示。

`cardrush_delivery.py` 继续只负责压缩。1200 分类器作为合并转发适配器的私有实现，
避免导入 `monitors` 包时触发其 `__init__.py` 中的插件注册副作用。旧的逐页
`send_qq_pages` 在两个调用方迁移后删除。

## 数据流

手动图报：

```text
渲染原图 → QQ WebP 压缩 → 每页构造自定义节点
→ 一次群聊/私聊合并转发 → 完成提示
```

自动日报：

```text
渲染原图 → QQ WebP 压缩
→ 每位管理员一次私聊合并转发
→ `PUBLIC_GROUP_ID` 一次群聊合并转发
→ B 站继续使用原图发布
```

## 错误处理

- 节点构造失败、目标参数错误、非 1200 API 错误继续抛出。
- 1200 只在最终的一次合并转发调用边界静默处理并写内部日志。
- 不重试 1200，避免合并转发实际已送达时重复出现。
- 因为没有中转消息，所以不存在“已经上传但拿不到 `message_id`”的问题。

## 测试

- 两页私聊图报只调用一次 `send_private_forward_msg`，包含两个按顺序排列的图片节点。
- 群聊目标只调用一次 `send_group_forward_msg`。
- 使用真实 OneBot `ActionFailed` 验证 1200 被静默处理，非 1200 原样抛出。
- 源码契约确认手动与自动路径均使用合并转发，B 站仍使用未压缩原图。
- 运行全量 pytest、compileall、改动文件 pyflakes 和插件加载检查。
