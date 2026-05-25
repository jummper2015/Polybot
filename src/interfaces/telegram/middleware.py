# src/interfaces/telegram/middleware.py

import os
from typing import Any, Awaitable, Callable

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = structlog.get_logger(__name__)


class AuthMiddleware(BaseMiddleware):
    """
    Middleware que autoriza solo el TELEGRAM_CHAT_ID configurado.
    Cualquier otro usuario que intente usar el bot es ignorado silenciosamente.
    Aplica a mensajes Y a callback queries (botones inline).
    """

    def __init__(self):
        self._allowed_chat_id = int(os.environ["TELEGRAM_CHAT_ID"])

    async def __call__(
        self,
        handler:  Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event:    TelegramObject,
        data:     dict[str, Any],
    ) -> Any:
        # Extrae el chat_id del evento (mensaje o callback)
        if isinstance(event, Message):
            chat_id = event.chat.id
        elif isinstance(event, CallbackQuery):
            chat_id = event.message.chat.id
        else:
            return  # Tipo de evento no conocido → ignorar

        if chat_id != self._allowed_chat_id:
            logger.warning(
                "unauthorized_telegram_access",
                chat_id=chat_id,
                allowed=self._allowed_chat_id,
            )
            return  # Silenciosamente ignorado — no responde al intruso

        # Usuario autorizado → continúa con el handler
        return await handler(event, data)


class ContainerMiddleware(BaseMiddleware):
    """
    Middleware que inyecta el Container en el diccionario 'data' de aiogram.
    Esto permite que los handlers reciban el container vía parámetro:

        @router.message(Command("status"))
        async def cmd_status(message: Message, container=None) -> None:
            ...

    El container se instala DESPUÉS del AuthMiddleware en la cadena.
    """

    def __init__(self, container):
        super().__init__()
        self._container = container

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event:   TelegramObject,
        data:    dict[str, Any],
    ) -> Any:
        data["container"] = self._container
        return await handler(event, data)
