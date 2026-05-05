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
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from app.config import settings


@dataclass
class GenerationResult:
    """Lo que devuelve un adapter al terminar."""

    asset_url: str               # URL pública servida por nuestra API
    asset_local_path: str        # Path absoluto en disco
    metadata: dict[str, Any]     # Lo que sea relevante (prompt, opciones, IDs internos)


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
            await self.submit(page, payload)
            meta = await self.wait_for_result(page)
            return await self.download(page, meta)
        except Exception:
            await self._dump_debug(page)
            raise

    async def dismiss_cookiebot_if_present(self, page: Page) -> bool:
        """Cierra Cookiebot si está bloqueando la UI.

        Es idempotente: si el modal no existe o ya está cerrado, no hace nada.
        """
        dialog = page.locator("#CybotCookiebotDialog")
        if await dialog.count() == 0:
            return False

        active = page.locator("#CybotCookiebotDialog.CybotCookiebotDialogActive")
        target = active if await active.count() > 0 else dialog

        if not await target.first.is_visible():
            return False

        logger.info("[{}] Cookiebot detectado, intentando cerrarlo", self.name)

        candidates = (
            page.locator("#CybotCookiebotDialogBodyButtonDecline"),
            page.get_by_role("button", name="Reject all", exact=False),
            page.get_by_role("button", name="Reject All", exact=False),
            page.get_by_role("button", name="Accept all", exact=False),
        )

        for button in candidates:
            try:
                if not await button.first.is_visible():
                    continue
                await button.first.click(timeout=3_000)
                await target.first.wait_for(state="hidden", timeout=5_000)
                logger.info("[{}] Cookiebot cerrado", self.name)
                return True
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue

        try:
            await page.keyboard.press("Escape")
            await target.first.wait_for(state="hidden", timeout=2_000)
            logger.info("[{}] Cookiebot cerrado con Escape", self.name)
            return True
        except Exception:
            logger.warning("[{}] Cookiebot sigue visible tras intentos de cierre", self.name)
            return False

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
