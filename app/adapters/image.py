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

    # Etiquetas en inglés y español para los comboboxes.
    # Cada ratio/cantidad mapea a una tupla de posibles textos según el idioma de la UI.
    ASPECT_RATIO_VALUES: dict[str, tuple[str, ...]] = {
        "1:1":  ("Square", "Cuadrado"),
        "16:9": ("Landscape", "Panorámico", "Horizontal"),
        "9:16": ("Portrait", "Retrato", "Vertical"),
        "4:3":  ("Standard", "Estándar"),
        "3:4":  ("Tall", "Alto"),
    }

    VARIATIONS_VALUES: dict[int, tuple[str, ...]] = {
        1: ("1 Variation",  "1 Variación"),
        2: ("2 Variations", "2 Variaciones"),
        3: ("3 Variations", "3 Variaciones"),
        4: ("4 Variations", "4 Variaciones"),
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
            await self._change_combobox(
                page,
                candidates=self._all_aspect_ratio_labels(),
                target_labels=self.ASPECT_RATIO_VALUES[aspect_ratio],
            )

        variations = payload.get("variations")
        if isinstance(variations, int) and variations in self.VARIATIONS_VALUES:
            await self._change_combobox(
                page,
                candidates=self._all_variations_labels(),
                target_labels=self.VARIATIONS_VALUES[variations],
            )
            self._expected_count = variations
        else:
            self._expected_count = 1

        style = payload.get("style")
        if style:
            await self._change_combobox(
                page,
                candidates=["Style", "Estilo"],
                target_labels=(style,),
                exact_option=False,
            )

        await page.locator(self.SUBMIT_BUTTON).first.click()

    def _all_aspect_ratio_labels(self) -> list[str]:
        return [label for labels in self.ASPECT_RATIO_VALUES.values() for label in labels]

    def _all_variations_labels(self) -> list[str]:
        return [label for labels in self.VARIATIONS_VALUES.values() for label in labels]

    async def _change_combobox(
        self,
        page: Page,
        candidates: list[str],
        target_labels: tuple[str, ...],
        exact_option: bool = True,
    ) -> None:
        """Abre un combobox probando cada label candidato (idioma-agnóstico),
        luego selecciona la opción objetivo.

        `candidates` son los posibles textos del botón en el estado actual
        (para abrirlo). `target_labels` son los posibles textos de la opción
        a seleccionar (en distintos idiomas).
        """
        opened = False
        for label in candidates:
            try:
                btn = page.get_by_role("button", name=label, exact=True).first
                if await btn.count() > 0:
                    await btn.click(timeout=2_000)
                    opened = True
                    break
            except Exception:  # noqa: BLE001
                continue

        if not opened:
            logger.warning("[image] no pude abrir combobox (candidatos: {})", candidates[:4])
            return

        for label in target_labels:
            try:
                opt = page.get_by_role("option", name=label, exact=exact_option).first
                if await opt.count() > 0:
                    await opt.click(timeout=3_000)
                    return
            except Exception:  # noqa: BLE001
                continue

        logger.warning("[image] no pude seleccionar opción (targets: {})", target_labels)
        await page.keyboard.press("Escape")

    async def wait_for_result(self, page: Page) -> dict[str, Any]:
        logger.info("[image] esperando resultado")

        # Esperar a que la URL cambie al detalle del job (puede ser breve).
        await page.wait_for_url(
            self.JOB_URL_PATTERN,
            timeout=settings.generation_timeout_ms,
        )
        match = self.JOB_URL_PATTERN.search(page.url)
        job_id = match.group(1) if match else None
        logger.info("[image] job_id Envato: {}", job_id)

        # Esperar a que aparezcan TODAS las imágenes de las variaciones solicitadas.
        # Estrategia: devolver cuando tengamos `_expected_count` imágenes listas,
        # o cuando el conteo no cambie durante `_STABLE_CYCLES_REQUIRED` × 2 s.
        deadline = settings.generation_timeout_ms / 1000
        elapsed = 0.0
        ready: list[str] = []
        stable_cycles = 0

        while elapsed < deadline:
            srcs = await page.evaluate(
                """() => Array.from(document.querySelectorAll('img[alt="Generated Image"]'))
                    .map(i => i.src).filter(Boolean)"""
            )
            new_ready = [s for s in srcs if self.FINAL_SRC_PATTERN.search(s)]

            if new_ready:
                if len(new_ready) >= self._expected_count:
                    logger.info("[image] {} imagen(es) listas (esperadas: {})",
                                len(new_ready), self._expected_count)
                    return {"envato_job_id": job_id, "image_srcs": new_ready, "page_url": page.url}

                if len(new_ready) == len(ready):
                    stable_cycles += 1
                    if stable_cycles >= self._STABLE_CYCLES_REQUIRED:
                        logger.info("[image] {} imagen(es) estable(s) tras {}s de espera",
                                    len(new_ready), stable_cycles * 2)
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
            f"[image] no aparecieron imágenes finales en {deadline}s. "
            f"srcs vistos: {srcs[:3]}"
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
