# src/interfaces/telegram/bot.py

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

from src.interfaces.telegram.handlers.positions import router as positions_router
from src.interfaces.telegram.handlers.settings import router as settings_router
from src.interfaces.telegram.handlers.start import router as start_router
from src.interfaces.telegram.handlers.status import router as status_router
from src.interfaces.telegram.middleware import AuthMiddleware, ContainerMiddleware

logger = structlog.get_logger(__name__)


def create_bot(token: str) -> Bot:
    """
    Crea la instancia del Bot con Markdown V2 como parse mode default.
    """
    return Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2),
    )


def create_dispatcher(redis: Redis, container=None) -> Dispatcher:
    """
    Crea el Dispatcher con FSM storage en Redis.
    FSM en Redis permite que los estados de conversación
    sobrevivan reinicios del bot.

    Si se provee un container, se instala ContainerMiddleware
    para que los handlers puedan acceder a los servicios del bot.
    """
    storage = RedisStorage(redis=redis)
    dp      = Dispatcher(storage=storage)

    # Middleware de autorización — aplica a TODOS los handlers
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())

    # Middleware de inyección del container (después de auth)
    if container is not None:
        dp.message.middleware(ContainerMiddleware(container))
        dp.callback_query.middleware(ContainerMiddleware(container))
        logger.info("container_middleware_installed")

    # Registra todos los routers
    dp.include_router(start_router)
    dp.include_router(status_router)
    dp.include_router(positions_router)
    dp.include_router(settings_router)

    logger.info("telegram_dispatcher_created")
    return dp


async def start_polling(bot: Bot, dp: Dispatcher) -> None:
    """Arranca el polling del bot. Bloqueante hasta que se cancele."""
    logger.info("telegram_polling_started")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
