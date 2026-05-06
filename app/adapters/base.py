"""Contrato común para todos los adapters de generadores Envato AI.

Cada generador (image, video, music, voice, sound, graphics, mockup) implementa
los cuatro métodos del ciclo de vida. El JobRunner los llama en orden.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from loguru import logger
from playwright.async_api import Page

from app.config import settings


@dataclass
class GenerationResult:
    """Lo que devuelve un adapter al terminar."""

    asset_urls: list[str]           # URLs públicas (una por variación generada)
    asset_local_paths: list[str]    # Paths en disco correspondientes
    metadata: dict[str, Any]

    @property
    def asset_url(self) -> str:
        return self.asset_urls[0] if self.asset_urls else ""

    @property
    def asset_local_path(self) -> str:
        return self.asset_local_paths[0] if self.asset_local_paths else ""


class GeneratorAdapter(ABC):
    """Plantilla del ciclo de vida de una generación."""

    name: str  # se setea en cada subclase

    @abstractmethod
    async def navigate(self, page: Page) -> None:
        """Llevar la página a la pantalla del generador correspondiente."""

    @abstractmethod
    async def submit(self, page: Page, payload: dict[str, Any]) -> None:
        """Llenar el formulario y disparar la generación."""

    @abstractmethod
    async def wait_for_result(self, page: Page) -> dict[str, Any]:
        """Esperar a que el resultado esté listo. Devuelve metadata interna."""

    @abstractmethod
    async def download(self, page: Page, meta: dict[str, Any]) -> GenerationResult:
        """Descargar el asset al disco y devolver paths/URLs."""

    async def run(self, page: Page, payload: dict[str, Any]) -> GenerationResult:
        """Ejecuta el ciclo completo. Si falla, guarda screenshot + HTML para debug."""
        try:
            await self.navigate(page)
            await self._dismiss_cookie_banner(page)
            await self.submit(page, payload)
            meta = await self.wait_for_result(page)
            return await self.download(page, meta)
        except Exception:
            await self._dump_debug(page)
            raise

    async def _dismiss_cookie_banner(self, page: Page) -> None:
        """Descarta el banner de cookies/privacidad si está presente.

        Elige la opción más privada (Reject all) automáticamente.
        No falla si no hay banner.
        """
        # Cookiebot — Envato usa este proveedor. El script carga de forma asíncrona
        # desde un CDN externo, por eso esperamos hasta 6 s a que el botón aparezca.
        try:
            btn = page.locator("#CybotCookiebotDialogBodyButtonDecline")
            await btn.wait_for(state="visible", timeout=6_000)
            await btn.click(timeout=3_000)
            logger.info("[{}] banner Cookiebot descartado (Reject all)", self.name)
            await page.wait_for_timeout(400)
            return
        except Exception:  # noqa: BLE001
            pass

        # Fallback genérico para otros banners
        candidates = [
            'button:has-text("Reject all")',
            'button:has-text("Rechazar todo")',
            'button:has-text("Reject")',
            'button:has-text("Decline")',
        ]
        for selector in candidates:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible():
                    await btn.click(timeout=3_000)
                    logger.info("[{}] banner de cookies descartado", self.name)
                    await page.wait_for_timeout(300)
                    return
            except Exception:  # noqa: BLE001
                continue

    async def _dump_debug(self, page: Page) -> None:
        """Guarda screenshot + HTML + URL al fallar el job, para inspeccionar después."""
        debug_dir = settings.storage_dir / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        prefix = f"{self.name}-{ts}"
        try:
            await page.screenshot(
                path=str(debug_dir / f"{prefix}.png"),
                full_page=True,
                timeout=10_000,
            )
            html = await page.content()
            (debug_dir / f"{prefix}.html").write_text(html, encoding="utf-8")
            (debug_dir / f"{prefix}.url.txt").write_text(page.url, encoding="utf-8")
            logger.warning(
                "[{}] debug guardado: /files/debug/{}.png (también .html y .url.txt)",
                self.name, prefix,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[{}] no pude guardar debug: {}", self.name, exc)
