from .deck import router as deck_router
from .sms import router as sms_router
from .cr_upload import router as cr_upload_router
from .uptime_webhook import router as uptime_webhook_router

routers = [deck_router, sms_router, cr_upload_router, uptime_webhook_router]