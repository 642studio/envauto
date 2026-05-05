"""Adapter para Envato AI - imageGen (https://app.envato.com/image-gen).

Mapeo del flujo confirmado contra la UI real (mayo 2026):

1. Navegar a /image-gen.
2. Escribir el prompt en el contenteditable [data-cy="prompt-input"].
3. (Opcional) Configurar aspect ratio, count y style clickeando los comboboxes
   por su texto actual ("Square", "3 Variations", "Style") y eligiendo el item.
4. Click en button[type="submit"] (texto "Generate").
5. La URL cambia a /image-gen/genai-image/{uuid} cuando arrancan las generaciones.
6. Las imágenes finales aparecen como img[alt="Generated Image"] con src en
   gen-assets-resized.envatousercontent.com.
7. La descarga se hace por HTTP directo al src usando las cookies del contexto,
   en vez de pelear con un menú nativo del browser.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger
from playwright.async_api import Page

from app.adapters.base import GenerationResult, GeneratorAdapter
from app.config import settings
from app.core.storage import new_asset_path, public_url


class ImageGenAdapter(GeneratorAdapter):
    name = "image"

    URL = "https://app.envato.com/image-gen"

    # Selectores estables (data-cy + tipos semánticos).
    # SUBMIT_BUTTON: hay versión desktop y mobile renderizadas en paralelo.
    # Filtramos por visible y tomamos el primero para no romper en strict mode.
    PROMPT_INPUT = '[data-cy="prompt-input"]'
    SUBMIT_BUTTON = 'button[type="submit"][data-analytics-name="gen_click"]:visible'
    RESULT_IMAGE = 'img[alt="Generated Image"]'

    # Comboboxes detectados por su texto actual. Se localizan dinámicamente
    # porque el texto cambia con la selección del usuario.
    ASPECT_RATIO_VALUES = {
        "1:1": "Square",
        "16:9": "Landscape",
        "9:16": "Portrait",
        "4:3": "Standard",
        "3:4": "Tall",
    }

    VARIATIONS_VALUES = {1: "1 Variation", 2: "2 Variations", 3: "3 Variations", 4: "4 Variations"}

    # Patrón de URL del job una vez que se dispara la generación.
    JOB_URL_PATTERN = re.compile(r"/image-gen/genai-image/([0-9a-f-]+)")

    # Patrón del src de la imagen final.
    FINAL_SRC_PATTERN = re.compile(r"gen-assets-resized\.envatousercontent\.com|gen-assets\.envatousercontent\.com")

    async def navigate(self, page: Page) -> None:
        logger.info("[image] navegando a {}", self.URL)
        await page.goto(self.URL, wait_until="domcontentloaded")
        if "sign_in" in page.url or "/login" in page.url:
            raise RuntimeError(
                "La sesión de Envato no es válida. Re-correr scripts/login.py."
            )
        # Esperar a que el formulario esté listo.
        await page.locator(self.PROMPT_INPUT).first.wait_for(state="visible")

    async def submit(self, page: Page, payload: dict[str, Any]) -> None:
        prompt: str = payload["prompt"]
        logger.info("[image] enviando prompt ({} chars)", len(prompt))

        # El prompt es un div contenteditable. fill() funciona en Playwright
        # sobre contenteditable también.
        prompt_box = page.locator(self.PROMPT_INPUT).first
        await prompt_box.click()
        await prompt_box.fill(prompt)

        # Opciones soportadas (todas opcionales).
        aspect_ratio = payload.get("aspect_ratio")
        if aspect_ratio in self.ASPECT_RATIO_VALUES:
            await self._set_combobox(page, self.ASPECT_RATIO_VALUES[aspect_ratio])

        variations = payload.get("variations")
        if isinstance(variations, int) and variations in self.VARIATIONS_VALUES:
            await self._set_combobox(page, self.VARIATIONS_VALUES[variations])

        style = payload.get("style")
        if style:
            # El botón "Style" abre un picker; el item se elige por texto exacto.
            await self._open_combobox_by_label(page, "Style")
            await page.get_by_role("option", name=style, exact=False).first.click()

        # Disparar. .first como red de seguridad si :visible matchea más de uno.
        await page.locator(self.SUBMIT_BUTTON).first.click()

    async def _set_combobox(self, page: Page, current_or_target_label: str) -> None:
        """Click en un combobox cuyo texto visible es `current_or_target_label`,
        después click en la opción del mismo nombre.

        Como el texto del botón refleja la selección actual, la primera vez
        clickeamos sobre el valor por defecto, y la segunda vez sobre el deseado.
        Si el valor ya estaba seleccionado no hace falta cambiarlo, así que
        usamos el patrón abrir-y-elegir-target.
        """
        await page.get_by_role("button", name=current_or_target_label).first.click()
        # Esperar al menú emergente y seleccionar.
        try:
            await page.get_by_role("option", name=current_or_target_label).first.click(
                timeout=5_000
            )
        except Exception:  # noqa: BLE001 - ya estaba seleccionado o el label difiere
            await page.keyboard.press("Escape")

    async def _open_combobox_by_label(self, page: Page, label: str) -> None:
        await page.get_by_role("button", name=label).first.click()

    async def wait_for_result(self, page: Page) -> dict[str, Any]:
        logger.info("[image] esperando resultado")

        # Paso 1: esperar a que la URL cambie al detalle del job.
        await page.wait_for_url(
            self.JOB_URL_PATTERN,
            timeout=settings.generation_timeout_ms,
        )
        match = self.JOB_URL_PATTERN.search(page.url)
        job_id = match.group(1) if match else None
        logger.info("[image] job_id Envato: {}", job_id)

        # Paso 2: esperar a que aparezcan las imágenes generadas con src real.
        deadline = settings.generation_timeout_ms / 1000
        elapsed = 0.0
        srcs: list[str] = []
        while elapsed < deadline:
            srcs = await page.evaluate(
                """() => Array.from(document.querySelectorAll('img[alt=\"Generated Image\"]'))
                    .map(i => i.src).filter(Boolean)"""
            )
            ready = [s for s in srcs if self.FINAL_SRC_PATTERN.search(s)]
            if ready:
                return {
                    "envato_job_id": job_id,
                    "image_srcs": ready,
                    "page_url": page.url,
                }
            await asyncio.sleep(2)
            elapsed += 2

        raise TimeoutError(
            f"[image] no aparecieron imágenes finales en {deadline}s. "
            f"srcs vistos: {srcs[:3]}"
        )

    async def download(self, page: Page, meta: dict[str, Any]) -> GenerationResult:
        """Descarga la primera imagen vía HTTP usando las cookies del contexto.

        En vez de pelear con el menú nativo de descarga del navegador, usamos
        page.request, que hereda las cookies del contexto y funciona limpiamente.
        """
        srcs: list[str] = meta["image_srcs"]
        if not srcs:
            raise RuntimeError("[image] meta sin image_srcs")

        # La URL resized tiene un sufijo de tamaño; pedimos el original si existe.
        src = srcs[0]
        original = src.replace("gen-assets-resized.envatousercontent.com", "gen-assets.envatousercontent.com")
        candidates = [original, src]

        target = new_asset_path(self.name, ".png")
        last_error: Exception | None = None
        for candidate in candidates:
            try:
                logger.info("[image] descargando {}", candidate[:120])
                response = await page.request.get(candidate)
                if response.ok:
                    target.write_bytes(await response.body())
                    return GenerationResult(
                        asset_url=public_url(target),
                        asset_local_path=str(target),
                        metadata={
                            **meta,
                            "downloaded_from": candidate,
                            "all_image_srcs": srcs,
                        },
                    )
                last_error = RuntimeError(f"HTTP {response.status} en {candidate}")
            except Exception as exc:  # noqa: BLE001
                last_error = exc

        raise RuntimeError(f"[image] no pude descargar: {last_error}")
