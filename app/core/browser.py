"""Manager de Playwright con navegador fresco por job.

Mantiene Playwright vivo durante toda la vida del proceso (es barato) pero lanza un
Chromium NUEVO para cada job, con un contexto cargado desde `auth/storage_state.json`,
y lo cierra al terminar (guardando la sesión).

Por qué fresco por job y no un navegador/contexto persistente eterno: un proceso de
navegador de larga vida que acumula muchas generaciones hace que Envato rechace EN
SILENCIO las generaciones de VIDEO (las de imagen siguen andando, y la sesión queda
válida). Se comprobó que recrear solo el contexto NO alcanza: hay que recrear el
navegador entero. Un Chromium fresco por job —serializado por la cola, uno a la vez—
evita el bloqueo y es más robusto.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from loguru import logger
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from app.config import settings


class BrowserManager:
    """Navegador único + contexto fresco por operación.

    Pensado para correr serializado: el job runner toma `page()` para una operación,
    que crea un contexto nuevo, lo usa, guarda la sesión y lo cierra. El navegador
    queda vivo entre operaciones.
    """

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        # Navegador y contexto de la operación en curso (None entre jobs). Se exponen
        # para que save_storage_state() pueda persistir si se llama durante un job.
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """No-op de arranque: cada job crea su propio Playwright en page(). Solo avisa
        si falta la sesión."""
        logger.info("BrowserManager listo (Playwright fresco por job, headless={})", settings.headless)
        if not settings.storage_state_file.exists():
            logger.warning(
                "No se encontró {}. Tenés que correr scripts/login.py primero.",
                settings.storage_state_file,
            )

    async def _new_context(self, browser: Browser) -> BrowserContext:
        """Crea un contexto fresco cargando la sesión de Envato desde disco."""
        storage_state = (
            str(settings.storage_state_file)
            if settings.storage_state_file.exists()
            else None
        )
        context = await browser.new_context(
            storage_state=storage_state,
            viewport={"width": 1440, "height": 900},
            # User-Agent consistente con el OS del contenedor (Ubuntu/Linux).
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
        )
        context.set_default_timeout(settings.nav_timeout_ms)
        context.set_default_navigation_timeout(settings.nav_timeout_ms)
        return context

    async def stop(self) -> None:
        """Cierra lo que haya abierto y Playwright limpiamente."""
        if self._context:
            try:
                await self._context.close()
            except Exception:  # noqa: BLE001
                pass
            self._context = None
        if self._browser:
            try:
                await self._browser.close()
            except Exception:  # noqa: BLE001
                pass
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("Playwright detenido")

    @asynccontextmanager
    async def page(self) -> AsyncIterator[Page]:
        """Lanza un Chromium fresco + contexto + página para una operación y los cierra.

        El lock asegura que solo un job a la vez use el navegador (v1 serial). Al cerrar,
        guarda el storage_state para preservar refreshes de cookies.

        Cada job usa su PROPIA instancia de Playwright + navegador (autocontenido), igual
        que correr un script standalone. Reutilizar una instancia de Playwright de larga
        vida (la del proceso uvicorn) hacía que Envato no completara las generaciones de
        VIDEO; un Playwright fresco por job replica las condiciones que sí funcionan."""
        async with self._lock:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    headless=settings.headless,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                self._browser = browser
                context = await self._new_context(browser)
                self._context = context
                page = await context.new_page()
                try:
                    yield page
                finally:
                    # Persistir la sesión (cookies/localStorage pueden haberse
                    # refrescado) antes de descartar todo.
                    try:
                        await context.storage_state(path=str(settings.storage_state_file))
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("No pude guardar storage_state al cerrar: {}", exc)
                    for closer in (page.close, context.close, browser.close):
                        try:
                            await closer()
                        except Exception:  # noqa: BLE001
                            pass
                    self._context = None
                    self._browser = None

    async def save_storage_state(self) -> None:
        """Guarda el storage_state si hay un contexto activo (durante un job).

        Entre jobs no hay contexto: la persistencia ya ocurre al cerrar cada contexto
        en page(), así que acá simplemente no hay nada que guardar."""
        if self._context is None:
            return
        await self._context.storage_state(path=str(settings.storage_state_file))
        logger.info("storage_state guardado en {}", settings.storage_state_file)

    @property
    def is_authenticated(self) -> bool:
        """Heurística simple: existe el archivo de sesión (el browser se lanza por job)."""
        return settings.storage_state_file.exists()


# Singleton accesible desde routers y adapters.
browser_manager = BrowserManager()
