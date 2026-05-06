"""Registro de adapters disponibles, indexado por nombre del generador."""
from app.adapters.base import GeneratorAdapter
from app.adapters.graphics import GraphicsGenAdapter
from app.adapters.image import ImageGenAdapter
from app.adapters.music import MusicGenAdapter
from app.adapters.video import VideoGenAdapter

ADAPTERS: dict[str, GeneratorAdapter] = {
    "graphics": GraphicsGenAdapter(),
    "image": ImageGenAdapter(),
    "music": MusicGenAdapter(),
    "video": VideoGenAdapter(),
}


def get_adapter(name: str) -> GeneratorAdapter:
    if name not in ADAPTERS:
        raise KeyError(
            f"Generador '{name}' no registrado. Disponibles: {sorted(ADAPTERS)}"
        )
    return ADAPTERS[name]


def available() -> list[str]:
    return sorted(ADAPTERS)
