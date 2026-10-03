"""Structured logging setup for antcrew-platform."""
from __future__ import annotations

import contextvars
import json
import logging
import os

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id_var.get()
        if rid:
            d["request_id"] = rid
        return json.dumps(d)


def _setup_logging() -> None:
    level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    handler = logging.StreamHandler()
    if os.environ.get("LOG_FORMAT", "json").lower() == "json":
        handler.setFormatter(_JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
