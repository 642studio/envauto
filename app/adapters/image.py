"""Adapter para Envato AI - imageGen (https://app.envato.com/image-gen).

Mapeo del flujo confirmado contra la UI real (mayo 2026):

1. Navegar a /image-gen.
2. Escribir el prompt en el contenteditable [data-cy="prompt-input"].
3. (Opcional) Configurar aspect ratio, count y style clickeando los comboboxes.
4. Click en button[type="submit"] (texto "Generate").
5. La URL cambia brevemente a /image-gen/genai-image/{uuid} y vuelve a /image-gen.
6. Las imágenes finales aparecen como img[alt="Generated Image"] con src en
   gen-assets-resized.envatousercontent.com.
7. La descarga se hace por HTTP directo al src usando las cookies del contexto.
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

    PROMPT_INPUT = '[data-cy="prompt-input"]'
    SUBMIT_BUTTON = 'button[type="submit"][data-analytics-name="gen_click"]:visible'
    RESULT_IMAGE = 'img[alt="Generated Image"]'

    # Selectores data-cy para los chips (botones que abren el dropdown).
    ASPECT_RATIO_CHIP    = '[data-cy="aspect-ratio-chip"]'
    ASPECT_RATIO_DROPDOWN = '[data-cy="aspect-ratio-dropdown"]'
    VARIATIONS_CHIP      = '[data-cy="variations-chip"]'
    VARIATIONS_DROPDOWN  = '[data-cy="variations-dropdown"]'

    # Texto exacto de las opciones dentro de cada dropdown (UI en español).
    ASPECT_RATIO_VALUES: dict[str, str] = {
        "1:1":  "Cuadrado",
        "16:9": "Horizontal",
        "9:16": "Vertical",
        "4:3":  "Estándar",
        "3:4":  "Alto",
    }

    VARIATIONS_VALUES: dict[int, str] = {
        1: "1 Variación",
        2: "2 Variaciones",
        3: "3 Variaciones",
        4: "4 Variaciones",
    }

    JOB_URL_PATTERN = re.compile(r"/image-gen/genai-image/([0-9a-f-]+)")
    FINAL_SRC_PATTERN = re.compile(
        r"gen-assets-resized\.envatousercontent\.com|gen-assets\.envatousercontent\.com"
    )

    # Cuántos ciclos de 2 s sin cambio de cantidad se esperan antes de asumir
    # que ya cargaron todas las variaciones.
    _STABLE_CYCLES_REQUIRED = 3  # 6 segundos

    def __init__(self) -> None:
        self._expected_count: int = 1

    async def navigate(self, page: Page) -> None:
        logger.info("[image] navegando a {}", self.URL)
        await page.goto(self.URL, wait_until="domcontentloaded")
        if "sign_in" in page.url or "/login" in page.url:
            raise RuntimeError(
                "La sesión de Envato no es válida. Re-correr scripts/login.py."
            )
        await page.locator(self.PROMPT_INPUT).first.wait_for(state="visible")

    async def submit(self, page: Page, payload: dict[str, Any]) -> None:
        prompt: str = payload["prompt"]
        logger.info("[image] enviando prompt ({} chars)", len(prompt))

        prompt_box = page.locator(self.PROMPT_INPUT).first
        await prompt_box.click()
        await prompt_box.fill(prompt)

        aspect_ratio = payload.get("aspect_ratio")
        if aspect_ratio in self.ASPECT_RATIO_VALUES:
            await self._select_dropdown(
                page,
                chip=self.ASPECT_RATIO_CHIP,
                dropdown=self.ASPECT_RATIO_DROPDOWN,
                option_text=self.ASPECT_RATIO_VALUES[aspect_ratio],
            )

        variations = payload.get("variations")
        if isinstance(variations, int) and variations in self.VARIATIONS_VALUES:
            await self._select_dropdown(
                page,
                chip=self.VARIATIONS_CHIP,
                dropdown=self.VARIATIONS_DROPDOWN,
                option_text=self.VARIATIONS_VALUES[variations],
            )
            self._expected_count = variations
        else:
            self._expected_count = 1

        style = payload.get("style")
        if style:
            await self._select_dropdown(
                page,
                chip='[data-cy="style-chip"]',
                dropdown='[data-cy="style-dropdown"]',
                option_text=style,
                exact=False,
            )

        # Capturar las imágenes existentes antes de generar para filtrarlas después.
        self._baseline_srcs: set[str] = set(await page.evaluate(
            """() => Array.from(document.querySelectorAll('img[alt="Generated Image"]'))
                .map(i => i.src).filter(Boolean)"""
        ))
        await page.locator(self.SUBMIT_BUTTON).first.click()

    async def _select_dropdown(
        self,
        page: Page,
        chip: str,
        dropdown: str,
        option_text: str,
        exact: bool = True,
    ) -> None:
        """Abre el dropdown clickeando el chip y selecciona la opción por texto.

        Antes de abrir verifica si el chip ya muestra el valor deseado para
        evitar un toggle que deseleccionaría el valor actual.
        """
        chip_loc = page.locator(chip).first
        try:
            current = await chip_loc.inner_text(timeout=3_000)
        except Exception:  # noqa: BLE001
            current = ""

        if option_text.strip().lower() in current.strip().lower():
            logger.debug("[image] '{}' ya seleccionado en {}, skip", option_text, chip)
            return

        try:
            await chip_loc.click(timeout=5_000)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[image] no pude abrir dropdown {}: {}", chip, exc)
            return

        dropdown_loc = page.locator(dropdown)
        try:
            await dropdown_loc.wait_for(state="visible", timeout=4_000)
        except Exception:  # noqa: BLE001
            pass

        try:
            option = dropdown_loc.locator("button").filter(has_text=option_text).first
            await option.click(timeout=5_000)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[image] no pude seleccionar '{}' en {}: {}", option_text, dropdown, exc)
            await page.keyboard.press("Escape")

    async def wait_for_result(self, page: Page) -> dict[str, Any]:
        logger.info("[image] esperando resultado")

        # El URL del job (si aparece) es metadata opcional; no bloqueamos en él.
        job_id: str | None = None
        try:
            await page.wait_for_url(self.JOB_URL_PATTERN, timeout=15_000)
            match = self.JOB_URL_PATTERN.search(page.url)
            job_id = match.group(1) if match else None
        except Exception:  # noqa: BLE001
            pass  # Envato puede no cambiar la URL en generaciones multi-variación
        logger.info("[image] job_id Envato: {}", job_id)

        # Esperar imágenes NUEVAS (no estaban antes del click en Generate).
        # Estrategia: devolver cuando tengamos `_expected_count` nuevas imágenes,
        # o cuando el conteo nuevo no cambie durante `_STABLE_CYCLES_REQUIRED` × 2 s.
        baseline = getattr(self, "_baseline_srcs", set())
        deadline = settings.generation_timeout_ms / 1000
        elapsed = 0.0
        ready: list[str] = []
        stable_cycles = 0

        while elapsed < deadline:
            all_srcs = await page.evaluate(
                """() => Array.from(document.querySelectorAll('img[alt="Generated Image"]'))
                    .map(i => i.src).filter(Boolean)"""
            )
            new_ready = [
                s for s in all_srcs
                if self.FINAL_SRC_PATTERN.search(s) and s not in baseline
            ]

            if new_ready:
                if len(new_ready) >= self._expected_count:
                    logger.info("[image] {} imagen(es) nuevas listas", len(new_ready))
                    return {"envato_job_id": job_id, "image_srcs": new_ready, "page_url": page.url}

                if len(new_ready) == len(ready):
                    stable_cycles += 1
                    if stable_cycles >= self._STABLE_CYCLES_REQUIRED:
                        logger.info("[image] {} imagen(es) estables tras espera", len(new_ready))
                        return {"envato_job_id": job_id, "image_srcs": new_ready, "page_url": page.url}
                else:
                    ready = new_ready
                    stable_cycles = 0

            await asyncio.sleep(2)
            elapsed += 2

        if ready:
            logger.warning("[image] timeout — devolviendo {} imagen(es) parciales", len(ready))
            return {"envato_job_id": job_id, "image_srcs": ready, "page_url": page.url}

        raise TimeoutError(
            f"[image] no aparecieron imágenes nuevas en {deadline}s. "
            f"baseline={len(baseline)} imgs previas."
        )

    async def download(self, page: Page, meta: dict[str, Any]) -> GenerationResult:
        """Descarga todas las imágenes vía HTTP usando las cookies del contexto."""
        srcs: list[str] = meta["image_srcs"]
        if not srcs:
            raise RuntimeError("[image] meta sin image_srcs")

        asset_urls: list[str] = []
        asset_local_paths: list[str] = []

        for src in srcs:
            original = src.replace(
                "gen-assets-resized.envatousercontent.com",
                "gen-assets.envatousercontent.com",
            )
            target = new_asset_path(self.name, ".png")
            downloaded = False
            for candidate in (original, src):
                try:
                    logger.info("[image] descargando {}", candidate[:100])
                    response = await page.request.get(candidate)
                    if response.ok:
                        target.write_bytes(await response.body())
                        asset_local_paths.append(str(target))
                        asset_urls.append(public_url(target))
                        downloaded = True
                        break
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[image] error descargando {}: {}", candidate[:80], exc)

            if not downloaded:
                logger.warning("[image] no pude descargar {}", src[:80])

        if not asset_urls:
            raise RuntimeError("[image] no se pudo descargar ninguna imagen")

        return GenerationResult(
            asset_urls=asset_urls,
            asset_local_paths=asset_local_paths,
            metadata={
                **meta,
                "downloaded_urls": asset_urls,
                "all_image_srcs": srcs,
            },
        )
