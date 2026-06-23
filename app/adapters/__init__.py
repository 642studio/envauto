"""Registro de adapters disponibles, indexado por nombre del generador."""
from app.adapters.base import GeneratorAdapter
from app.adapters.image import ImageGenAdapter
from app.adapters.music import MusicGenAdapter
from app.adapters.sound import SoundGenAdapter
from app.adapters.video import VideoGenAdapter

# Más adapters se agregan acá conforme los implementamos.
ADAPTERS: dict[str, GeneratorAdapter] = {
    "image": ImageGenAdapter(),
    "video": VideoGenAdapter(),
    "sound": SoundGenAdapter(),
    "music": MusicGenAdapter(),
}


def get_adapter(name: str) -> GeneratorAdapter:
    if name not in ADAPTERS:
        raise KeyError(
            f"Generador '{name}' no registrado. Disponibles: {sorted(ADAPTERS)}"
        )
    return ADAPTERS[name]


def available() -> list[str]:
    return sorted(ADAPTERS)
