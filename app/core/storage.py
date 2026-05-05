"""Helpers para guardar assets descargados y construir URLs públicas."""
from __future__ import annotations

import uuid
from pathlib import Path

from app.config import settings


def new_asset_path(generator: str, suffix: str) -> Path:
    """Construye un path único bajo storage/{generator}/{uuid}{.suffix}."""
    folder = settings.storage_dir / generator
    folder.mkdir(parents=True, exist_ok=True)
    if not suffix.startswith("."):
        suffix = "." + suffix
    name = f"{uuid.uuid4().hex}{suffix}"
    return folder / name


def public_url(path: Path) -> str:
    """Devuelve la URL pública desde donde la API sirve este archivo."""
    rel = path.relative_to(settings.storage_dir)
    return f"{settings.public_base_url.rstrip('/')}/files/{rel.as_posix()}"
