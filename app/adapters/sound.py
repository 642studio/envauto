"""Adapter para Envato AI - soundGen (https://app.envato.com/sound-gen).

Mapeo confirmado contra la UI real (junio 2026):

- Opciones: `duration` (slider role=slider, 0–25s, default 5) y `loop` (botón toggle).
  El POST a /sound-gen.data manda `prompt`, `duration_seconds`, `loop`.
- Una generación produce 5 variaciones (5 item-cards). Los <audio> cargan lazy, así
  que NO tienen src en el DOM; el audio se baja con el botón `item-action-download`,
  que dispara un download event de Playwright con la URL real del .mp3.
- Bajamos la primera variación (consistente con imageGen). El resto queda listado en
  metadata para una iteración futura.
"""
from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger
from playwright.async_api import Page

from app.adapters.base import GenerationResult, GeneratorAdapter
from app.config import settings
from app.core.storage import new_asset_path, public_url


class SoundGenAdapter(GeneratorAdapter):
    name = "sound"

    URL = "https://app.envato.com/sound-gen"

    PROMPT_INPUT = '[data-cy="prompt-input"]'
    SUBMIT_BUTTON = 'button[data-analytics-name="gen_click"]:visible'
    CALLOUT_CLOSE = '[data-cy="image-gen-shortcuts-feature-callout-close"]'
    ITEM_CARD = '[data-cy="item-card"]'
    DOWNLOAD_BUTTON = '[data-cy="item-action-download"]'

    DURATION_CHIP = '[data-cy="duration-chip"]'
    DURATION_POPOVER = '[data-cy="duration-slider-popover"]'
    DURATION_MIN = 0
    DURATION_MAX = 25
    DURATION_DEFAULT = 5

    async def navigate(self, page: Page) -> None:
        logger.info("[sound] navegando a {}", self.URL)
        await page.goto(self.URL, wait_until="domcontentloaded")
        if "sign_in" in page.url or "/login" in page.url:
            raise RuntimeError(
                "La sesión de Envato no es válida. Re-correr scripts/login.py."
            )
        await page.locator(self.PROMPT_INPUT).first.wait_for(state="visible")
        close_btn = page.locator(self.CALLOUT_CLOSE)
        try:
            if await close_btn.count():
                await close_btn.first.click(timeout=3_000)
                logger.info("[sound] callout cerrado")
        except Exception as exc:  # noqa: BLE001
            logger.debug("[sound] no hubo callout que cerrar: {}", exc)

    async def submit(self, page: Page, payload: dict[str, Any]) -> None:
        prompt: str = payload["prompt"]
        logger.info("[sound] enviando prompt ({} chars)", len(prompt))

        # Baseline: cuántos item-cards hay antes de generar. La generación agrega 5
        # variaciones nuevas arriba; detectamos el resultado por el aumento de cards.
        self._baseline_cards = await page.locator(self.ITEM_CARD).count()
        logger.info("[sound] baseline: {} cards previos", self._baseline_cards)

        prompt_box = page.locator(self.PROMPT_INPUT).first
        await self._type_prompt(page, prompt_box, prompt)

        duration = payload.get("duration")
        if isinstance(duration, (int, float)):
            await self._set_duration(page, int(duration))

        if payload.get("loop") is True:
            await self._toggle_loop(page)

        await page.locator(self.SUBMIT_BUTTON).first.click()

    async def _type_prompt(self, page: Page, prompt_box, prompt: str) -> None:
        """Escribe el prompt y verifica que el editor lo capturó; reintenta si quedó
        vacío (el editor rich-text a veces ignora el primer intento → POST sin prompt)."""
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
                    logger.info("[sound] prompt escrito en intento {}", attempt + 1)
                return
            logger.warning("[sound] el editor no tomó el prompt (intento {}), reintento", attempt + 1)
            await asyncio.sleep(0.8)
        raise RuntimeError("[sound] no pude escribir el prompt en el editor tras 3 intentos")

    async def _set_duration(self, page: Page, seconds: int) -> None:
        """Setea la duración moviendo el slider (role=slider) con flechas del teclado."""
        seconds = max(self.DURATION_MIN, min(self.DURATION_MAX, seconds))
        await page.locator(f"{self.DURATION_CHIP}:visible").first.click()
        popover = page.locator(f"{self.DURATION_POPOVER}:visible").first
        await popover.wait_for(state="visible", timeout=5_000)
        slider = popover.locator('[role="slider"]').first
        await slider.wait_for(state="visible", timeout=3_000)
        current = int(await slider.get_attribute("aria-valuenow") or self.DURATION_DEFAULT)
        await slider.focus()
        delta = seconds - current
        key = "ArrowRight" if delta > 0 else "ArrowLeft"
        for _ in range(abs(delta)):
            await page.keyboard.press(key)
            await asyncio.sleep(0.02)
        final = int(await slider.get_attribute("aria-valuenow") or current)
        logger.info("[sound] duración seteada a {}s (pedido {}s)", final, seconds)
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.3)

    async def _toggle_loop(self, page: Page) -> None:
        """Activa el modo loop (botón 'Loop'). Solo lo clickea si no está ya activo."""
        loop_btn = page.get_by_role("button", name="Loop", exact=True).first
        try:
            pressed = await loop_btn.get_attribute("aria-pressed")
            if pressed == "true":
                return
            await loop_btn.click(timeout=3_000)
            logger.info("[sound] loop activado")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[sound] no pude activar loop: {}", exc)

    async def wait_for_result(self, page: Page) -> dict[str, Any]:
        """Espera a que aparezcan item-cards nuevos (la generación agrega 5 variaciones)."""
        logger.info("[sound] esperando resultado")
        baseline = getattr(self, "_baseline_cards", 0)
        deadline = settings.generation_timeout_ms / 1000
        elapsed = 0.0
        while elapsed < deadline:
            count = await page.locator(self.ITEM_CARD).count()
            generating = await page.evaluate(
                """() => /generating|processing|in progress/i.test(document.body.innerText)"""
            )
            if count > baseline and not generating:
                await asyncio.sleep(2)
                if await page.locator(self.ITEM_CARD).count() > baseline:
                    logger.info("[sound] resultado listo ({} cards)", count)
                    return {"new_cards": count - baseline, "page_url": page.url}
            await asyncio.sleep(3)
            elapsed += 3

        raise TimeoutError(
            f"[sound] no aparecieron resultados nuevos en {deadline:.0f}s."
        )

    async def download(self, page: Page, meta: dict[str, Any]) -> GenerationResult:
        """Baja la primera variación clickeando su botón de descarga (download event)."""
        dl_btn = page.locator(f"{self.DOWNLOAD_BUTTON}").first
        await dl_btn.wait_for(state="visible", timeout=10_000)
        async with page.expect_download(timeout=30_000) as dl_info:
            await dl_btn.click()
        download = await dl_info.value

        suffix = "." + (download.suggested_filename.rsplit(".", 1)[-1] if "." in download.suggested_filename else "mp3")
        target = new_asset_path(self.name, suffix)
        await download.save_as(str(target))
        logger.info("[sound] descargado {} -> {}", download.suggested_filename, target.name)
        return GenerationResult(
            asset_url=public_url(target),
            asset_local_path=str(target),
            metadata={
                **meta,
                "suggested_filename": download.suggested_filename,
                "downloaded_from": download.url,
            },
        )
