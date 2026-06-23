"""Adapter para Envato AI - videoGen (https://app.envato.com/video-gen).

Mapeo confirmado contra la UI real (junio 2026). Sigue el mismo patrón que imageGen
pero con diferencias propias de video:

- Opciones: aspect ratio (16:9 / 9:16) y audio (No audio / With audio), como chips
  data-cy + dropdown.
- Uploads por pestañas (botones por texto): "Start frame" (fotograma inicial, 1 img),
  "End frame" (fotograma final, 1 img), "Images" (referencias, hasta 5).
- El resultado es un <video> dentro de un item-card. El thumbnail es
  img[alt="Generated video"] (gen-assets-resized) y el video real está en
  gen-assets.app.envatousercontent.com (URL firmada S3, expira ~1h).

Limitación conocida: en la cuenta actual el botón "End frame" aparece DESHABILITADO
(feature en rollout / modo específico). El adapter lo intenta y, si está disabled,
avisa y sigue sin el fotograma final en vez de fallar el job.
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import Page

from app.adapters.base import GenerationResult, GeneratorAdapter
from app.config import settings
from app.core.storage import new_asset_path, public_url


class VideoGenAdapter(GeneratorAdapter):
    name = "video"

    URL = "https://app.envato.com/video-gen"

    PROMPT_INPUT = '[data-cy="prompt-input"]'
    # El botón Generate de videoGen comparte el data-analytics-name con imageGen.
    SUBMIT_BUTTON = 'button[data-analytics-name="gen_click"]:visible'
    CALLOUT_CLOSE = '[data-cy="image-gen-shortcuts-feature-callout-close"]'

    # Opciones (chips data-cy + dropdown, como imageGen).
    ASPECT_RATIO_CHIP = '[data-cy="video-aspect-ratio-chip"]'
    ASPECT_RATIO_DROPDOWN = '[data-cy="video-aspect-ratio-dropdown"]'
    ASPECT_RATIO_VALUES = {"16:9", "9:16"}

    AUDIO_CHIP = '[data-cy="video-audio-chip"]'
    AUDIO_DROPDOWN = '[data-cy="video-audio-dropdown"]'
    AUDIO_VALUES = {True: "With audio", False: "No audio"}

    # Pestañas de upload (se identifican por texto exacto del botón).
    TAB_START_FRAME = "Start frame"
    TAB_END_FRAME = "End frame"
    TAB_IMAGES = "Images"
    MAX_REFERENCES = 5
    ALLOWED_TYPES = ("image/jpeg", "image/jpg", "image/png", "image/webp")

    async def navigate(self, page: Page) -> None:
        logger.info("[video] navegando a {}", self.URL)
        await page.goto(self.URL, wait_until="domcontentloaded")
        if "sign_in" in page.url or "/login" in page.url:
            raise RuntimeError(
                "La sesión de Envato no es válida. Re-correr scripts/login.py."
            )
        await page.locator(self.PROMPT_INPUT).first.wait_for(state="visible")
        # Cerrar cualquier callout que pudiera interceptar el submit (igual que imageGen).
        close_btn = page.locator(self.CALLOUT_CLOSE)
        try:
            if await close_btn.count():
                await close_btn.first.click(timeout=3_000)
                logger.info("[video] callout cerrado")
        except Exception as exc:  # noqa: BLE001
            logger.debug("[video] no hubo callout que cerrar: {}", exc)

    async def submit(self, page: Page, payload: dict[str, Any]) -> None:
        prompt: str = payload["prompt"]
        logger.info("[video] enviando prompt ({} chars)", len(prompt))

        # Baseline de <video> srcs presentes antes de generar. La señal confiable de
        # "resultado listo" es que aparezca un <video> nuevo como primer item-card
        # (el thumbnail img no es fiable). Comparamos por path sin query (URL firmada).
        self._baseline_videos = {self._strip_query(s) for s in await self._video_srcs(page)}
        logger.info("[video] baseline: {} videos previos", len(self._baseline_videos))

        # Uploads (todos opcionales), antes del prompt.
        first_frame = payload.get("first_frame")
        if first_frame:
            await self._upload_via_tab(page, self.TAB_START_FRAME, [first_frame], multiple=False)

        last_frame = payload.get("last_frame")
        if last_frame:
            await self._upload_via_tab(page, self.TAB_END_FRAME, [last_frame], multiple=False)

        reference_images = payload.get("reference_images") or []
        if reference_images:
            if len(reference_images) > self.MAX_REFERENCES:
                logger.warning(
                    "[video] {} referencias, uso solo {}", len(reference_images), self.MAX_REFERENCES
                )
                reference_images = reference_images[: self.MAX_REFERENCES]
            await self._upload_via_tab(page, self.TAB_IMAGES, reference_images, multiple=True)

        # Prompt (editor rich-text custom). CRÍTICO: el editor de video-gen a veces no
        # captura el texto si se tipea apenas carga (el POST de generación salía con
        # prompt vacío → el backend fallaba en silencio). Tipeamos, VERIFICAMOS que el
        # editor lo tomó, y reintentamos si quedó vacío.
        prompt_box = page.locator(self.PROMPT_INPUT).first
        await self._type_prompt(page, prompt_box, prompt)
        if "@" in prompt:
            try:
                if await page.locator('[role="listbox"]:visible').count():
                    await page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass

        # Opciones.
        aspect_ratio = payload.get("aspect_ratio")
        if aspect_ratio:
            if aspect_ratio in self.ASPECT_RATIO_VALUES:
                await self._select_chip_option(
                    page, self.ASPECT_RATIO_CHIP, self.ASPECT_RATIO_DROPDOWN, aspect_ratio
                )
            else:
                logger.warning("[video] aspect_ratio '{}' no soportado (válidos: {})",
                               aspect_ratio, sorted(self.ASPECT_RATIO_VALUES))

        audio = payload.get("audio")
        if isinstance(audio, bool):
            await self._select_chip_option(
                page, self.AUDIO_CHIP, self.AUDIO_DROPDOWN, self.AUDIO_VALUES[audio]
            )

        await page.locator(self.SUBMIT_BUTTON).first.click()

    async def _upload_via_tab(
        self, page: Page, tab_label: str, urls: list[str], multiple: bool
    ) -> None:
        """Descarga URLs y las sube por la pestaña `tab_label` (Start/End frame, Images).

        Cada pestaña, al clickearse, revela un input[type=file]. Elegimos el input cuyo
        atributo `multiple` coincide con lo esperado, para no confundir el de frames
        (single) con el de referencias (multiple)."""
        button = page.get_by_role("button", name=tab_label, exact=True).first
        # Si la pestaña está deshabilitada (ej. "End frame" en rollout), avisamos y salimos.
        try:
            if await button.is_disabled():
                logger.warning("[video] pestaña '{}' deshabilitada, la omito", tab_label)
                return
        except Exception:  # noqa: BLE001
            pass

        tmpdir = tempfile.mkdtemp(prefix="envauto-vid-")
        try:
            paths: list[str] = []
            for i, url in enumerate(urls):
                logger.info("[video] descargando para '{}' ({}): {}", tab_label, i + 1, str(url)[:100])
                resp = await page.request.get(url)
                if not resp.ok:
                    raise RuntimeError(f"[video] '{tab_label}' #{i+1}: HTTP {resp.status} en {url}")
                body = await resp.body()
                ct = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                suffix = self._suffix_for(ct, body)
                if suffix == ".img":
                    raise RuntimeError(
                        f"[video] '{tab_label}' #{i+1}: tipo no soportado ({ct or 'desconocido'})."
                    )
                p = Path(tmpdir) / f"{tab_label.replace(' ', '_')}_{i+1}{suffix}"
                p.write_bytes(body)
                paths.append(str(p))

            await button.click()
            await asyncio.sleep(0.6)
            # Elegir el input correcto por su atributo multiple.
            inputs = page.locator('input[type="file"]')
            await inputs.first.wait_for(state="attached", timeout=5_000)
            count = await inputs.count()
            target = None
            for idx in range(count):
                is_multi = await inputs.nth(idx).evaluate("i => i.multiple")
                if is_multi == multiple:
                    target = inputs.nth(idx)
            if target is None:
                target = inputs.last
            await target.set_input_files(paths if multiple else paths[0])
            logger.info("[video] '{}': {} archivo(s) subido(s)", tab_label, len(paths))
            await asyncio.sleep(2)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    async def _type_prompt(self, page: Page, prompt_box, prompt: str) -> None:
        """Escribe el prompt y verifica que el editor lo capturó; reintenta si no.

        El editor rich-text de video-gen a veces ignora el primer intento si todavía
        no terminó de inicializarse, dejando el POST de generación con prompt vacío."""
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
                    logger.info("[video] prompt escrito en intento {}", attempt + 1)
                return
            logger.warning("[video] el editor no tomó el prompt (intento {}), reintento", attempt + 1)
            await asyncio.sleep(0.8)
        raise RuntimeError("[video] no pude escribir el prompt en el editor tras 3 intentos")

    async def _select_chip_option(
        self, page: Page, chip_selector: str, dropdown_selector: str, option_text: str
    ) -> None:
        """Abre un chip-combobox y clickea la opción de texto exacto dentro del dropdown
        visible (mismo patrón que imageGen)."""
        logger.info("[video] seleccionando '{}' en {}", option_text, chip_selector)
        await page.locator(f"{chip_selector}:visible").first.click()
        dropdown = page.locator(f"{dropdown_selector}:visible").first
        await dropdown.wait_for(state="visible", timeout=5_000)
        await dropdown.get_by_role("button", name=option_text, exact=True).first.click(timeout=5_000)

    async def _video_srcs(self, page: Page) -> list[str]:
        """src de todos los <video> dentro de item-cards de la galería."""
        return await page.evaluate(
            """() => Array.from(document.querySelectorAll('[data-cy="item-card"] video'))
                .map(v => v.src || (v.querySelector('source') ? v.querySelector('source').src : ''))
                .filter(Boolean)"""
        )

    async def _first_video_src(self, page: Page) -> str:
        """src del <video> del primer item-card (el más nuevo), o '' si no hay."""
        return await page.evaluate(
            """() => {
                const card = document.querySelector('[data-cy="item-card"]');
                if (!card) return '';
                const v = card.querySelector('video');
                if (!v) return '';
                return v.src || (v.querySelector('source') ? v.querySelector('source').src : '');
            }"""
        )

    @staticmethod
    def _strip_query(url: str) -> str:
        return url.split("?", 1)[0]

    async def wait_for_result(self, page: Page) -> dict[str, Any]:
        """Espera a que aparezca un <video> nuevo (no presente en el baseline) como
        primer item-card. NO recarga la página: con navegador fresco por job la
        generación completa normalmente y la galería se actualiza sola (recargar
        mientras genera cancelaría la generación)."""
        logger.info("[video] esperando resultado (puede tardar minutos)")
        baseline = getattr(self, "_baseline_videos", set())
        deadline = settings.generation_timeout_ms / 1000
        elapsed = 0.0
        while elapsed < deadline:
            first = await self._first_video_src(page)
            if first and self._strip_query(first) not in baseline:
                # Confirmar estabilidad (que no sea un reordenamiento transitorio).
                await asyncio.sleep(3)
                confirm = await self._first_video_src(page)
                if confirm and self._strip_query(confirm) not in baseline:
                    logger.info("[video] video nuevo detectado")
                    return {"video_src": confirm, "page_url": page.url}
            await asyncio.sleep(5)
            elapsed += 5

        raise TimeoutError(
            f"[video] no apareció un video nuevo en {deadline:.0f}s. "
            f"La generación pudo fallar o tardar más del timeout."
        )

    async def download(self, page: Page, meta: dict[str, Any]) -> GenerationResult:
        """Descarga el video vía HTTP usando las cookies del contexto."""
        src: str = meta.get("video_src", "")
        if not src:
            raise RuntimeError("[video] meta sin video_src")
        logger.info("[video] descargando {}", src[:120])
        response = await page.request.get(src)
        if not response.ok:
            raise RuntimeError(f"[video] HTTP {response.status} al descargar el video")
        body = await response.body()
        suffix = self._suffix_for(response.headers.get("content-type", ""), body)
        target = new_asset_path(self.name, suffix)
        target.write_bytes(body)
        return GenerationResult(
            asset_url=public_url(target),
            asset_local_path=str(target),
            metadata={**meta, "downloaded_from": src},
        )

    @staticmethod
    def _suffix_for(content_type: str, body: bytes) -> str:
        """Extensión por Content-Type, con fallback a magic bytes. Soporta mp4/webm
        (video) y jpeg/png/webp (por si alguna vez se descarga una imagen)."""
        ct = content_type.lower()
        if "mp4" in ct:
            return ".mp4"
        if "webm" in ct:
            return ".webm"
        if "quicktime" in ct or "mov" in ct:
            return ".mov"
        if "webp" in ct:
            return ".webp"
        if "jpeg" in ct or "jpg" in ct:
            return ".jpg"
        if "png" in ct:
            return ".png"
        # Magic bytes.
        if body[4:8] == b"ftyp":
            return ".mp4"
        if body[:4] == b"\x1aE\xdf\xa3":
            return ".webm"
        if body[:3] == b"\xff\xd8\xff":
            return ".jpg"
        if body[:8] == b"\x89PNG\r\n\x1a\n":
            return ".png"
        if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
            return ".webp"
        return ".img"
