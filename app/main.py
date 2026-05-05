"""Entry point de FastAPI.

Ciclo de vida:
1. Arrancar el navegador con la sesión persistente.
2. Conectar el JobRunner a la cola y arrancar el worker.
3. Arrancar el SessionKeeper.
4. Servir las rutas.
5. Al cierre, parar todo limpio.
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.adapters import get_adapter
from app.config import settings
from app.core.browser import browser_manager
from app.core.queue import Job, job_queue
from app.core.session_keeper import session_keeper
from app.routes import admin, generate, jobs


def configure_logging() -> None:
    logger.remove()
    logger.add(sys.stderr, level=settings.log_level)


async def run_job(job: Job) -> dict[str, Any]:
    """Ejecuta un Job sobre el adapter correspondiente, dentro del browser persistente."""
    adapter = get_adapter(job.generator)
    async with browser_manager.page() as page:
        try:
            result = await adapter.run(page, job.payload)
        except RuntimeError as exc:
            # Heurística: si el adapter detectó redirect a login, marcamos sesión muerta.
            if "sesión" in str(exc).lower() or "session" in str(exc).lower():
                session_keeper.mark_unauthenticated()
            raise
    return {
        "asset_url": result.asset_url,
        "asset_local_path": result.asset_local_path,
        "metadata": result.metadata,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info("Iniciando envautomatico v{}", app.version)
    await browser_manager.start()
    job_queue.set_runner(run_job)
    await job_queue.start()
    await session_keeper.start()
    try:
        yield
    finally:
        logger.info("Apagando envautomatico")
        await session_keeper.stop()
        await job_queue.stop()
        await browser_manager.save_storage_state()
        await browser_manager.stop()


app = FastAPI(
    title="envautomatico",
    version="0.1.0",
    description="API que automatiza la suite de generadores de Envato AI vía Playwright.",
    lifespan=lifespan,
)

# Servir los assets generados desde /files/.
app.mount("/files", StaticFiles(directory=str(settings.storage_dir)), name="files")

# Routers.
app.include_router(admin.router)
app.include_router(generate.router)
app.include_router(jobs.router)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"name": "envautomatico", "version": app.version, "docs": "/docs"}
