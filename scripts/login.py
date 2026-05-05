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

from playwright.async_api import async_playwright  # noqa: E402

from app.config import settings  # noqa: E402


BANNER = """
================================================================
  envautomatico - login interactivo
================================================================
Te voy a abrir un Chromium real con Envato. Hacé login completo
(usuario, contraseña, 2FA si tenés) y cuando estés DENTRO de tu
cuenta, volvé a esta terminal y presioná ENTER.

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
        context = await browser.new_context(storage_state=storage)
        page = await context.new_page()
        await page.goto(settings.envato_login_url)

        # Esperar a que el usuario confirme.
        await asyncio.get_event_loop().run_in_executor(
            None, input, "\n>>> Cuando hayas terminado el login, presioná ENTER acá: "
        )

        await context.storage_state(path=str(settings.storage_state_file))
        print(f"\n[ok] Sesión guardada en {settings.storage_state_file}")
        print(
            "Ahora subí ese archivo al VPS:\n"
            f"  scp {settings.storage_state_file} usuario@vps:/ruta/envautomatico/auth/\n"
            "O usá el endpoint POST /admin/storage-state."
        )
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
