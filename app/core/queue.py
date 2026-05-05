"""Cola de jobs en memoria.

v1: una sola cola asyncio, un único worker que ejecuta los jobs en orden.
Esto evita pelearse con la UI del navegador desde dos lados a la vez.
Si más adelante necesitamos paralelismo, multiplicamos contextos de browser.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from loguru import logger

JobStatus = Literal["queued", "running", "completed", "failed"]


@dataclass
class Job:
    """Estado y resultado de un trabajo de generación."""

    id: str
    generator: str
    payload: dict[str, Any]
    status: JobStatus = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


# Tipo del callable que ejecuta un job: recibe el Job, devuelve un dict con el resultado.
JobRunner = Callable[[Job], Awaitable[dict[str, Any]]]


class JobQueue:
    """Cola FIFO con un único worker. Thread-safe dentro del event loop."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None
        self._runner: JobRunner | None = None

    def set_runner(self, runner: JobRunner) -> None:
        """Inyecta el callable que sabe cómo ejecutar cada job."""
        self._runner = runner

    async def start(self) -> None:
        """Lanza el worker en background."""
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker(), name="job-worker")
            logger.info("Job worker iniciado")

    async def stop(self) -> None:
        """Cancela el worker."""
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

    def submit(self, generator: str, payload: dict[str, Any]) -> Job:
        """Crea un job, lo encola y devuelve la referencia."""
        job = Job(id=uuid.uuid4().hex, generator=generator, payload=payload)
        self._jobs[job.id] = job
        self._queue.put_nowait(job.id)
        logger.info("Job {} encolado para {}", job.id, generator)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return list(self._jobs.values())

    async def _worker(self) -> None:
        """Loop infinito: toma un job_id, lo corre, persiste el resultado."""
        while True:
            job_id = await self._queue.get()
            job = self._jobs.get(job_id)
            if job is None:
                continue
            if self._runner is None:
                job.status = "failed"
                job.error = "No hay runner configurado"
                continue

            job.status = "running"
            job.started_at = time.time()
            logger.info("Ejecutando job {} ({})", job.id, job.generator)
            try:
                result = await self._runner(job)
                job.result = result
                job.status = "completed"
            except Exception as exc:  # noqa: BLE001 - capturamos todo a propósito
                logger.exception("Job {} falló: {}", job.id, exc)
                job.error = f"{type(exc).__name__}: {exc}"
                job.status = "failed"
            finally:
                job.finished_at = time.time()


job_queue = JobQueue()
