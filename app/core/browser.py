"""Manager de Playwright con contexto persistente.

Mantiene un único navegador y un único contexto durante toda la vida del proceso.
El contexto se crea con `storage_state` cargado desde `auth/storage_state.json`
para reutilizar la sesión de Envato.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from loguru import logger
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from app.config import settings


class BrowserManager:
    """Mantiene un único navegador + contexto reutilizable.

    Pensado para correr serializado: el job runner toma `page()` para una operación,
    el navegador queda vivo entre operaciones y la sesión se preserva.
    """

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Arranca Playwright, lanza el browser y crea el contexto persistente."""
        if self._playwright is not None:
            return

        logger.info("Iniciando Playwright (headless={})", settings.headless)
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=settings.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )

        storage_state = (
            str(settings.storage_state_file)
            if settings.storage_state_file.exists()
            else None
        )
        if storage_state is None:
            logger.warning(
                "No se encontró {}. Tenés que correr scripts/login.py primero.",
                settings.storage_state_file,
            )

        self._context = await self._browser.new_context(
            storage_state=storage_state,
            viewport={
                "width": settings.browser_viewport_width,
                "height": settings.browser_viewport_height,
            },
            # Mantener el mismo fingerprint base entre login local y VPS.
            user_agent=settings.browser_user_agent,
            locale=settings.browser_locale,
            timezone_id=settings.browser_timezone_id,
        )
        self._context.set_default_timeout(settings.nav_timeout_ms)
        self._context.set_default_navigation_timeout(settings.nav_timeout_ms)

    async def stop(self) -> None:
        """Cierra navegador y Playwright limpiamente."""
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("Playwright detenido")

    @asynccontextmanager
    async def page(self) -> AsyncIterator[Page]:
        """Context manager que abre una página, la usa exclusivamente y la cierra.

        El lock asegura que solo un job a la vez controle el navegador (v1 serial).
        """
        async with self._lock:
            if self._context is None:
                await self.start()
            assert self._context is not None
            page = await self._context.new_page()
            try:
                yield page
            finally:
                await page.close()

    async def save_storage_state(self) -> None:
        """Guarda el estado actual a auth/storage_state.json.

        Incluye IndexedDB cuando la versión de Playwright lo soporta para
        preservar sesiones que guardan credenciales fuera de cookies/localStorage.
        """
        if self._context is None:
            return
        try:
            await self._context.storage_state(
                path=str(settings.storage_state_file),
                indexed_db=True,
            )
        except TypeError:
            await self._context.storage_state(path=str(settings.storage_state_file))
            logger.warning(
                "Playwright sin soporte indexed_db en storage_state; guardando sin IndexedDB"
            )
        logger.info("storage_state guardado en {}", settings.storage_state_file)

    @property
    def is_authenticated(self) -> bool:
        """Heurística simple: existe el archivo de sesión y el contexto está cargado."""
        return settings.storage_state_file.exists() and self._context is not None


# Singleton accesible desde routers y adapters.
browser_manager = BrowserManager()
