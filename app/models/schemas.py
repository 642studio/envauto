"""Schemas Pydantic para request/response."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


GeneratorName = Literal[
    "image",
    "video",
    "music",
    "voice",
    "sound",
    "graphics",
    "mockup",
]


class GenerateRequest(BaseModel):
    """Body común para POST /generate/{generador}.

    `params` deja espacio a opciones específicas de cada generador
    (aspect_ratio, voice_id, duration_seconds, etc.) sin tener que
    versionar el schema cada vez que aparece un campo nuevo.
    """

    prompt: str = Field(..., min_length=1, max_length=4000)
    params: dict[str, Any] = Field(default_factory=dict)


class JobView(BaseModel):
    """Vista pública de un Job."""

    id: str
    generator: str
    status: str
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class HealthResponse(BaseModel):
    status: str
    authenticated: bool
    generators: list[str]
