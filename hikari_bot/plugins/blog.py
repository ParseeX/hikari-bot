import asyncio

from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.permission import SUPERUSER

from hikari_bot.core.commands import on_cmd
from hikari_bot.core.config import settings

update_blog = on_cmd("更新博客", aliases={"blog"}, permission=SUPERUSER)

@update_blog.handle()
async def _(bot: Bot, event: MessageEvent):
    script = settings.blog_update_script
    if not script:
        await bot.send(event=event, message="未配置 BLOG_UPDATE_SCRIPT。")
        return
    if not script.is_file():
        await bot.send(event=event, message=f"博客更新脚本不存在：{script}")
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            "bash", str(script),
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
    script = settings.blog_deploy_script
    if not script:
        await bot.send(event=event, message="未配置 BLOG_DEPLOY_SCRIPT。")
        return
    if not script.is_file():
        await bot.send(event=event, message=f"博客发布脚本不存在：{script}")
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            "bash", str(script),
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
