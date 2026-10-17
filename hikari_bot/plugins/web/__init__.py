from nonebot import get_app, get_driver

from .routes import routers

app = get_app()

for router in routers:
    app.include_router(router, prefix="")

# 与 Bot 同生命周期；页面访问不会触发上游刷新。
from .routes.mycard import front_stats, series_stats

app.state.mc_series_cache = series_stats


@get_driver().on_startup
async def start_front_cache():
    series_stats.start()
    front_stats.start()


@get_driver().on_shutdown
async def stop_front_cache():
    await front_stats.close()
    await series_stats.close()
