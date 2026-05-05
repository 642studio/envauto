"""Endpoints administrativos: subir storage_state, health.

El upload de storage_state evita tener que entrar al VPS por SSH cuando
queremos refrescar la sesión de Envato: corres login.py en local y haces
un curl/PUT con el JSON resultante.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.adapters import available
from app.config import settings
from app.core import auth as auth_helpers
from app.core.browser import browser_manager
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
    """Sube un nuevo storage_state.json y recarga el contexto del browser en caliente.

    No es necesario reiniciar el contenedor: el contexto se recrea con las
    nuevas cookies inmediatamente después de guardar el archivo.
    """
    try:
        content = await file.read()
        auth_helpers.write_storage_state(content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await browser_manager.reload_context()
    session_keeper._authenticated = True


@router.post(
    "/admin/reload-session",
    dependencies=[Depends(require_token)],
    status_code=204,
)
async def reload_session() -> None:
    """Recarga el contexto del browser con el storage_state.json que ya está en disco.

    Útil cuando el archivo fue subido por SCP y no se quiere reiniciar el contenedor.
    """
    await browser_manager.reload_context()
    session_keeper._authenticated = settings.storage_state_file.exists()
