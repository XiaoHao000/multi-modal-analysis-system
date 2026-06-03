import sys
import os
from loguru import logger

# Remove default handler
logger.remove()

# Production: structured JSON to stdout (for container logs → ELK/Loki/Datadog)
# Development: colorized console output
_is_prod = os.getenv("DATABASE_URL", "").startswith("postgresql") or os.getenv("ENV", "") == "production"

if _is_prod:
    logger.add(
        sys.stdout,
        format="{time} {level} {name}:{line} {message}",
        serialize=True,
        level="INFO",
    )
else:
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}:{line}</cyan> | <level>{message}</level>",
        level="INFO",
        colorize=True,
    )

# File: JSON-structured for log aggregation (ELK/Loki)
logger.add(
    "logs/app_{time:YYYY-MM-DD}.json",
    format="{time} {level} {name}:{line} {message}",
    serialize=True,
    rotation="10 MB",
    retention="7 days",
    level="DEBUG",
)

__all__ = ["logger"]
