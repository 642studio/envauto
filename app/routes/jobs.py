"""Endpoints para consultar el estado de los jobs."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.queue import job_queue
from app.models.schemas import JobView
from app.routes.security import require_token

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_token)])


def _to_view(job) -> JobView:
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


@router.get("", response_model=list[JobView])
async def list_jobs() -> list[JobView]:
    return [_to_view(j) for j in job_queue.list()]


@router.get("/{job_id}", response_model=JobView)
async def get_job(job_id: str) -> JobView:
    job = job_queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return _to_view(job)
