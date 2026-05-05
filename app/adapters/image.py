"""Adapter para Envato AI - imageGen (https://app.envato.com/image-gen).

Mapeo del flujo confirmado contra la UI real (mayo 2026):

1. Navegar a /image-gen.
2. Escribir el prompt en el contenteditable [data-cy="prompt-input"].
3. (Opcional) Configurar aspect ratio, count y style clickeando los comboboxes
   por su texto actual ("Square", "3 Variations", "Style") y eligiendo el item.
4. Si aparece Cookiebot, cerrarlo antes de clickear submit.
5. Click en button[type="submit"] (texto "Generate").
6. Resultado por estrategia dual:
   - si la URL cambia a /image-gen/genai-image/{uuid}, se toma ese job_id;
   - si no cambia, se resuelve por estado visible de [data-cy="details-panel"].
7. Las imágenes finales aparecen como img[alt="Generated Image"] con src en
   gen-assets*.envatousercontent.com.
8. La descarga se hace por HTTP directo al src usando las cookies del contexto,
   en vez de pelear con un menú nativo del browser.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger
from playwright.async_api import Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeoutError

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
    DETAILS_PANEL = '[data-cy="details-panel"]'
    FAST_FAILURE_MARKERS = ("All generations failed", "Try again")
    _last_prompt: str | None = None
    _baseline_image_srcs: set[str] = set()

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
        await self.dismiss_cookiebot_if_present(page)

    async def submit(self, page: Page, payload: dict[str, Any]) -> None:
        prompt: str = payload["prompt"]
        self._last_prompt = prompt
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

        # Baseline de imágenes visibles antes de generar, para detectar nuevas.
        self._baseline_image_srcs = set(await self._list_visible_result_images(page))

        # Si Cookiebot reapareció entre acciones, cerrarlo antes del submit.
        await self.dismiss_cookiebot_if_present(page)

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
        prompt = self._last_prompt or ""

        # Paso 1 (rápido): intentar detectar transición de URL al job.
        job_id: str | None = None
        resolution_path = "panel"
        try:
            await page.wait_for_url(
                self.JOB_URL_PATTERN,
                timeout=min(15_000, settings.generation_timeout_ms),
            )
            match = self.JOB_URL_PATTERN.search(page.url)
            job_id = match.group(1) if match else None
            resolution_path = "url"
            logger.info("[image] job_id Envato por URL: {}", job_id)
        except PlaywrightTimeoutError:
            logger.warning(
                "[image] sin transición de URL al job, continúo por estado del panel"
            )

        # Paso 2: observar el panel activo para éxito o error explícito.
        deadline = settings.generation_timeout_ms / 1000
        elapsed = 0.0
        srcs: list[str] = []
        last_panel_state: dict[str, Any] = {}
        clicked_prompt_panel = False
        while elapsed < deadline:
            if "sign_in" in page.url or "/login" in page.url:
                raise RuntimeError(
                    "La sesión de Envato expiró durante la espera del resultado."
                )

            await self.dismiss_cookiebot_if_present(page)
            try:
                panel_state = await self._read_active_panel_state(page, prompt=prompt)
            except PlaywrightError as exc:
                if self._is_transient_navigation_context_error(exc):
                    logger.debug(
                        "[image] contexto JS transitorio durante navegación, reintento en loop"
                    )
                    await asyncio.sleep(1)
                    elapsed += 1
                    continue
                raise
            last_panel_state = panel_state

            # Si el panel del prompt no trae señal, usar el mejor panel con señal visible.
            using_fallback_signal = (
                panel_state["prompt_match"]
                and not panel_state["has_failure"]
                and not panel_state["image_srcs"]
                and not panel_state["job_id"]
                and panel_state["has_any_signal_panel"]
            )
            active_state = panel_state["signal_panel"] if using_fallback_signal else panel_state

            if active_state["job_id"] and not job_id:
                job_id = active_state["job_id"]

            if active_state["has_failure"]:
                raise RuntimeError(
                    f"[image] Envato reportó error explícito: {active_state['failure_marker']}"
                )

            srcs = active_state["image_srcs"]
            ready = [s for s in srcs if self.FINAL_SRC_PATTERN.search(s)]
            if ready:
                return {
                    "envato_job_id": job_id,
                    "image_srcs": ready,
                    "page_url": page.url,
                    "resolution_path": resolution_path,
                }

            # Fallback: imágenes nuevas detectadas globalmente vs baseline previo al submit.
            global_new = [
                s for s in panel_state["global_image_srcs"]
                if s not in self._baseline_image_srcs
            ]
            global_ready = [s for s in global_new if self.FINAL_SRC_PATTERN.search(s)]
            if global_ready:
                return {
                    "envato_job_id": job_id,
                    "image_srcs": global_ready,
                    "page_url": page.url,
                    "resolution_path": "global_diff",
                }

            # Intento adicional: seleccionar el panel del prompt para disparar su detalle.
            if (
                prompt
                and panel_state["prompt_match"]
                and not panel_state["has_failure"]
                and not panel_state["image_srcs"]
                and not panel_state["job_id"]
                and not clicked_prompt_panel
                and elapsed >= 10
            ):
                try:
                    await page.locator(self.DETAILS_PANEL).filter(has_text=prompt).first.click(timeout=2_000)
                    clicked_prompt_panel = True
                except Exception:
                    clicked_prompt_panel = True

            await asyncio.sleep(2)
            elapsed += 2

        raise TimeoutError(
            f"[image] no aparecieron imágenes finales en {deadline}s. "
            f"srcs vistos: {srcs[:3]} | panel: {last_panel_state}"
        )

    async def _list_visible_result_images(self, page: Page) -> list[str]:
        try:
            return await page.evaluate(
                """
                ({ resultImageSelector }) => {
                  const visible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    if (style.visibility === "hidden" || style.display === "none") return false;
                    return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                  };

                  return Array.from(document.querySelectorAll(resultImageSelector))
                    .filter(visible)
                    .map((img) => ({ src: img.src, top: img.getBoundingClientRect().top }))
                    .filter((x) => !!x.src)
                    .sort((a, b) => a.top - b.top)
                    .map((x) => x.src);
                }
                """,
                {"resultImageSelector": self.RESULT_IMAGE},
            )
        except PlaywrightError as exc:
            if self._is_transient_navigation_context_error(exc):
                return []
            raise

    async def _read_active_panel_state(self, page: Page, prompt: str) -> dict[str, Any]:
        return await page.evaluate(
            """
            ({ detailsPanelSelector, resultImageSelector, failureMarkers, prompt }) => {
              const visible = (el) => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (style.visibility === "hidden" || style.display === "none") return false;
                return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
              };

              const panels = Array.from(document.querySelectorAll(detailsPanelSelector)).filter(visible);
              if (!panels.length) {
                return {
                  has_panel: false,
                  has_failure: false,
                  failure_marker: null,
                  job_id: null,
                  image_srcs: [],
                  panel_count: 0,
                  prompt_match: false,
                  has_any_signal_panel: false,
                  global_image_srcs: [],
                  signal_panel: {
                    has_failure: false,
                    failure_marker: null,
                    job_id: null,
                    image_srcs: [],
                  },
                };
              }

              const states = panels.map((panel) => {
                const text = panel.innerText || "";
                const failureMarker = failureMarkers.find((m) => text.includes(m)) || null;
                const imageSrcs = Array.from(panel.querySelectorAll(resultImageSelector))
                  .map((img) => img.src)
                  .filter(Boolean);
                const jobHref = Array.from(panel.querySelectorAll('a[href*="/image-gen/genai-image/"]'))
                  .map((a) => a.getAttribute("href"))
                  .find(Boolean) || "";
                const match = jobHref.match(/\\/image-gen\\/genai-image\\/([0-9a-f-]+)/);
                const rect = panel.getBoundingClientRect();

                return {
                  text,
                  failureMarker,
                  imageSrcs,
                  jobId: match ? match[1] : null,
                  promptMatch: !!(prompt && text.includes(prompt)),
                  top: rect.top,
                  area: rect.width * rect.height,
                };
              });

              states.sort((a, b) => {
                if (a.promptMatch !== b.promptMatch) return a.promptMatch ? -1 : 1;
                if (a.imageSrcs.length !== b.imageSrcs.length) return b.imageSrcs.length - a.imageSrcs.length;
                if (a.area !== b.area) return b.area - a.area;
                return a.top - b.top;
              });

              const chosen = states[0];
              const signalPanels = states.filter((s) => s.failureMarker || s.imageSrcs.length || s.jobId);
              signalPanels.sort((a, b) => {
                if (!!a.failureMarker !== !!b.failureMarker) return a.failureMarker ? -1 : 1;
                if (a.imageSrcs.length !== b.imageSrcs.length) return b.imageSrcs.length - a.imageSrcs.length;
                if (a.area !== b.area) return b.area - a.area;
                return a.top - b.top;
              });
              const bestSignal = signalPanels[0] || null;

              const globalImageSrcs = Array.from(document.querySelectorAll(resultImageSelector))
                .filter(visible)
                .map((img) => ({ src: img.src, top: img.getBoundingClientRect().top }))
                .filter((x) => !!x.src)
                .sort((a, b) => a.top - b.top)
                .map((x) => x.src);

              return {
                has_panel: true,
                has_failure: !!chosen.failureMarker,
                failure_marker: chosen.failureMarker,
                job_id: chosen.jobId,
                image_srcs: chosen.imageSrcs,
                panel_count: states.length,
                prompt_match: chosen.promptMatch,
                has_any_signal_panel: !!bestSignal,
                global_image_srcs: globalImageSrcs,
                signal_panel: bestSignal ? {
                  has_failure: !!bestSignal.failureMarker,
                  failure_marker: bestSignal.failureMarker,
                  job_id: bestSignal.jobId,
                  image_srcs: bestSignal.imageSrcs,
                } : {
                  has_failure: false,
                  failure_marker: null,
                  job_id: null,
                  image_srcs: [],
                },
              };
            }
            """,
            {
                "detailsPanelSelector": self.DETAILS_PANEL,
                "resultImageSelector": self.RESULT_IMAGE,
                "failureMarkers": list(self.FAST_FAILURE_MARKERS),
                "prompt": prompt,
            },
        )

    @staticmethod
    def _is_transient_navigation_context_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return "execution context was destroyed" in msg or "most likely because of a navigation" in msg

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
