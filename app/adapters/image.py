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
import shutil
import tempfile
from pathlib import Path
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
    # Overlay que tapa el botón Generate al cargar; hay que cerrarlo antes de enviar.
    CALLOUT_CLOSE = '[data-cy="image-gen-shortcuts-feature-callout-close"]'

    # Referencias: el botón revela un input[type=file] (multiple) que acepta jpeg/png/webp.
    # Se pueden referenciar luego en el prompt con @image1, @image2, etc.
    REFERENCE_BUTTON = '[data-cy="reference-images-button"]'
    MAX_REFERENCES = 5
    ALLOWED_REF_TYPES = ("image/jpeg", "image/jpg", "image/png", "image/webp")

    # Controles de opciones (UI junio 2026). Cada uno es un "chip" con data-cy
    # que abre un dropdown; dentro, cada opción es un <button> con el texto del valor.
    ASPECT_RATIO_CHIP = '[data-cy="aspect-ratio-chip"]'
    ASPECT_RATIO_DROPDOWN = '[data-cy="aspect-ratio-dropdown"]'
    VARIATIONS_CHIP = '[data-cy="variations-chip"]'
    VARIATIONS_DROPDOWN = '[data-cy="variations-dropdown"]'

    # Aspect ratios: el texto de la opción es la notación directa ("1:1", "16:9", ...).
    ASPECT_RATIO_VALUES = {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"}

    # Variations: Envato hoy solo ofrece 1 y 3. El texto de la opción es "N Variation(s)".
    VARIATIONS_VALUES = {1: "1 Variation", 3: "3 Variations"}

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

        # CRÍTICO: al cargar, Envato muestra un "feature callout" (tip de shortcuts)
        # que se superpone al botón Generate e intercepta el click, haciendo que la
        # generación nunca arranque (este era EL bug que bloqueaba todo). Lo cerramos.
        close_btn = page.locator(self.CALLOUT_CLOSE)
        try:
            if await close_btn.count():
                await close_btn.first.click(timeout=3_000)
                logger.info("[image] callout de shortcuts cerrado")
        except Exception as exc:  # noqa: BLE001 - si no está o no se puede cerrar, seguimos
            logger.debug("[image] no hubo callout que cerrar: {}", exc)

    async def submit(self, page: Page, payload: dict[str, Any]) -> None:
        prompt: str = payload["prompt"]
        logger.info("[image] enviando prompt ({} chars)", len(prompt))

        # Baseline: qué imágenes ya están en la galería ANTES de generar. La URL no
        # cambia al generar (las nuevas aparecen inline arriba), así que detectamos el
        # resultado comparando contra este set. Ejecución serial => guardar en self es seguro.
        self._baseline_srcs = set(await self._result_srcs(page))
        logger.info("[image] baseline: {} imágenes previas", len(self._baseline_srcs))

        # Referencias (opcional): URLs que descargamos y subimos al input de Envato.
        # Se hace ANTES de escribir el prompt, así se pueden citar con @image1, @image2.
        reference_images = payload.get("reference_images") or []
        if reference_images:
            await self._attach_references(page, reference_images)

        # El prompt es un editor rich-text custom. fill() no actualiza su modelo interno;
        # hay que tipear con eventos reales Y VERIFICAR que quedó (el editor a veces
        # ignora el primer intento si aún no terminó de inicializarse, lo que dejaba el
        # POST de generación con prompt vacío).
        prompt_box = page.locator(self.PROMPT_INPUT).first
        await self._type_prompt(page, prompt_box, prompt)

        # Si el prompt contiene un "@", Envato abre un autocomplete de menciones
        # (role=listbox) que se superpone al botón Generate y bloquea el submit.
        # Lo cerramos para no repetir el bug del callout. (Citar refs con @imageN
        # requeriría elegir del picker; no soportado aún: usar prompts descriptivos.)
        if "@" in prompt:
            try:
                if await page.locator('[role="listbox"]:visible').count():
                    await page.keyboard.press("Escape")
                    logger.info("[image] cerré autocomplete de menciones (@)")
            except Exception:  # noqa: BLE001
                pass

        # Opciones soportadas (todas opcionales).
        aspect_ratio = payload.get("aspect_ratio")
        if aspect_ratio:
            if aspect_ratio in self.ASPECT_RATIO_VALUES:
                await self._select_chip_option(
                    page, self.ASPECT_RATIO_CHIP, self.ASPECT_RATIO_DROPDOWN, aspect_ratio
                )
            else:
                logger.warning("[image] aspect_ratio '{}' no soportado, lo ignoro", aspect_ratio)

        variations = payload.get("variations")
        if isinstance(variations, int):
            if variations in self.VARIATIONS_VALUES:
                await self._select_chip_option(
                    page, self.VARIATIONS_CHIP, self.VARIATIONS_DROPDOWN,
                    self.VARIATIONS_VALUES[variations],
                )
            else:
                logger.warning(
                    "[image] variations={} no soportado (válidos: {}), lo ignoro",
                    variations, sorted(self.VARIATIONS_VALUES),
                )

        # Disparar. .first como red de seguridad si :visible matchea más de uno.
        await page.locator(self.SUBMIT_BUTTON).first.click()

    async def _type_prompt(self, page: Page, prompt_box, prompt: str) -> None:
        """Escribe el prompt y verifica que el editor lo capturó; reintenta si quedó
        vacío (el editor rich-text a veces ignora el primer intento)."""
        for attempt in range(3):
            await prompt_box.click()
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Delete")
            await page.keyboard.type(prompt)
            await asyncio.sleep(0.4)
            try:
                typed = (await prompt_box.inner_text()).strip()
            except Exception:  # noqa: BLE001
                typed = ""
            if prompt.strip()[:20] in typed:
                if attempt:
                    logger.info("[image] prompt escrito en intento {}", attempt + 1)
                return
            logger.warning("[image] el editor no tomó el prompt (intento {}), reintento", attempt + 1)
            await asyncio.sleep(0.8)
        raise RuntimeError("[image] no pude escribir el prompt en el editor tras 3 intentos")

    async def _select_chip_option(
        self, page: Page, chip_selector: str, dropdown_selector: str, option_text: str
    ) -> None:
        """Abre un chip-combobox por su data-cy y clickea la opción cuyo texto exacto
        es `option_text`.

        La UI de Envato renderiza cada control como un chip (`data-cy="..."`) que al
        clickearse despliega un dropdown (`data-cy="...-dropdown"`) con un <button> por
        opción. El texto del botón es el valor ("1:1", "3 Variations", etc.).
        """
        logger.info("[image] seleccionando '{}' en {}", option_text, chip_selector)
        # Clickear el chip VISIBLE (hay variantes desktop/compact con el mismo data-cy).
        await page.locator(f"{chip_selector}:visible").first.click()

        # Hay 2 dropdowns en el DOM (layout desktop + compact); solo uno se hace visible
        # al abrir. Nos scopeamos al dropdown VISIBLE y buscamos la opción adentro por
        # texto exacto, para no chocar con los botones del dropdown oculto.
        dropdown = page.locator(f"{dropdown_selector}:visible").first
        await dropdown.wait_for(state="visible", timeout=5_000)
        await dropdown.get_by_role("button", name=option_text, exact=True).first.click(
            timeout=5_000
        )

    async def _attach_references(self, page: Page, urls: list[str]) -> None:
        """Descarga imágenes de referencia por URL y las sube al input de Envato.

        El botón de referencias revela un `input[type=file]` (multiple) que acepta
        jpeg/png/webp. Descargamos cada URL a un temp, validamos el tipo, y subimos
        todas de una con set_input_files. Máximo 5 (las extra se ignoran con warning).
        """
        if not isinstance(urls, list):
            raise RuntimeError("[image] reference_images debe ser una lista de URLs")
        if len(urls) > self.MAX_REFERENCES:
            logger.warning(
                "[image] {} referencias recibidas, uso solo las primeras {}",
                len(urls), self.MAX_REFERENCES,
            )
            urls = urls[: self.MAX_REFERENCES]

        tmpdir = tempfile.mkdtemp(prefix="envauto-ref-")
        try:
            paths: list[str] = []
            for i, url in enumerate(urls):
                logger.info("[image] descargando referencia {}: {}", i + 1, str(url)[:120])
                resp = await page.request.get(url)
                if not resp.ok:
                    raise RuntimeError(f"[image] referencia {i + 1}: HTTP {resp.status} en {url}")
                body = await resp.body()
                content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                suffix = self._suffix_for(content_type, body)
                if suffix == ".img":
                    raise RuntimeError(
                        f"[image] referencia {i + 1}: tipo no soportado "
                        f"({content_type or 'desconocido'}). Permitidos: jpeg, png, webp."
                    )
                ref_path = Path(tmpdir) / f"ref_{i + 1}{suffix}"
                ref_path.write_bytes(body)
                paths.append(str(ref_path))

            # Revelar el input y subir todos los archivos juntos (input multiple).
            await page.locator(f"{self.REFERENCE_BUTTON}:visible").first.click()
            file_input = page.locator('input[type="file"]').first
            await file_input.wait_for(state="attached", timeout=5_000)
            await file_input.set_input_files(paths)
            logger.info("[image] {} referencia(s) adjuntada(s)", len(paths))
            # Dar tiempo a que Envato procese/suba los thumbnails antes de generar.
            await asyncio.sleep(3)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    async def _result_srcs(self, page: Page) -> list[str]:
        """Lista los src de todas las imágenes de resultado presentes en la galería."""
        return await page.evaluate(
            """() => Array.from(document.querySelectorAll('img[alt="Generated Image"]'))
                .map(i => i.src).filter(Boolean)"""
        )

    async def wait_for_result(self, page: Page) -> dict[str, Any]:
        """Espera a que aparezcan imágenes NUEVAS respecto del baseline.

        La UI ya no cambia la URL al generar (antes iba a /image-gen/genai-image/{uuid});
        los resultados se insertan inline arriba de la galería. Detectamos el resultado
        comparando los src finales contra el baseline capturado en submit().
        """
        logger.info("[image] esperando resultado (sin cambio de URL)")
        baseline = getattr(self, "_baseline_srcs", set())
        deadline = settings.generation_timeout_ms / 1000
        elapsed = 0.0
        while elapsed < deadline:
            srcs = await self._result_srcs(page)
            new = [
                s for s in srcs
                if s not in baseline and self.FINAL_SRC_PATTERN.search(s)
            ]
            if new:
                # Dar un margen para que terminen de cargar todas las variaciones,
                # y re-capturar para devolver el set completo de nuevas imágenes.
                await asyncio.sleep(4)
                srcs2 = await self._result_srcs(page)
                new2 = [
                    s for s in srcs2
                    if s not in baseline and self.FINAL_SRC_PATTERN.search(s)
                ]
                final = new2 or new
                logger.info("[image] {} imagen(es) nueva(s) detectada(s)", len(final))
                return {"image_srcs": final, "page_url": page.url}
            await asyncio.sleep(2)
            elapsed += 2

        raise TimeoutError(
            f"[image] no aparecieron imágenes nuevas en {deadline:.0f}s. "
            f"La generación pudo fallar del lado de Envato."
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

        last_error: Exception | None = None
        for candidate in candidates:
            try:
                logger.info("[image] descargando {}", candidate[:120])
                response = await page.request.get(candidate)
                if response.ok:
                    body = await response.body()
                    # El resizer devuelve format=auto (suele ser JPEG/WebP), así que la
                    # extensión la sacamos del Content-Type real, no asumimos .png.
                    suffix = self._suffix_for(response.headers.get("content-type", ""), body)
                    target = new_asset_path(self.name, suffix)
                    target.write_bytes(body)
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

    @staticmethod
    def _suffix_for(content_type: str, body: bytes) -> str:
        """Deriva la extensión del archivo desde el Content-Type, con fallback a los
        magic bytes. Envato sirve format=auto, así que puede ser jpeg, webp o png."""
        ct = content_type.lower()
        if "webp" in ct:
            return ".webp"
        if "jpeg" in ct or "jpg" in ct:
            return ".jpg"
        if "png" in ct:
            return ".png"
        # Fallback por magic bytes si el header no fue claro.
        if body[:3] == b"\xff\xd8\xff":
            return ".jpg"
        if body[:8] == b"\x89PNG\r\n\x1a\n":
            return ".png"
        if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
            return ".webp"
        return ".img"
