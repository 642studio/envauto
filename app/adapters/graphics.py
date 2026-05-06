"""Adapter para Envato AI - graphicsGen (https://app.envato.com/graphics-gen).

Flujo confirmado contra la UI real (mayo 2026):

1. Navegar a /graphics-gen.
2. (Opcional) Adjuntar hasta 3 imágenes de referencia con el botón "+".
3. Escribir el prompt en el contenteditable [data-cy="prompt-input"].
4. (Opcional) Configurar Estilo, Variaciones (1|3), Forma (Cuadrado/Horizontal/Vertical)
   y Fondo (Sólido/Transparente).
5. Click en "Generar +" → button[type="submit"][data-analytics-name="gen_click"].
6. Las imágenes generadas aparecen como img[alt="Generated Image"] en el CDN
   gen-assets-resized.envatousercontent.com (igual que image-gen).
7. Descarga HTTP directa usando las cookies del contexto.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import Page

from app.adapters.base import GenerationResult, GeneratorAdapter
from app.config import settings
from app.core.storage import new_asset_path, public_url


class GraphicsGenAdapter(GeneratorAdapter):
    name = "graphics"

    URL = "https://app.envato.com/graphics-gen"

    PROMPT_INPUT  = '[data-cy="prompt-input"]'
    SUBMIT_BUTTON = 'button[type="submit"][data-analytics-name="gen_click"]:visible'
    RESULT_IMAGE  = 'img[alt="Generated Image"]'

    # ── chips de opciones ────────────────────────────────────────────────────
    # Intentamos data-cy primero (igual que image-gen) y caemos a texto si no existe.
    STYLE_CHIP       = '[data-cy="style-chip"]'
    STYLE_DROPDOWN   = '[data-cy="style-dropdown"]'
    VARIATIONS_CHIP  = '[data-cy="variations-chip"]'
    VARIATIONS_DROPDOWN = '[data-cy="variations-dropdown"]'
    # La forma (cuadrado/horizontal/vertical) puede llamarse "aspect-ratio" o "shape".
    SHAPE_CHIP       = '[data-cy="aspect-ratio-chip"]'
    SHAPE_DROPDOWN   = '[data-cy="aspect-ratio-dropdown"]'

    # ── valores de opciones ──────────────────────────────────────────────────
    SHAPE_VALUES: dict[str, list[str]] = {
        "cuadrado":   ["Square",    "Cuadrado"],
        "horizontal": ["Landscape", "Horizontal"],
        "vertical":   ["Portrait",  "Vertical"],
    }

    VARIATIONS_VALUES: dict[int, list[str]] = {
        1: ["1 Variation",  "1 Variación"],
        3: ["3 Variations", "3 Variaciones"],
    }

    # ── fondo (toggle directo, igual que "Sin letra / Con letra" en music) ───
    BACKGROUND_SOLID_TEXTS       = ["Sólido",      "Solid"]
    BACKGROUND_TRANSPARENT_TEXTS = ["Transparente", "Transparent"]

    # ── detección del resultado (mismo CDN que image-gen) ────────────────────
    JOB_URL_PATTERN = re.compile(r"/graphics-gen/genai-graphic[s]?/([0-9a-f-]+)")
    FINAL_SRC_PATTERN = re.compile(
        r"gen-assets-resized\.envatousercontent\.com|gen-assets\.envatousercontent\.com"
    )

    # Graphics-gen genera variaciones secuencialmente (~25-40 s por variación).
    # Con 20 ciclos de 2 s = 40 s de estabilidad nos aseguramos de esperar
    # el tiempo suficiente para todas las variaciones antes de declarar "listo".
    _STABLE_CYCLES_REQUIRED = 20  # 40 s

    def __init__(self) -> None:
        self._expected_count: int = 3
        self._baseline_srcs: set[str] = set()

    # ─────────────────────────────────── ciclo de vida ───────────────────────

    async def navigate(self, page: Page) -> None:
        logger.info("[graphics] navegando a {}", self.URL)
        await page.goto(self.URL, wait_until="domcontentloaded")
        if "sign_in" in page.url or "/login" in page.url:
            raise RuntimeError(
                "La sesión de Envato no es válida. Re-correr scripts/login.py."
            )
        await page.locator(self.PROMPT_INPUT).first.wait_for(state="visible")

        # Diagnóstico: registrar botones visibles para depuración de selectores
        try:
            btns = await page.evaluate("""() =>
                Array.from(document.querySelectorAll('button'))
                    .filter(b => b.offsetParent !== null)
                    .map(b => ({
                        cy: b.getAttribute('data-cy') || '',
                        an: b.getAttribute('data-analytics-name') || '',
                        text: b.innerText.trim().slice(0, 40),
                        type: b.type,
                    }))
                    .filter(b => b.cy || b.text)
                    .slice(0, 35)
            """)
            logger.debug("[graphics] botones: {}", btns)
        except Exception:  # noqa: BLE001
            pass

        await asyncio.sleep(1.0)

    async def submit(self, page: Page, payload: dict[str, Any]) -> None:
        # 1. Adjuntar imágenes de referencia (opcional, hasta 3)
        images = payload.get("images") or []
        if images:
            await self._attach_images(page, list(images)[:3])

        # 2. Escribir el prompt
        prompt: str = payload.get("prompt", "")
        if prompt:
            logger.info("[graphics] enviando prompt ({} chars)", len(prompt))
            prompt_box = page.locator(self.PROMPT_INPUT).first
            await prompt_box.click()
            await prompt_box.fill(prompt)

        # 3. Estilo (dropdown, igual que image-gen)
        style = payload.get("style")
        if style:
            await self._select_chip_dropdown(
                page, self.STYLE_CHIP, self.STYLE_DROPDOWN, [style], exact=False
            )

        # 4. Variaciones (1 o 3)
        variations = int(payload.get("variations", 3))
        if variations in self.VARIATIONS_VALUES:
            await self._select_chip_dropdown(
                page, self.VARIATIONS_CHIP, self.VARIATIONS_DROPDOWN,
                self.VARIATIONS_VALUES[variations]
            )
            self._expected_count = variations
        else:
            self._expected_count = 3

        # 5. Forma (cuadrado / horizontal / vertical)
        shape = str(payload.get("shape", "cuadrado")).lower()
        if shape in self.SHAPE_VALUES:
            await self._select_chip_dropdown(
                page, self.SHAPE_CHIP, self.SHAPE_DROPDOWN,
                self.SHAPE_VALUES[shape]
            )

        # 6. Fondo (sólido / transparente) — toggle directo
        background = str(payload.get("background", "solido")).lower()
        if background in ("transparente", "transparent"):
            await self._set_background(page, want_transparent=True)
        else:
            await self._set_background(page, want_transparent=False)

        # 7. Baseline ANTES del click para filtrar imágenes previas
        self._baseline_srcs = set(await page.evaluate(
            """() => Array.from(document.querySelectorAll('img[alt="Generated Image"]'))
                .map(i => i.src).filter(Boolean)"""
        ))

        await page.locator(self.SUBMIT_BUTTON).first.click()

    # ─────────────────────────────────── helpers ─────────────────────────────

    async def _attach_images(self, page: Page, image_paths: list[str]) -> None:
        """Adjunta imágenes de referencia via el botón '+' del toolbar."""
        # Primero intentamos encontrar un input[type="file"] directamente
        file_input = page.locator('input[type="file"]').first
        try:
            if await file_input.count() > 0:
                valid = [p for p in image_paths if Path(p).exists()]
                if valid:
                    await file_input.set_input_files(valid)
                    logger.info("[graphics] {} imagen(es) adjuntada(s) via input", len(valid))
                    await asyncio.sleep(1.0)
                    return
        except Exception:  # noqa: BLE001
            pass

        # Fallback: click en el botón "+" y esperamos el file-chooser
        add_btn = None
        for selector in [
            '[data-cy="add-image-button"]',
            'button[aria-label*="image" i]',
            'button[aria-label*="imagen" i]',
            'button:has-text("+")',
        ]:
            try:
                loc = page.locator(selector).first
                if await loc.count() > 0:
                    add_btn = loc
                    break
            except Exception:  # noqa: BLE001
                continue

        if add_btn is None:
            logger.warning("[graphics] no encontré botón '+' para adjuntar imágenes")
            return

        valid = [p for p in image_paths if Path(p).exists()]
        if not valid:
            logger.warning("[graphics] ninguna ruta de imagen válida: {}", image_paths)
            return

        try:
            async with page.expect_file_chooser(timeout=5_000) as fc_info:
                await add_btn.click()
            fc = await fc_info.value
            await fc.set_files(valid)
            logger.info("[graphics] {} imagen(es) adjuntada(s) via file-chooser", len(valid))
            await asyncio.sleep(1.5)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[graphics] error adjuntando imágenes: {}", exc)

    async def _select_chip_dropdown(
        self,
        page: Page,
        chip_sel: str,
        dropdown_sel: str,
        option_texts: list[str],
        exact: bool = True,
    ) -> None:
        """Abre un chip por data-cy y selecciona la opción por texto.

        Si el chip no tiene data-cy, busca por texto del chip como fallback.
        """
        chip_loc = page.locator(chip_sel).first

        # Fallback text-based si data-cy no existe en esta página
        if await chip_loc.count() == 0:
            for text in option_texts:
                fb = page.locator("button:visible").filter(has_text=text).first
                if await fb.count() > 0:
                    logger.info("[graphics] chip '{}' ya activo, skip", text)
                    return
            logger.warning("[graphics] chip {} no encontrado", chip_sel)
            return

        try:
            current = (await chip_loc.inner_text(timeout=3_000)).strip().lower()
        except Exception:  # noqa: BLE001
            current = ""

        if any(t.strip().lower() in current for t in option_texts):
            logger.info("[graphics] '{}' ya seleccionado, skip", option_texts[0])
            return

        try:
            await chip_loc.click(timeout=5_000)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[graphics] no pude abrir chip {}: {}", chip_sel, exc)
            return

        await asyncio.sleep(0.4)

        dropdown_loc = page.locator(dropdown_sel)
        # Si el dropdown data-cy no existe, buscar opciones visibles
        if await dropdown_loc.count() == 0:
            dropdown_loc = page.locator("ul:visible, [role='listbox']:visible, [role='menu']:visible").first

        for text in option_texts:
            try:
                option = dropdown_loc.locator("button, li, [role='option']").filter(
                    has_text=text
                ).first
                if await option.count() > 0:
                    await option.click(timeout=3_000, force=True)
                    logger.info("[graphics] '{}' seleccionado", text)
                    return
            except Exception:  # noqa: BLE001
                continue

        logger.warning("[graphics] no encontré opción {} en dropdown", option_texts)
        await page.keyboard.press("Escape")

    async def _set_background(self, page: Page, want_transparent: bool) -> None:
        """Alterna el botón Sólido/Transparente al estado deseado."""
        target_texts   = self.BACKGROUND_TRANSPARENT_TEXTS if want_transparent else self.BACKGROUND_SOLID_TEXTS
        opposite_texts = self.BACKGROUND_SOLID_TEXTS       if want_transparent else self.BACKGROUND_TRANSPARENT_TEXTS

        all_bg_texts = self.BACKGROUND_SOLID_TEXTS + self.BACKGROUND_TRANSPARENT_TEXTS
        btn = None
        for text in all_bg_texts:
            loc = page.locator("button:visible").filter(has_text=text).first
            try:
                if await loc.count() > 0:
                    btn = loc
                    break
            except Exception:  # noqa: BLE001
                continue

        if btn is None:
            logger.warning("[graphics] botón de fondo no encontrado")
            return

        try:
            current = (await btn.inner_text(timeout=2_000)).strip().lower()
        except Exception:  # noqa: BLE001
            current = ""

        if any(t.lower() in current for t in target_texts):
            logger.info("[graphics] fondo ya en estado deseado, skip")
            return

        # El estado actual es el opuesto → click para alternar
        try:
            await btn.click(timeout=5_000)
            logger.info("[graphics] fondo cambiado a {}",
                        "Transparente" if want_transparent else "Sólido")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[graphics] no pude cambiar fondo: {}", exc)

    # ─────────────────────────────────── wait / download ─────────────────────

    async def wait_for_result(self, page: Page) -> dict[str, Any]:
        logger.info("[graphics] esperando resultado")

        job_id: str | None = None
        try:
            await page.wait_for_url(self.JOB_URL_PATTERN, timeout=15_000)
            match = self.JOB_URL_PATTERN.search(page.url)
            job_id = match.group(1) if match else None
        except Exception:  # noqa: BLE001
            pass
        logger.info("[graphics] job_id Envato: {}", job_id)

        baseline = self._baseline_srcs
        deadline = settings.generation_timeout_ms / 1000
        elapsed  = 0.0
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
                    logger.info("[graphics] {} imagen(es) nuevas listas", len(new_ready))
                    return {"envato_job_id": job_id, "image_srcs": new_ready,
                            "page_url": page.url}

                if len(new_ready) == len(ready):
                    stable_cycles += 1
                    if stable_cycles >= self._STABLE_CYCLES_REQUIRED:
                        logger.info("[graphics] {} imagen(es) estables", len(new_ready))
                        return {"envato_job_id": job_id, "image_srcs": new_ready,
                                "page_url": page.url}
                else:
                    ready = new_ready
                    stable_cycles = 0

            await asyncio.sleep(2)
            elapsed += 2

        if ready:
            logger.warning("[graphics] timeout — devolviendo {} parcial(es)", len(ready))
            return {"envato_job_id": job_id, "image_srcs": ready, "page_url": page.url}

        # Fallback: mismo baseline saturado que image-gen
        fallback_srcs = await page.evaluate(
            """() => Array.from(document.querySelectorAll('img[alt="Generated Image"]'))
                .map(i => i.src).filter(Boolean)"""
        )
        fallback = [
            s for s in fallback_srcs if self.FINAL_SRC_PATTERN.search(s)
        ][: self._expected_count]
        if fallback:
            logger.warning("[graphics] fallback baseline saturado — {} imagen(es)", len(fallback))
            return {"envato_job_id": job_id, "image_srcs": fallback, "page_url": page.url}

        raise TimeoutError(
            f"[graphics] no aparecieron imágenes en {deadline}s. "
            f"baseline={len(baseline)}"
        )

    async def download(self, page: Page, meta: dict[str, Any]) -> GenerationResult:
        srcs: list[str] = meta["image_srcs"]
        if not srcs:
            raise RuntimeError("[graphics] meta sin image_srcs")

        asset_urls: list[str]       = []
        asset_local_paths: list[str] = []

        for src in srcs:
            # graphics-gen usa el dominio 'newvato-gen-assets-production-resizer' que
            # solo está disponible en gen-assets-resized.envatousercontent.com.
            # No intentamos la sustitución de dominio; descargamos directamente el src.
            target = new_asset_path(self.name, ".png")
            try:
                logger.info("[graphics] descargando {}", src[:100])
                response = await page.request.get(src)
                if response.ok:
                    target.write_bytes(await response.body())
                    asset_local_paths.append(str(target))
                    asset_urls.append(public_url(target))
                else:
                    logger.warning("[graphics] HTTP {} para {}", response.status, src[:80])
            except Exception as exc:  # noqa: BLE001
                logger.warning("[graphics] error descargando {}: {}", src[:80], exc)

        if not asset_urls:
            raise RuntimeError("[graphics] no se pudo descargar ninguna imagen")

        return GenerationResult(
            asset_urls=asset_urls,
            asset_local_paths=asset_local_paths,
            metadata={**meta, "downloaded_urls": asset_urls},
        )
