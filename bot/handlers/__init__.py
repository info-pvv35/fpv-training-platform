from .user import router as user_router
from .admin import router as admin_router
from .payments import router as payments_router
from .voice import router as voice_router

__all__ = [
    "user_router",
    "admin_router",
    "payments_router",
    "voice_router"
]