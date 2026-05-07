import asyncio

from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.permission import SUPERUSER

from hikari_bot.core.commands import on_cmd

DEPLOY_SCRIPT = "/home/xyk/blog/deploy.sh"
UPDATE_SCRIPT = "/home/xyk/blog/update.sh"

update_blog = on_cmd("更新博客", aliases={"blog"}, permission=SUPERUSER)

@update_blog.handle()
async def _(bot: Bot, event: MessageEvent):
    try:
        proc = await asyncio.create_subprocess_exec(
            "bash", UPDATE_SCRIPT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode(errors="replace").strip()
        if proc.returncode == 0:
            msg = "博客更新成功！"
            if output:
                msg += f"\n{output}"
        else:
            msg = f"博客更新失败（退出码 {proc.returncode}）"
            if output:
                msg += f"\n{output}"
    except Exception as e:
        msg = f"执行部署脚本时出错：{e}"
    await bot.send(event=event, message=msg)


deploy_blog = on_cmd("发布", aliases={"deploy"}, permission=SUPERUSER)
@deploy_blog.handle()
async def _(bot: Bot, event: MessageEvent):
    try:
        proc = await asyncio.create_subprocess_exec(
            "bash", DEPLOY_SCRIPT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode(errors="replace").strip()
        if proc.returncode == 0:
            msg = "发布成功！"
        else:
            msg = f"发布失败（{proc.returncode}）"
            if output:
                msg += f"\n{output}"
    except Exception as e:
        msg = f"执行部署脚本时出错：{e}"
    await bot.send(event=event, message=msg)