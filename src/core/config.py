# src/core/config.py

import os

import structlog
from dotenv import load_dotenv

from src.infrastructure.security.secure_config import SecureConfig

logger = structlog.get_logger(__name__)


def load_config() -> SecureConfig:
    """
    Carga variables de entorno desde .env y construye SecureConfig.
    Es el primer paso del bootstrap — todo lo demás depende de esto.
    Si falla, el proceso termina con código de salida 1.
    """
    # Carga .env si existe (en producción las vars vienen del entorno directamente)
    env_file = os.path.join(os.getcwd(), ".env")
    if os.path.exists(env_file):
        load_dotenv(env_file, override=True)   # .env es fuente de verdad en dev local
        logger.info("env_file_loaded", path=env_file)
    else:
        logger.info("no_env_file_found", note="usando variables de entorno del sistema")

    try:
        config = SecureConfig.from_env()
        logger.info("config_loaded", **config.safe_repr())
        return config

    except EnvironmentError as e:
        logger.error("config_load_failed", error=str(e))
        raise SystemExit(1)
