"""Adapter para Envato AI - videoGen (https://app.envato.com/video-gen).

Flujo confirmado contra la UI real (mayo 2026):

1. Navegar a /video-gen.
2. Escribir el prompt en el contenteditable [data-cy="prompt-input"].
3. (Opcional) Subir imágenes de fotograma inicial/final.
4. (Opcional) Configurar aspect ratio, preset y audio con los chips inferiores.
5. Click en button[type="submit"][data-analytics-name="gen_click"] (texto "Generar +").
6. La URL cambia a /video-gen/genai-video/{uuid} mientras genera.
7. El video aparece en el DOM como <video> con currentSrc en envatousercontent.com.
8. Detección vía intercepción de red: el browser carga el video nuevo desde CDN
   (los videos del historial vienen cacheados, sin request nueva).
9. La descarga se hace por HTTP directo usando las cookies del contexto.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import Page, Response

from app.adapters.base import GenerationResult, GeneratorAdapter
from app.config import settings
from app.core.storage import new_asset_path, public_url


class VideoGenAdapter(GeneratorAdapter):
    name = "video"

    URL = "https://app.envato.com/video-gen"

    PROMPT_INPUT = '[data-cy="prompt-input"]'
    SUBMIT_BUTTON = 'button[type="submit"][data-analytics-name="gen_click"]:visible'

    # Confirmados por inspección DOM (mayo 2026):
    # El chip de aspect ratio no tiene data-cy; se localiza por su texto (ratio N:N).
    # El preset sí tiene data-cy="style-panel-toggle".
    # El chip de audio no tiene data-cy; se localiza por texto.
    PRESET_CHIP           = '[data-cy="style-panel-toggle"]'
    INITIAL_FRAME_BUTTON  = 'button:visible >> text="Start frame"'
    FINAL_FRAME_BUTTON    = 'button:visible >> text="End frame"'

    ASPECT_RATIO_VALUES: dict[str, list[str]] = {
        "16:9": ["Landscape", "Horizontal", "16:9"],
        "9:16": ["Portrait",  "Vertical",   "9:16"],
        "1:1":  ["Square",    "Cuadrado",   "1:1"],
    }

    JOB_URL_PATTERN = re.compile(r"/video-gen/genai-video/([0-9a-f-]+)")
    CDN_PATTERN = re.compile(r"envatousercontent\.com")

    def __init__(self) -> None:
        self._detected_url: str | None = None
        self._response_handler: Any = None

    def _make_response_handler(self) -> Any:
        """Crea un handler que captura la primera respuesta de video desde CDN."""
        def handler(response: Response) -> None:
            if self._detected_url:
                return
            url = response.url
            if not self.CDN_PATTERN.search(url):
                return
            # Aceptar por Content-Type o por extensión en la URL
            try:
                ct = response.headers.get("content-type", "")
            except Exception:  # noqa: BLE001
                ct = ""
            if "video" in ct or "mp4" in ct:
                self._detected_url = url
                logger.info("[video] URL capturada via red (content-type): {}…", url[:80])
                return
            # Fallback: por extensión en el path (antes del ?)
            path = url.split("?")[0].lower()
            if any(path.endswith(ext) for ext in (".mp4", ".webm", ".mov", ".m4v")):
                self._detected_url = url
                logger.info("[video] URL capturada via red (ext): {}…", url[:80])
        return handler

    async def navigate(self, page: Page) -> None:
        logger.info("[video] navegando a {}", self.URL)
        await page.goto(self.URL, wait_until="domcontentloaded")
        if "sign_in" in page.url or "/login" in page.url:
            raise RuntimeError(
                "La sesión de Envato no es válida. Re-correr scripts/login.py."
            )
        await page.locator(self.PROMPT_INPUT).first.wait_for(state="visible")
        await asyncio.sleep(1.5)

    async def submit(self, page: Page, payload: dict[str, Any]) -> None:
        prompt: str = payload["prompt"]
        logger.info("[video] enviando prompt ({} chars)", len(prompt))

        prompt_box = page.locator(self.PROMPT_INPUT).first
        await prompt_box.click()
        await prompt_box.fill(prompt)

        aspect_ratio = payload.get("aspect_ratio")
        if aspect_ratio in self.ASPECT_RATIO_VALUES:
            await self._select_aspect_ratio(page, aspect_ratio)

        preset = payload.get("preset")
        if preset:
            await self._select_simple_dropdown(
                page,
                chip=self.PRESET_CHIP,
                option_texts=[preset],
            )

        initial_frame: str | None = payload.get("initial_frame")
        final_frame: str | None = payload.get("final_frame")
        if initial_frame:
            await self._upload_frame(page, self.INITIAL_FRAME_BUTTON, initial_frame)
        if final_frame:
            await self._upload_frame(page, self.FINAL_FRAME_BUTTON, final_frame)

        # Registrar interceptor ANTES del click para no perder la respuesta.
        self._detected_url = None
        self._response_handler = self._make_response_handler()
        page.on("response", self._response_handler)

        await page.locator(self.SUBMIT_BUTTON).first.click()

    async def _select_aspect_ratio(self, page: Page, ratio: str) -> None:
        """Selecciona el aspect ratio del chip de texto (sin data-cy en video-gen)."""
        labels = self.ASPECT_RATIO_VALUES[ratio]
        # El chip muestra el valor actual como texto (ej. "16:9"). Lo localizo por patrón.
        chip_loc = page.locator("button:visible").filter(
            has_text=re.compile(r"^\s*\d+:\d+\s*$")
        ).first
        try:
            current = (await chip_loc.inner_text(timeout=3_000)).strip()
        except Exception:  # noqa: BLE001
            logger.warning("[video] no encontré chip de aspect ratio")
            return

        if any(lbl.lower() in current.lower() or current in lbl for lbl in labels):
            logger.info("[video] aspect ratio '{}' ya seleccionado, skip", current)
            return

        try:
            await chip_loc.click(timeout=5_000)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[video] no pude clickear chip aspect ratio: {}", exc)
            return

        await asyncio.sleep(0.4)

        for text in labels:
            try:
                option = page.locator("button:visible").filter(has_text=text).first
                if await option.count() > 0:
                    await option.click(timeout=3_000, force=True)
                    logger.info("[video] aspect ratio seleccionado: {}", text)
                    return
            except Exception:  # noqa: BLE001
                continue

        logger.warning("[video] no encontré opción de aspect ratio {}", labels)
        await page.keyboard.press("Escape")

    async def _select_simple_dropdown(
        self,
        page: Page,
        chip: str,
        option_texts: list[str],
    ) -> None:
        """Abre el chip por selector y elige la opción por texto (busca en cualquier popup)."""
        chip_loc = page.locator(chip).first
        try:
            current = (await chip_loc.inner_text(timeout=3_000)).strip().lower()
        except Exception:  # noqa: BLE001
            current = ""

        if any(t.strip().lower() in current for t in option_texts):
            logger.info("[video] '{}' ya seleccionado, skip", option_texts[0])
            return

        try:
            await chip_loc.click(timeout=5_000)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[video] no pude abrir chip {}: {}", chip, exc)
            return

        await asyncio.sleep(0.4)

        for text in option_texts:
            try:
                option = page.locator("button:visible").filter(has_text=text).first
                if await option.count() > 0:
                    await option.click(timeout=3_000, force=True)
                    logger.info("[video] seleccionado '{}'", text)
                    return
            except Exception:  # noqa: BLE001
                continue

        logger.warning("[video] no encontré opción {}", option_texts)
        await page.keyboard.press("Escape")

    async def _upload_frame(self, page: Page, button_selector: str, file_path: str) -> None:
        path = Path(file_path)
        if not path.exists():
            logger.warning("[video] fotograma no encontrado: {}", file_path)
            return
        try:
            btn = page.locator(button_selector).first
            async with page.expect_file_chooser() as fc_info:
                await btn.click(timeout=5_000)
            file_chooser = await fc_info.value
            await file_chooser.set_files(str(path))
            logger.info("[video] fotograma subido: {}", path.name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[video] error subiendo fotograma {}: {}", file_path, exc)

    def _cleanup_listener(self, page: Page) -> None:
        if self._response_handler:
            try:
                page.remove_listener("response", self._response_handler)
            except Exception:  # noqa: BLE001
                pass
            self._response_handler = None

    async def wait_for_result(self, page: Page) -> dict[str, Any]:
        logger.info("[video] esperando resultado (intercepción de red activa)")

        job_id: str | None = None
        try:
            await page.wait_for_url(self.JOB_URL_PATTERN, timeout=15_000)
            match = self.JOB_URL_PATTERN.search(page.url)
            job_id = match.group(1) if match else None
        except Exception:  # noqa: BLE001
            pass
        logger.info("[video] job_id Envato: {}", job_id)

        deadline = settings.generation_timeout_ms / 1000
        elapsed = 0.0

        while elapsed < deadline:
            if self._detected_url:
                self._cleanup_listener(page)
                return {
                    "envato_job_id": job_id,
                    "video_srcs": [self._detected_url],
                    "page_url": page.url,
                }
            await asyncio.sleep(2)
            elapsed += 2

        self._cleanup_listener(page)

        # Fallback: buscar en DOM si la intercepción no capturó nada.
        fallback_srcs = await page.evaluate("""() => {
            const srcs = new Set();
            document.querySelectorAll('video').forEach(v => {
                const s = v.currentSrc || v.src || '';
                if (s && s.includes('envatousercontent.com')) srcs.add(s);
            });
            return Array.from(srcs);
        }""")
        if fallback_srcs:
            logger.warning("[video] fallback DOM: {} video(s)", len(fallback_srcs))
            return {
                "envato_job_id": job_id,
                "video_srcs": fallback_srcs[:1],
                "page_url": page.url,
            }

        raise TimeoutError(
            f"[video] no se detectó ningún video nuevo en {deadline}s."
        )

    async def download(self, page: Page, meta: dict[str, Any]) -> GenerationResult:
        srcs: list[str] = meta["video_srcs"]
        if not srcs:
            raise RuntimeError("[video] meta sin video_srcs")

        asset_urls: list[str] = []
        asset_local_paths: list[str] = []

        for src in srcs:
            target = new_asset_path(self.name, ".mp4")
            try:
                logger.info("[video] descargando {}", src[:100])
                response = await page.request.get(src)
                if response.ok:
                    target.write_bytes(await response.body())
                    asset_local_paths.append(str(target))
                    asset_urls.append(public_url(target))
                    size_mb = target.stat().st_size / 1024 / 1024
                    logger.info("[video] guardado {} ({:.1f} MB)", target.name, size_mb)
                else:
                    logger.warning("[video] HTTP {} para {}", response.status, src[:80])
            except Exception as exc:  # noqa: BLE001
                logger.warning("[video] error descargando {}: {}", src[:80], exc)

        if not asset_urls:
            raise RuntimeError("[video] no se pudo descargar ningún video")

        return GenerationResult(
            asset_urls=asset_urls,
            asset_local_paths=asset_local_paths,
            metadata={**meta, "downloaded_urls": asset_urls},
        )
