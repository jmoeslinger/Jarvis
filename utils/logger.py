import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config.settings import LOG_DIR


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("jarvis")
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        fmt="[%(asctime)s]  %(levelname)-8s  %(name)-28s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # stdout auf UTF-8 zwingen (Windows-Konsole nutzt sonst cp1252)
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "jarvis.log"
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"jarvis.{name}")
