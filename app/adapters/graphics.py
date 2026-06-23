"""Adapter para Envato AI - graphicsGen (https://app.envato.com/graphics-gen).

graphicsGen es prácticamente idéntico a imageGen: mismos chips de aspect_ratio y
variations, mismo botón de referencias, y el resultado son img[alt="Generated Image"]
en gen-assets-resized (misma detección y descarga). Por eso hereda de ImageGenAdapter.

Lo único propio es `transparent_background`: un control cuyo valor por defecto es
"Solid" y que, al abrirlo, ofrece la opción "Transparent" (el POST manda
`transparent_background`).
"""
from __future__ import annotations

from typing import Any

from loguru import logger
from playwright.async_api import Page

from app.adapters.image import ImageGenAdapter


class GraphicsGenAdapter(ImageGenAdapter):
    name = "graphics"

    URL = "https://app.envato.com/graphics-gen"

    async def _extra_options(self, page: Page, payload: dict[str, Any]) -> None:
        """Activa el fondo transparente si se pide. El control muestra 'Solid' por
        defecto; al clickearlo aparece la opción 'Transparent'."""
        if payload.get("transparent_background") is not True:
            return
        solid = page.get_by_role("button", name="Solid", exact=True)
        if not await solid.count():
            logger.info("[graphics] el fondo ya no es 'Solid', no toco transparencia")
            return
        try:
            await solid.first.click()
            await page.get_by_role("button", name="Transparent", exact=True).first.click(timeout=5_000)
            logger.info("[graphics] fondo transparente activado")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[graphics] no pude activar fondo transparente: {}", exc)
