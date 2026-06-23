"""Login interactivo por VNC con auto-detección (sin presionar Enter).

Pensado para correr DENTRO del contenedor de login (display virtual Xvfb + x11vnc).
Abre un Chromium headed con la página de login de Envato. El usuario se conecta por
VNC y hace el login completo (Google SSO + 2FA). Un loop en segundo plano verifica,
cada pocos segundos y sin molestar la pestaña del login, si la app ya carga logueada;
cuando lo detecta, guarda auth/storage_state.json y termina.

Como el navegador es el Chromium Linux del contenedor (mismo fingerprint que el de
producción), la sesión capturada SÍ valida en el servicio principal.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright  # noqa: E402

from app.config import settings  # noqa: E402

POLL_EVERY_S = 12
MAX_WAIT_S = 30 * 60  # 30 min para hacer el login con calma

BANNER = """
================================================================
  LOGIN ENVATO POR VNC (auto-detección)
================================================================
  En el Chromium que ves por VNC, hacé el login COMPLETO de
  Envato (Google SSO + 2FA). No hace falta tocar nada más:
  cuando detecte que estás dentro, guardo la sesión solo.
================================================================
"""


async def main() -> None:
    print(BANNER, flush=True)
    settings.auth_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        )
        storage = (
            str(settings.storage_state_file)
            if settings.storage_state_file.exists()
            else None
        )
        context = await browser.new_context(storage_state=storage, no_viewport=True)

        page = await context.new_page()
        await page.goto(settings.envato_login_url)

        checker = await context.new_page()
        elapsed = 0
        saved = False
        while elapsed < MAX_WAIT_S:
            await asyncio.sleep(POLL_EVERY_S)
            elapsed += POLL_EVERY_S
            try:
                await checker.goto(settings.envato_ai_home, wait_until="domcontentloaded")
                await asyncio.sleep(2)
                logged_in = (
                    "sign_in" not in checker.url
                    and "/login" not in checker.url
                    and "google/authenticate" not in checker.url
                    and await checker.locator('[data-cy="prompt-input"]').count() > 0
                )
                if logged_in:
                    await context.storage_state(path=str(settings.storage_state_file))
                    data = json.loads(settings.storage_state_file.read_text())
                    app_cookies = [c for c in data.get("cookies", []) if "app.envato.com" in c.get("domain", "")]
                    origins = [o.get("origin") for o in data.get("origins", [])]
                    print(
                        f"[ok] sesión Linux guardada en {settings.storage_state_file}\n"
                        f"     cookies app.envato.com: {len(app_cookies)} | localStorage: {origins or 'NINGUNO'}",
                        flush=True,
                    )
                    saved = True
                    break
                else:
                    print(f"[..] todavía no logueado ({elapsed}s) — segui con el login en el VNC", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[..] chequeo falló ({exc}); reintento", flush=True)

        if not saved:
            print("[ERROR] no detecté login dentro del tiempo límite. No guardé nada.", flush=True)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
