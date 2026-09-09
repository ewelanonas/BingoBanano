"""Shared dependencies para sa API layer."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

_WEB_ROOT = Path(__file__).resolve().parent.parent / "web"

TEMPLATES_DIR = _WEB_ROOT / "templates"
STATIC_DIR = _WEB_ROOT / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
