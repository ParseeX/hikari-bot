from nonebot import get_app

from .routes import routers

app = get_app()

for router in routers:
    app.include_router(router, prefix="")
