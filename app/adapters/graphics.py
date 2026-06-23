"""Adapter para Envato AI - graphicsGen (https://app.envato.com/graphics-gen).

graphicsGen es prácticamente idéntico a imageGen: mismos chips de aspect_ratio y
variations, mismo botón de referencias, y el resultado son img[alt="Generated Image"]
en gen-assets-resized (misma detección). Por eso hereda de ImageGenAdapter.

Diferencias propias:
- `transparent_background`: control que muestra "Solid" por defecto y ofrece
  "Transparent" al abrirlo (el POST manda `transparent_background`).
- La descarga usa el menú de Envato (botón item-action-download → "Original size") en
  vez de bajar el src resized, que es JPEG (format=auto) recomprimido. "Original size"
  entrega el PNG nativo a resolución completa.

Limitación conocida: con transparent_background=true la opción se activa en Envato,
pero el export raster "Original size" sale como PNG RGB con fondo blanco (sin alpha).
La transparencia real solo está en el SVG, cuya descarga no es automatizable de forma
confiable (el download event no se dispara en tiempo). Queda pendiente.
"""
from __future__ import annotations

from typing import Any

from loguru import logger
from playwright.async_api import Page

from app.adapters.base import GenerationResult
from app.adapters.image import ImageGenAdapter
from app.core.storage import new_asset_path, public_url


class GraphicsGenAdapter(ImageGenAdapter):
    name = "graphics"

    URL = "https://app.envato.com/graphics-gen"

    DOWNLOAD_BUTTON = '[data-cy="item-action-download"]'

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
            logger.info("[graphics] fondo transparente activado (nota: el PNG sale opaco)")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[graphics] no pude activar fondo transparente: {}", exc)

    async def download(self, page: Page, meta: dict[str, Any]) -> GenerationResult:
        """Baja la primera variación eligiendo "Original size" en el menú de descarga
        (PNG nativo a resolución completa). Cae al fetch del src (imageGen) si falla."""
        try:
            dl_btn = page.locator(self.DOWNLOAD_BUTTON).first
            await dl_btn.wait_for(state="visible", timeout=10_000)
            await dl_btn.click()  # abre el menú (Original size / Upscale 2x / 4x / SVG)
            original = page.locator('[data-cy="download-original"]').first
            await original.wait_for(state="visible", timeout=5_000)
            async with page.expect_download(timeout=30_000) as dl_info:
                await original.click()
            download = await dl_info.value
            name = download.suggested_filename
            suffix = "." + (name.rsplit(".", 1)[-1] if "." in name else "png")
            target = new_asset_path(self.name, suffix)
            await download.save_as(str(target))
            logger.info("[graphics] descargado {} -> {}", name, target.name)
            return GenerationResult(
                asset_url=public_url(target),
                asset_local_path=str(target),
                metadata={**meta, "suggested_filename": name, "downloaded_from": download.url},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[graphics] descarga por menú falló ({}), uso fetch del src", exc)
            return await super().download(page, meta)
