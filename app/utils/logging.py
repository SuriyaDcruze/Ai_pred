"""Structured application logging.

Uses :mod:`rich` for readable local logs and falls back to plain formatting in
containers. Import :func:`get_logger` everywhere instead of ``logging`` directly.
"""

from __future__ import annotations

import logging
import sys
from functools import lru_cache

from app.config import settings

_CONFIGURED = False


def _configure_root() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    handler: logging.Handler
    # Only use Rich when attached to a real terminal — piped/notebook/CI output
    # makes Rich's renderer throw, so fall back to plain formatting there.
    use_rich = sys.stderr.isatty()
    if use_rich:
        try:
            from rich.logging import RichHandler

            handler = RichHandler(rich_tracebacks=True, show_path=False)
            fmt = "%(message)s"
        except Exception:  # pragma: no cover - defensive
            use_rich = False
    if not use_rich:
        handler = logging.StreamHandler(sys.stdout)
        fmt = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"

    logging.basicConfig(level=level, format=fmt, handlers=[handler], force=True)
    _CONFIGURED = True


@lru_cache
def get_logger(name: str) -> logging.Logger:
    """Return a configured logger for ``name`` (module ``__name__``)."""
    _configure_root()
    return logging.getLogger(name)
