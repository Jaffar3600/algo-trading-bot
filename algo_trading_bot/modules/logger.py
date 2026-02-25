"""
modules/logger.py — Centralised Logging
Provides coloured console output + file logging for every module.
Usage:
    from modules.logger import get_logger
    log = get_logger(__name__)
    log.info("Bot started")
    log.error("Something went wrong")
"""

import logging
import sys
from pathlib import Path
from datetime import datetime

try:
    import colorlog
    HAS_COLOR = True
except ImportError:
    HAS_COLOR = False

# ── Log file path ─────────────────────────────────────────────────────────────
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    """
    Returns a logger with:
    - Coloured console output (if colorlog installed)
    - Daily rotating file output in logs/
    """
    logger = logging.getLogger(name)

    # Avoid adding handlers multiple times
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt_str = "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    # ── Console handler ───────────────────────────────────────────────────────
    if HAS_COLOR:
        color_fmt = (
            "%(log_color)s%(asctime)s | %(levelname)-8s%(reset)s | "
            "%(cyan)s%(name)-30s%(reset)s | %(message)s"
        )
        console_handler = colorlog.StreamHandler(sys.stdout)
        console_handler.setFormatter(colorlog.ColoredFormatter(
            color_fmt,
            datefmt=date_fmt,
            log_colors={
                "DEBUG":    "white",
                "INFO":     "green",
                "WARNING":  "yellow",
                "ERROR":    "red",
                "CRITICAL": "bold_red",
            }
        ))
    else:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(logging.Formatter(fmt_str, datefmt=date_fmt))

    # ── File handler ──────────────────────────────────────────────────────────
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = LOG_DIR / f"algo_bot_{today}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(fmt_str, datefmt=date_fmt))

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger


# ── Convenience: module-level logger ─────────────────────────────────────────
log = get_logger("algo_bot")
