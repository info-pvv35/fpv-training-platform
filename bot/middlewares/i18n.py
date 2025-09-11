from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from typing import Any, Awaitable, Callable, Dict, Union

class ACLMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Union[Message, CallbackQuery], Dict[str, Any]], Awaitable[Any]],
        event: Union[Message, CallbackQuery],
        data: Dict[str, Any]  # ← ДОБАВЬТЕ ЭТОТ ПАРАМЕТР!
    ) -> Any:
        user = data.get("event_from_user")
        if user:
            # Можно брать язык из БД или оставить "ru" по умолчанию
            data["locale"] = "ru"
        return await handler(event, data)