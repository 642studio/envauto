"""Endpoints para encolar generaciones."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.adapters import available, get_adapter
from app.core.queue import job_queue
from app.models.schemas import GenerateRequest, GeneratorName, JobView
from app.routes.security import require_token

router = APIRouter(prefix="/generate", tags=["generate"], dependencies=[Depends(require_token)])


@router.post("/{generator}", response_model=JobView, status_code=202)
async def generate(generator: GeneratorName, body: GenerateRequest) -> JobView:
    """Encola un trabajo de generación. Devuelve el job para que el cliente haga polling."""
    try:
        get_adapter(generator)
    except KeyError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Generador desconocido. Disponibles: {available()}",
        ) from exc

    job = job_queue.submit(
        generator=generator,
        payload={"prompt": body.prompt, **body.params},
    )
    return JobView(
        id=job.id,
        generator=job.generator,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        result=job.result,
        error=job.error,
    )
