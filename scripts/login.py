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

        # Verificar que la sesión sirve PARA LA APP, no solo para account.envato.com.
        # Navegamos a la app real y comprobamos que el prompt-input cargue. Esto, además,
        # fuerza a que el contexto reciba las cookies + localStorage de app.envato.com,
        # que es lo que el VPS necesita y lo que faltaba en capturas anteriores.
        print(f"\nVerificando que la sesión sea válida en {settings.envato_ai_home} ...")
        await page.goto(settings.envato_ai_home, wait_until="domcontentloaded")
        await asyncio.sleep(3)
        if "sign_in" in page.url or "/login" in page.url or "google/authenticate" in page.url:
            print(
                "\n[ERROR] La app sigue pidiendo login (URL: "
                f"{page.url}).\n"
                "No guardé nada. Hacé el login COMPLETO (incluyendo entrar a la app) "
                "y volvé a apretar ENTER, o cerrá y reintentá."
            )
            await asyncio.get_event_loop().run_in_executor(
                None, input, ">>> Entrá a la app manualmente y presioná ENTER para reintentar: "
            )
            await page.goto(settings.envato_ai_home, wait_until="domcontentloaded")
            await asyncio.sleep(3)
            if "sign_in" in page.url or "/login" in page.url or "google/authenticate" in page.url:
                print(f"[ABORT] Sigue sin sesión válida (URL: {page.url}). No guardé nada.")
                await browser.close()
                return

        has_prompt = await page.locator('[data-cy="prompt-input"]').count()
        print(f"[ok] Sesión válida en la app. prompt-input detectado: {has_prompt}")

        await context.storage_state(path=str(settings.storage_state_file))
        # Sanity check del archivo guardado: tiene que tener cookies de app.envato.com.
        import json as _json
        saved = _json.loads(settings.storage_state_file.read_text())
        app_cookies = [c for c in saved.get("cookies", []) if "app.envato.com" in c.get("domain", "")]
        ls_origins = [o.get("origin") for o in saved.get("origins", [])]
        print(f"[ok] Sesión guardada en {settings.storage_state_file}")
        print(f"     cookies app.envato.com: {len(app_cookies)} | localStorage origins: {ls_origins or 'NINGUNO'}")
        if not app_cookies and not ls_origins:
            print("     [WARN] No se capturaron cookies/localStorage de la app. "
                  "La sesión podría no servir en el VPS.")
        print(
            "Ahora subí ese archivo al VPS:\n"
            f"  scp {settings.storage_state_file} usuario@vps:/ruta/envautomatico/auth/\n"
            "O usá el endpoint POST /admin/storage-state."
        )
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
