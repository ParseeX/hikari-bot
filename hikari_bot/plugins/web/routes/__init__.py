from .deck import router as deck_router
from .sms import router as sms_router
from .uptime_webhook import router as uptime_webhook_router

routers = [deck_router, sms_router, uptime_webhook_router]
