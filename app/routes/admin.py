"""Endpoints administrativos: subir storage_state, health.

El upload de storage_state evita tener que entrar al VPS por SSH cuando
queremos refrescar la sesión de Envato: corres login.py en local y haces
un curl/PUT con el JSON resultante.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.adapters import available
from app.core import auth as auth_helpers
from app.core.session_keeper import session_keeper
from app.models.schemas import HealthResponse
from app.routes.security import require_token

router = APIRouter(tags=["admin"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health pública: dice si la API está viva y si la sesión de Envato es válida."""
    return HealthResponse(
        status="ok",
        authenticated=session_keeper.authenticated,
        generators=available(),
    )


@router.post(
    "/admin/storage-state",
    dependencies=[Depends(require_token)],
    status_code=204,
)
async def upload_storage_state(file: UploadFile = File(...)) -> None:
    """Sube un nuevo storage_state.json (lo que devuelve scripts/login.py).

    Después de subir, hay que reiniciar el contenedor para que el browser tome la
    nueva sesión. En una iteración siguiente podemos hacer hot-reload.
    """
    try:
        content = await file.read()
        auth_helpers.write_storage_state(content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
