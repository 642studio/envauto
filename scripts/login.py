"""Login interactivo - se corre UNA VEZ desde tu máquina local.

Abre un Chromium real (no headless), te lleva a Envato, y te deja loguearte a
mano (incluyendo 2FA). Cuando ves que ya estás dentro, presionás Enter en la
terminal. El script guarda auth/storage_state.json con todas las cookies y
localStorage. Después subís ese archivo al VPS.

Uso:
    cd envautomatico
    python -m playwright install chromium    # solo la primera vez
    python scripts/login.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Permite correr el script directamente sin instalar el paquete.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import Error as PlaywrightError, async_playwright  # noqa: E402

from app.config import settings  # noqa: E402


BANNER = """
================================================================
  envautomatico - login interactivo
================================================================
Te voy a abrir un Chromium real con Envato. Hacé login completo
(usuario, contraseña, 2FA si tenés). Después abrí esta URL en el
mismo browser y verificá que carga sin pedir login:
  https://app.envato.com/image-gen

Cuando la veas abierta, volvé a esta terminal y presioná ENTER.

No cierres el navegador antes de presionar ENTER acá.
================================================================
"""


async def main() -> None:
    print(BANNER)

    settings.auth_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        # Si ya hay un storage_state previo, lo cargamos para que solo refresque.
        storage = (
            str(settings.storage_state_file)
            if settings.storage_state_file.exists()
            else None
        )
        # Debe matchear el contexto del VPS para evitar invalidaciones por fingerprint.
        context = await browser.new_context(
            storage_state=storage,
            viewport={
                "width": settings.browser_viewport_width,
                "height": settings.browser_viewport_height,
            },
            user_agent=settings.browser_user_agent,
            locale=settings.browser_locale,
            timezone_id=settings.browser_timezone_id,
        )
        page = await context.new_page()
        await page.goto(settings.envato_login_url)

        # Esperar a que el usuario confirme.
        await asyncio.get_event_loop().run_in_executor(
            None, input, "\n>>> Cuando hayas terminado el login, presioná ENTER acá: "
        )

        # Validación: la sesión tiene que servir también para app.envato.com.
        try:
            await page.goto("https://app.envato.com/image-gen", wait_until="domcontentloaded")
        except PlaywrightError as exc:
            print(
                "\n[error] El navegador o la pestaña se cerró antes de validar la sesión.\n"
                "Mantené Chromium abierto hasta que termine el script."
            )
            await browser.close()
            raise SystemExit(1) from exc
        if "sign_in" in page.url or "/login" in page.url:
            print(
                "\n[error] Envato sigue pidiendo login en app.envato.com/image-gen.\n"
                "Repetí el proceso y asegurate de abrir manualmente image-gen en el browser\n"
                "antes de presionar ENTER."
            )
            await browser.close()
            raise SystemExit(1)

        try:
            await context.storage_state(
                path=str(settings.storage_state_file),
                indexed_db=True,
            )
        except TypeError:
            await context.storage_state(path=str(settings.storage_state_file))
            print("[warn] Tu versión de Playwright no soporta indexed_db en storage_state.")
        print(f"\n[ok] Sesión guardada en {settings.storage_state_file}")
        print(
            "Ahora subí ese archivo al VPS:\n"
            f"  scp {settings.storage_state_file} usuario@vps:/ruta/envautomatico/auth/\n"
            "O usá el endpoint POST /admin/storage-state."
        )
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
