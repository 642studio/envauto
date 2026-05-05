"""Helpers de autenticación para el endpoint admin.

El flujo principal es: el usuario corre `scripts/login.py` localmente, que abre
un navegador headed, espera a que termine el login (incluyendo 2FA) y guarda
auth/storage_state.json. Para el VPS, ese archivo se sube vía SCP o vía el
endpoint POST /admin/storage-state que usa este módulo.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.config import settings


def write_storage_state(content: bytes) -> Path:
    """Escribe el contenido recibido como nuevo storage_state.json.

    Valida que sea JSON parseable antes de sobreescribir el archivo activo.
    """
    parsed = json.loads(content)
    if not isinstance(parsed, dict) or "cookies" not in parsed:
        raise ValueError("El archivo no tiene forma de storage_state de Playwright")

    settings.auth_dir.mkdir(parents=True, exist_ok=True)
    settings.storage_state_file.write_bytes(content)
    return settings.storage_state_file
