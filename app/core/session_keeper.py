"""Mantiene viva y persistida la sesión de Envato.

Tareas en background:
1. Guardar `storage_state.json` periódicamente para no perder refreshes silenciosos
   de cookies que Envato hace mientras navegamos.
2. Hacer un ping ligero a Envato cada cierto tiempo para que la cookie no muera
   por inactividad.
3. Validar que seguimos logueados; si no, marcar la sesión como expirada para
   que /health la exponga.
"""
from __future__ import annotations

import asyncio

from loguru import logger

from app.config import settings
from app.core.browser import browser_manager


class SessionKeeper:
    """Loop en background que cuida la sesión persistente."""

    PING_EVERY_S = 60 * 60 * 6      # 6 h:  navegar a Envato para mantener viva la sesión
    LOGIN_MARKERS = ("sign_in", "login")

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._authenticated: bool = settings.storage_state_file.exists()

    @property
    def authenticated(self) -> bool:
        return self._authenticated

    def mark_unauthenticated(self) -> None:
        """Llamado por adapters cuando detectan redirección a login."""
        if self._authenticated:
            logger.warning("Sesión de Envato marcada como expirada")
        self._authenticated = False

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="session-keeper")
            logger.info("SessionKeeper iniciado")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        last_ping = 0.0
        while True:
            await asyncio.sleep(60)
            now = asyncio.get_event_loop().time()

            # NO guardamos storage_state acá: cada job ya lo persiste al cerrar su
            # contexto en BrowserManager.page(). Hacerlo desde este loop llamaría a
            # context.storage_state() CONCURRENTEMENTE sobre el contexto del job en
            # curso, lo que rompía las generaciones de video (que duran >60s). Las de
            # imagen no se veían afectadas porque terminan antes del primer tick.

            # Ping de keepalive: abre una pestaña, navega, valida sesión. Usa page(),
            # que toma el lock, así que nunca corre durante un job.
            if now - last_ping >= self.PING_EVERY_S:
                await self._ping()
                last_ping = now

    async def _ping(self) -> None:
        """Navega al home de Envato AI y revisa si seguimos logueados."""
        try:
            async with browser_manager.page() as page:
                await page.goto(settings.envato_ai_home, wait_until="domcontentloaded")
                if any(m in page.url for m in self.LOGIN_MARKERS):
                    self.mark_unauthenticated()
                else:
                    self._authenticated = True
                    logger.debug("Keepalive ok")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Keepalive falló: {}", exc)


session_keeper = SessionKeeper()
