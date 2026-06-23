"""Adapter para Envato AI - musicGen (https://app.envato.com/music-gen).

Mapeo confirmado contra la UI real (junio 2026):

- El POST a /music-gen.data manda `prompt`, `genres`, `themes`, `energy`, `include_lyrics`.
- v1 soporta `prompt` y `energy` (chip + dropdown: Auto/Muted/Low/Medium/High/Very High).
  `genres`, `themes` (pickers multi-select) y `include_lyrics` quedan para una iteración
  futura.
- Una generación produce 3 variaciones (item-cards). Como en soundGen, los <audio>
  cargan lazy, así que el audio se baja con el botón item-action-download (download
  event de Playwright). Se baja la primera variación.
"""
from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger
from playwright.async_api import Page

from app.adapters.base import GenerationResult, GeneratorAdapter
from app.config import settings
from app.core.storage import new_asset_path, public_url


class MusicGenAdapter(GeneratorAdapter):
    name = "music"

    URL = "https://app.envato.com/music-gen"

    PROMPT_INPUT = '[data-cy="prompt-input"]'
    SUBMIT_BUTTON = 'button[data-analytics-name="gen_click"]:visible'
    CALLOUT_CLOSE = '[data-cy="image-gen-shortcuts-feature-callout-close"]'
    ITEM_CARD = '[data-cy="item-card"]'
    DOWNLOAD_BUTTON = '[data-cy="item-action-download"]'

    ENERGY_CHIP = '[data-cy="music-energy-chip"]'
    ENERGY_DROPDOWN = '[data-cy="music-energy-dropdown"]'
    ENERGY_VALUES = {
        "auto": "Auto",
        "muted": "Muted",
        "low": "Low",
        "medium": "Medium",
        "high": "High",
        "very high": "Very High",
        "very_high": "Very High",
    }

    async def navigate(self, page: Page) -> None:
        logger.info("[music] navegando a {}", self.URL)
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
                logger.info("[music] callout cerrado")
        except Exception as exc:  # noqa: BLE001
            logger.debug("[music] no hubo callout que cerrar: {}", exc)

    async def submit(self, page: Page, payload: dict[str, Any]) -> None:
        prompt: str = payload["prompt"]
        logger.info("[music] enviando prompt ({} chars)", len(prompt))

        self._baseline_cards = await page.locator(self.ITEM_CARD).count()
        logger.info("[music] baseline: {} cards previos", self._baseline_cards)

        prompt_box = page.locator(self.PROMPT_INPUT).first
        await self._type_prompt(page, prompt_box, prompt)

        energy = payload.get("energy")
        if energy:
            key = str(energy).strip().lower()
            if key in self.ENERGY_VALUES:
                await self._select_chip_option(
                    page, self.ENERGY_CHIP, self.ENERGY_DROPDOWN, self.ENERGY_VALUES[key]
                )
            else:
                logger.warning(
                    "[music] energy '{}' no soportado (válidos: {})",
                    energy, sorted({v for v in self.ENERGY_VALUES.values()}),
                )

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
                    logger.info("[music] prompt escrito en intento {}", attempt + 1)
                return
            logger.warning("[music] el editor no tomó el prompt (intento {}), reintento", attempt + 1)
            await asyncio.sleep(0.8)
        raise RuntimeError("[music] no pude escribir el prompt en el editor tras 3 intentos")

    async def _select_chip_option(
        self, page: Page, chip_selector: str, dropdown_selector: str, option_text: str
    ) -> None:
        """Abre un chip-combobox y clickea la opción de texto exacto dentro del dropdown
        visible (mismo patrón que imageGen/videoGen)."""
        logger.info("[music] seleccionando '{}' en {}", option_text, chip_selector)
        await page.locator(f"{chip_selector}:visible").first.click()
        dropdown = page.locator(f"{dropdown_selector}:visible").first
        await dropdown.wait_for(state="visible", timeout=5_000)
        await dropdown.get_by_role("button", name=option_text, exact=True).first.click(timeout=5_000)

    async def wait_for_result(self, page: Page) -> dict[str, Any]:
        """Espera a que aparezcan item-cards nuevos (la generación agrega variaciones)."""
        logger.info("[music] esperando resultado (puede tardar ~1 min)")
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
                    logger.info("[music] resultado listo ({} cards)", count)
                    return {"new_cards": count - baseline, "page_url": page.url}
            await asyncio.sleep(3)
            elapsed += 3

        raise TimeoutError(
            f"[music] no aparecieron resultados nuevos en {deadline:.0f}s."
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
        logger.info("[music] descargado {} -> {}", download.suggested_filename, target.name)
        return GenerationResult(
            asset_url=public_url(target),
            asset_local_path=str(target),
            metadata={
                **meta,
                "suggested_filename": download.suggested_filename,
                "downloaded_from": download.url,
            },
        )
