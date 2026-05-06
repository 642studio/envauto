"""Adapter para Envato AI - musicGen (https://app.envato.com/music-gen).

Flujo confirmado contra la UI real (mayo 2026):

1. Navegar a /music-gen.
2. Escribir el prompt en el contenteditable [data-cy="prompt-input"].
3. (Opcional) Configurar Género, Temas, Energía y letra con los chips del toolbar.
   - Género y Temas: dropdown con barra de búsqueda + checkboxes (multi-select).
   - Energía: lista radio (Auto / Apagado / Bajo / Medio / Alto / Muy alto).
   - Sin letra / Con letra: botón toggle (no abre dropdown, alterna directamente).
4. Click en button[type="submit"][data-analytics-name="gen_click"] (texto "Generar +").
5. La URL cambia a /music-gen/genai-music/{uuid} mientras genera.
6. El resultado son N pistas de audio (MP3) en envatousercontent.com.
7. Detección vía intercepción de red.
8. La descarga se hace por HTTP directo usando las cookies del contexto.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger
from playwright.async_api import Page, Response

from app.adapters.base import GenerationResult, GeneratorAdapter
from app.config import settings
from app.core.storage import new_asset_path, public_url


class MusicGenAdapter(GeneratorAdapter):
    name = "music"

    URL = "https://app.envato.com/music-gen"

    PROMPT_INPUT = '[data-cy="prompt-input"]'
    SUBMIT_BUTTON = 'button[type="submit"][data-analytics-name="gen_click"]:visible'

    JOB_URL_PATTERN = re.compile(r"/music-gen/genai-music/([0-9a-f-]+)")
    CDN_PATTERN = re.compile(r"envatousercontent\.com")

    # Textos del chip en la toolbar (EN / ES)
    GENRE_CHIP_TEXTS  = ["Genre",  "Género"]
    THEMES_CHIP_TEXTS = ["Themes", "Temas"]
    ENERGY_CHIP_TEXTS = ["Energy", "Energía"]

    # Géneros disponibles (confirmados en UI mayo 2026)
    AVAILABLE_GENRES = [
        "Acústico", "Hip hop", "Beats", "Funk", "Pop", "Drum and bass",
        "Trap", "Tokyo night pop", "Rock", "Latino", "House", "Tropical house",
        "Ambient", "Orquesta", "Electro y dance", "Electrónica", "Techno y trance",
        "Jersey club", "Drill", "R&B", "Lo-fi hip hop", "World music", "Afrobeats",
    ]

    # Temas disponibles (confirmados en UI mayo 2026)
    AVAILABLE_THEMES = [
        "Anuncios y trailers", "Radiodifusión", "Cinematográfico", "Corporativo",
        "Comedia", "Cocina", "Documental", "Moda y belleza", "Videojuegos",
        "Temporada festiva", "Terror y thriller", "Motivacional e inspirador",
        "Naturaleza", "Fotografía", "Deportes y acción", "Tecnología",
        "Viajes", "Tutoriales", "Vlogs", "Bodas y romance", "Entrenamiento y bienestar",
    ]

    # Energías disponibles (confirmadas en UI mayo 2026)
    AVAILABLE_ENERGIES = ["Auto", "Apagado", "Bajo", "Medio", "Alto", "Muy alto"]

    # Estabilidad: cuántos ciclos de 2 s sin nuevos audios antes de considerar completo.
    _STABLE_CYCLES_REQUIRED = 5  # 10 s

    # Conjunto de batch-UUIDs de audio ya vistos en jobs previos (persiste entre
    # llamadas dentro de la misma sesión del contenedor).  Sirve para no re-descargar
    # pistas del historial en el 2.º job en adelante.
    _seen_batch_uuids: set[str] = set()

    # Regex para extraer el batch-UUID de las rutas CDN:
    #  https://gen-assets.app.envatousercontent.com/generated-assets/{batch_uuid}/{file}
    BATCH_UUID_PATTERN = re.compile(
        r"generated-assets/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/"
    )

    def __init__(self) -> None:
        self._detected_urls: list[str] = []
        self._detected_base_urls: set[str] = set()   # dedup por ruta sin query params
        self._response_handler: Any = None
        self._new_batch_uuids: set[str] = set()       # batch-UUIDs capturados en este job

    # ──────────────────────────────── respuesta de red ────────────────────────

    def _make_response_handler(self) -> Any:
        def handler(response: Response) -> None:
            url = response.url
            if not self.CDN_PATTERN.search(url):
                return
            try:
                ct = response.headers.get("content-type", "")
            except Exception:  # noqa: BLE001
                ct = ""
            is_audio = (
                "audio" in ct or "mpeg" in ct or "ogg" in ct or
                any(url.split("?")[0].lower().endswith(ext)
                    for ext in (".mp3", ".wav", ".ogg", ".m4a", ".flac"))
            )
            if not is_audio:
                return
            # Extraer batch-UUID y descartar si ya lo vimos en un job previo
            m_batch = self.BATCH_UUID_PATTERN.search(url)
            batch_uuid = m_batch.group(1) if m_batch else None
            if batch_uuid and batch_uuid in MusicGenAdapter._seen_batch_uuids:
                return  # Pista de historial ya descargada anteriormente
            # Deduplicar por ruta base (las signed URLs cambian en cada request)
            base = url.split("?")[0]
            if base not in self._detected_base_urls:
                self._detected_base_urls.add(base)
                self._detected_urls.append(url)
                if batch_uuid:
                    self._new_batch_uuids.add(batch_uuid)
                logger.info("[music] pista ({}) capturada [batch={}…]: {}…",
                            ct.split(";")[0].strip() or "?",
                            (batch_uuid or "?")[:8], url[:80])
        return handler

    # ──────────────────────────────── ciclo de vida ───────────────────────────

    async def navigate(self, page: Page) -> None:
        logger.info("[music] navegando a {}", self.URL)
        await page.goto(self.URL, wait_until="domcontentloaded")
        if "sign_in" in page.url or "/login" in page.url:
            raise RuntimeError(
                "La sesión de Envato no es válida. Re-correr scripts/login.py."
            )
        await page.locator(self.PROMPT_INPUT).first.wait_for(state="visible")
        # Inyectar hook de History API para capturar el job UUID cuando Envato
        # navega a /music-gen/genai-music/{uuid} (puede ser via pushState).
        await page.evaluate("""() => {
            window.__envatoMusicJobId = null;
            const _capture = (url) => {
                if (!url) return;
                const m = String(url).match(/genai-music\\/([0-9a-f\\-]{36})/);
                if (m) window.__envatoMusicJobId = m[1];
            };
            const orig_push = history.pushState.bind(history);
            history.pushState = function(state, title, url) {
                _capture(url);
                return orig_push(state, title, url);
            };
            const orig_replace = history.replaceState.bind(history);
            history.replaceState = function(state, title, url) {
                _capture(url);
                return orig_replace(state, title, url);
            };
        }""")
        await asyncio.sleep(1.5)

    async def submit(self, page: Page, payload: dict[str, Any]) -> None:
        prompt: str = payload.get("prompt", "")
        if prompt:
            logger.info("[music] enviando prompt ({} chars)", len(prompt))
            prompt_box = page.locator(self.PROMPT_INPUT).first
            await prompt_box.click()
            await prompt_box.fill(prompt)

        # Género (dropdown con búsqueda, multi-select)
        genre = payload.get("genre")
        if genre:
            genres = [genre] if isinstance(genre, str) else genre
            for g in genres:
                await self._select_searchable(page, self.GENRE_CHIP_TEXTS,
                                              "Buscar géneros", g)

        # Temas (dropdown con búsqueda, multi-select)
        themes = payload.get("themes")
        if themes:
            theme_list = [themes] if isinstance(themes, str) else themes
            for t in theme_list:
                await self._select_searchable(page, self.THEMES_CHIP_TEXTS,
                                              "Buscar temas", t)

        # Energía (lista radio, click directo en la opción)
        energy = payload.get("energy")
        if energy:
            await self._select_energy(page, energy)

        # Letra: toggle directo (no abre dropdown)
        lyrics = payload.get("lyrics")
        if lyrics is not None:
            await self._toggle_lyrics(page, want_lyrics=bool(lyrics))

        # Interceptor ANTES del click.
        self._detected_urls = []
        self._detected_base_urls = set()
        self._new_batch_uuids = set()
        self._response_handler = self._make_response_handler()
        page.on("response", self._response_handler)

        await page.locator(self.SUBMIT_BUTTON).first.click()

    # ──────────────────────────────── helpers de UI ───────────────────────────

    async def _find_chip(self, page: Page, chip_texts: list[str]) -> Any:
        """Devuelve el locator del chip que coincida con alguno de los textos."""
        for text in chip_texts:
            loc = page.locator("button:visible").filter(has_text=text).first
            try:
                if await loc.count() > 0:
                    return loc
            except Exception:  # noqa: BLE001
                pass
        return None

    async def _select_searchable(
        self, page: Page, chip_texts: list[str], search_placeholder: str, value: str
    ) -> None:
        """Abre el dropdown con barra de búsqueda y selecciona el valor."""
        chip = await self._find_chip(page, chip_texts)
        if chip is None:
            logger.warning("[music] chip no encontrado: {}", chip_texts)
            return
        try:
            await chip.click(timeout=5_000)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[music] no pude abrir chip {}: {}", chip_texts[0], exc)
            return

        await asyncio.sleep(0.4)

        # Escribir en la barra de búsqueda
        search_box = page.locator(f'input[placeholder*="{search_placeholder}"]').first
        try:
            await search_box.wait_for(state="visible", timeout=3_000)
            await search_box.fill(value)
            await asyncio.sleep(0.3)
        except Exception:  # noqa: BLE001
            pass  # Si no hay buscador, intentamos click directo

        # Clickear el primer resultado que coincida
        try:
            option = page.locator("button:visible, label:visible, li:visible").filter(
                has_text=value
            ).first
            if await option.count() > 0:
                await option.click(timeout=3_000, force=True)
                logger.info("[music] '{}' seleccionado", value)
                # Cerrar dropdown
                await page.keyboard.press("Escape")
                return
        except Exception:  # noqa: BLE001
            pass

        logger.warning("[music] no encontré '{}' en {}", value, chip_texts[0])
        await page.keyboard.press("Escape")

    async def _select_energy(self, page: Page, energy: str) -> None:
        """Abre el chip de Energía y selecciona la opción por texto."""
        chip = await self._find_chip(page, self.ENERGY_CHIP_TEXTS)
        if chip is None:
            logger.warning("[music] chip Energía no encontrado")
            return

        try:
            current = (await chip.inner_text(timeout=2_000)).strip()
            if current.lower() == energy.lower():
                logger.info("[music] energía '{}' ya seleccionada, skip", energy)
                return
        except Exception:  # noqa: BLE001
            pass

        try:
            await chip.click(timeout=5_000)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[music] no pude abrir chip Energía: {}", exc)
            return

        await asyncio.sleep(0.4)

        try:
            option = page.locator("button:visible, li:visible").filter(
                has_text=re.compile(rf"^{re.escape(energy)}$", re.IGNORECASE)
            ).first
            if await option.count() > 0:
                await option.click(timeout=3_000, force=True)
                logger.info("[music] energía seleccionada: {}", energy)
                return
        except Exception:  # noqa: BLE001
            pass

        logger.warning("[music] no encontré energía '{}'", energy)
        await page.keyboard.press("Escape")

    async def _toggle_lyrics(self, page: Page, want_lyrics: bool) -> None:
        """Toggle el botón Sin letra / Con letra al estado deseado."""
        true_texts  = ["Con letra", "With lyrics"]
        false_texts = ["Sin letra", "No lyrics"]
        target_texts   = true_texts  if want_lyrics else false_texts
        opposite_texts = false_texts if want_lyrics else true_texts

        # Buscar el botón toggle (muestra el estado ACTUAL)
        btn = await self._find_chip(page, true_texts + false_texts)
        if btn is None:
            logger.warning("[music] botón de letra no encontrado")
            return

        try:
            current = (await btn.inner_text(timeout=2_000)).strip().lower()
        except Exception:  # noqa: BLE001
            current = ""

        if any(t.lower() in current for t in target_texts):
            logger.info("[music] letra ya en estado deseado ({}), skip",
                        "Con letra" if want_lyrics else "Sin letra")
            return

        # Estado actual es el opuesto → clickear para alternar
        try:
            await btn.click(timeout=5_000)
            logger.info("[music] letra cambiada a {}",
                        "Con letra" if want_lyrics else "Sin letra")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[music] no pude cambiar estado de letra: {}", exc)

    def _cleanup_listener(self, page: Page) -> None:
        if self._response_handler:
            try:
                page.remove_listener("response", self._response_handler)
            except Exception:  # noqa: BLE001
                pass
            self._response_handler = None

    # ──────────────────────────────── wait / download ─────────────────────────

    async def wait_for_result(self, page: Page) -> dict[str, Any]:
        logger.info("[music] esperando resultado (intercepción de red activa)")

        deadline = settings.generation_timeout_ms / 1000
        elapsed = 0.0
        prev_count = 0
        stable_cycles = 0

        # Esperar a que lleguen pistas estables (contando solo las post-skip-window)
        while elapsed < deadline:
            count = len(self._detected_urls)
            if count > 0:
                if count == prev_count:
                    stable_cycles += 1
                    if stable_cycles >= self._STABLE_CYCLES_REQUIRED:
                        logger.info("[music] {} pista(s) estables tras ventana de skip",
                                    count)
                        break
                else:
                    stable_cycles = 0
                    prev_count = count
            await asyncio.sleep(2)
            elapsed += 2

        self._cleanup_listener(page)

        # Intentar obtener el job UUID desde el hook JS (pushState/replaceState)
        job_id: str | None = None
        try:
            job_id = await page.evaluate("() => window.__envatoMusicJobId || null")
        except Exception:  # noqa: BLE001
            pass
        # Fallback: intentar extraer UUID de la URL actual
        if not job_id:
            match = self.JOB_URL_PATTERN.search(page.url)
            job_id = match.group(1) if match else None
        logger.info("[music] job_id Envato: {}", job_id)

        if self._detected_urls:
            all_srcs = list(self._detected_urls)
            # Si tenemos job_id, filtrar solo las pistas de esa generación
            if job_id:
                fresh = [u for u in all_srcs if job_id in u]
                if fresh:
                    logger.info("[music] {} pista(s) filtradas por job_id {}",
                                len(fresh), job_id)
                    return {"envato_job_id": job_id, "audio_srcs": fresh,
                            "page_url": page.url}
                logger.warning("[music] job_id {} no coincide con URLs; usando todas",
                               job_id)
            return {"envato_job_id": job_id, "audio_srcs": all_srcs,
                    "page_url": page.url}

        # Fallback DOM
        fallback = await page.evaluate("""() => {
            const s = new Set();
            document.querySelectorAll('audio[src]').forEach(a => a.src && s.add(a.src));
            document.querySelectorAll('audio source[src]').forEach(x => x.src && s.add(x.src));
            return Array.from(s).filter(u => u.includes('envatousercontent.com'));
        }""")
        if fallback:
            logger.warning("[music] fallback DOM: {} audio(s)", len(fallback))
            return {"envato_job_id": job_id, "audio_srcs": fallback, "page_url": page.url}

        raise TimeoutError(f"[music] no se detectó ninguna pista en {deadline}s.")

    async def download(self, page: Page, meta: dict[str, Any]) -> GenerationResult:
        srcs: list[str] = meta["audio_srcs"]
        if not srcs:
            raise RuntimeError("[music] meta sin audio_srcs")

        asset_urls: list[str] = []
        asset_local_paths: list[str] = []

        for src in srcs:
            target = new_asset_path(self.name, ".mp3")
            try:
                logger.info("[music] descargando {}", src[:100])
                response = await page.request.get(src)
                if response.ok:
                    target.write_bytes(await response.body())
                    asset_local_paths.append(str(target))
                    asset_urls.append(public_url(target))
                    size_mb = target.stat().st_size / 1024 / 1024
                    logger.info("[music] guardado {} ({:.1f} MB)", target.name, size_mb)
                else:
                    logger.warning("[music] HTTP {} para {}", response.status, src[:80])
            except Exception as exc:  # noqa: BLE001
                logger.warning("[music] error descargando {}: {}", src[:80], exc)

        if not asset_urls:
            raise RuntimeError("[music] no se pudo descargar ninguna pista")

        # Registrar los batch-UUIDs descargados para no repetirlos en jobs futuros
        MusicGenAdapter._seen_batch_uuids.update(self._new_batch_uuids)
        logger.info("[music] batch-UUIDs registrados como vistos: {}",
                    list(self._new_batch_uuids))

        return GenerationResult(
            asset_urls=asset_urls,
            asset_local_paths=asset_local_paths,
            metadata={**meta, "downloaded_urls": asset_urls},
        )
