from .deck import router as deck_router
from .sms import router as sms_router
from .cr_upload import router as cr_upload_router

routers = [deck_router, sms_router, cr_upload_router]