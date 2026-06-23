# Selectores DOM por generador

Mapa de los selectores CSS/ARIA que usa cada adapter, junto con notas de estabilidad y cómo los descubrimos. Sirve de referencia rápida cuando Envato cambia algo en la UI.

## Patrones generales (válidos para toda app.envato.com)

Los siete generadores principales (`image`, `image-edit`, `video`, `music`, `voice`, `sound`, `graphics`) viven en `https://app.envato.com/{nombre}-gen` y comparten el mismo React shell. Los selectores que listamos abajo son, hasta donde sabemos, los mismos para todos.

**Excepción**: `mockup-gen` vive en otro subdominio (`https://labs.envato.com/apps/mockup-gen/`) y es otra aplicación entera. Sus selectores serán diferentes.

### Atributos que conviene usar

Las clases CSS son hashes generados (ej. `ds-b1394h55g`) y cambian entre builds. **No usarlas como selectores**. Lo que sí es estable:

- `data-cy="..."` ── data-cy es el sistema de testing de Cypress, los marcamos en el frontend para tests E2E. Muy estable.
- `data-analytics-name="..."` ── nombre del evento que dispara analytics. Estable porque las métricas dependen de eso.
- `data-analytics-context="..."` ── contexto de analytics. Estable por el mismo motivo.
- `aria-label="..."` ── accesibilidad. Estable y semántico.
- `role="..."` ── ARIA roles. Estables.
- `type="..."` en buttons. Estable.
- `alt="..."` en imágenes. Estable.

### Selectores compartidos (probables)

Estos los confirmamos en imageGen y muy probablemente apliquen al resto de los generadores `*-gen` en `app.envato.com`:

| Elemento | Selector |
| -------- | -------- |
| Prompt input (contenteditable div) | `[data-cy="prompt-input"]` |
| Submit button | `button[data-analytics-name="gen_click"]:visible` (texto "Generate") |
| Chips de opciones | `[data-cy="...-chip"]` (role=combobox) que abre `[data-cy="...-dropdown"]` |
| Items del dropdown | `<button>` con texto exacto del valor, DENTRO del dropdown visible |
| Overlay a cerrar al cargar | `[data-cy="image-gen-shortcuts-feature-callout-close"]` (tapa Generate) |
| Resultado (imágenes) | `img[alt="Generated Image"]`, src en `gen-assets-resized.envatousercontent.com` |
| Resultado (videos) | `<video>` dentro de `[data-cy="item-card"]`, src en `gen-assets.app.envatousercontent.com` |
| Cards de la galería | `[data-cy="item-card"]` con `[data-cy="item-action-download"]` |

**Importante**: la URL **no** cambia al generar (antes iba a `/image-gen/genai-image/{uuid}`). El resultado se detecta por asset nuevo en la galería respecto de un baseline tomado antes de Generate. El prompt se escribe con `keyboard.type` Y se verifica con reintento (el editor a veces ignora el primer intento → POST con prompt vacío). El completado de video/sound/music llega por WebSocket a `wss://ai-platform.app.envato.com/ws`.

## imageGen

URL: `https://app.envato.com/image-gen`. Mapeado contra UI real (junio 2026).

```python
PROMPT_INPUT = '[data-cy="prompt-input"]'
SUBMIT_BUTTON = 'button[type="submit"][data-analytics-name="gen_click"]:visible'
CALLOUT_CLOSE = '[data-cy="image-gen-shortcuts-feature-callout-close"]'
RESULT_IMAGE = 'img[alt="Generated Image"]'
ASPECT_RATIO_CHIP = '[data-cy="aspect-ratio-chip"]'      # dropdown: [data-cy="aspect-ratio-dropdown"]
VARIATIONS_CHIP   = '[data-cy="variations-chip"]'        # dropdown: [data-cy="variations-dropdown"]
REFERENCE_BUTTON  = '[data-cy="reference-images-button"]'  # revela input[type=file] multiple
FINAL_SRC_PATTERN = r"gen-assets(-resized)?\.envatousercontent\.com"
```

Opciones (texto exacto de la opción en el dropdown):
- `aspect_ratio`: `Auto`, `1:1`, `16:9`, `9:16`, `4:3`, `3:4`, `3:2`, `2:3` (notación directa, ya NO "Square"/"Landscape").
- `variations`: `1 Variation`, `3 Variations` (solo 1 y 3).
- `reference_images`: click `reference-images-button` → `input[type=file].set_input_files([...])` (acepta jpeg/png/webp, multiple, hasta 5).

Descarga: `page.request.get(src)` (reutiliza cookies). La extensión se saca del Content-Type real (jpg/webp/png), no se asume .png.

## videoGen

URL: `https://app.envato.com/video-gen`.

```python
SUBMIT_BUTTON = 'button[data-analytics-name="gen_click"]:visible'
ASPECT_RATIO_CHIP = '[data-cy="video-aspect-ratio-chip"]'   # 16:9, 9:16
AUDIO_CHIP        = '[data-cy="video-audio-chip"]'          # "With audio" / "No audio"
# Uploads por pestañas (botones por texto): "Start frame", "End frame", "Images"
#   - cada pestaña revela un input[type=file]; "Images" es multiple (refs), frames son single
#   - "End frame" suele estar disabled (rollout) → se omite
```

Resultado: `<video>` dentro de `[data-cy="item-card"]`, src en `gen-assets.app.envatousercontent.com` (URL firmada S3, expira ~1h). Detección: primer item-card con `<video src>` nuevo vs baseline. Descarga: `page.request.get(video_src)` → `.mp4`.

## soundGen / musicGen

URLs: `https://app.envato.com/sound-gen`, `https://app.envato.com/music-gen`. Salida: audio (mp3).

```python
# soundGen
DURATION_CHIP = '[data-cy="duration-chip"]'   # popover con [role=slider] aria-valuenow (0–25s); flechas del teclado
# botón "Loop" (toggle)
# musicGen
ENERGY_CHIP = '[data-cy="music-energy-chip"]'  # dropdown: Auto/Muted/Low/Medium/High/Very High
# botones "Genre" / "Themes" (pickers multi-select, no mapeados) y "No lyrics" (toggle)
```

Resultado: la generación agrega item-cards (sound = 5 variaciones, music = 3). Los `<audio>` cargan lazy (sin src en el DOM), así que el audio se baja con el botón `[data-cy="item-action-download"]`, que dispara un **download event** de Playwright (requiere `accept_downloads=True` en el contexto). Detección: aumento de `[data-cy="item-card"]` vs baseline.

## graphicsGen

URL: `https://app.envato.com/graphics-gen`. Casi idéntico a imageGen (mismos chips aspect_ratio/variations, reference-images-button, resultado `img[alt="Generated Image"]`); el adapter **hereda de imageGen**.

- Extra: `transparent_background` ── botón aria-label "Solid" que al abrirse ofrece "Transparent".
- Descarga: el botón `item-action-download` abre un menú con `[data-cy="download-original"]` (PNG nativo), `download-upscale-2x`, `download-upscale-4x`, `download-svg`. Se usa "Original size". La transparencia real solo vive en el SVG (el download-svg no dispara download event de forma confiable → pendiente).

## voiceGen

No se implementa: el caso de voz se maneja por fuera con ElevenLabs.

## mockupGen (caso aparte)

URL: `https://labs.envato.com/apps/mockup-gen/`

Esto es otra aplicación bajo otro subdominio. Probablemente tiene su propio shell, sus propios selectores. **Hay que mapear desde cero** cuando se implemente. No asumir que los `data-cy` de app.envato.com aplican acá.

## Cómo descubrir selectores nuevos

Si Envato cambia algo o querés mapear un generador nuevo:

1. Abrí Chrome con tu sesión de Envato logueada.
2. Conectá Claude in Chrome (o usá las DevTools manualmente).
3. Probá selectores en la consola con `document.querySelector(...)`.
4. Buscá los `data-cy`, `data-analytics-name`, `aria-label` antes de caer a clases CSS.
5. Validá que el selector matchea **uno solo** elemento, o filtrá con `:visible` y `.first` cuando hay versiones desktop/mobile en paralelo.

Pasos en script:

```javascript
// en consola del browser
const promptInput = document.querySelector('[data-cy="prompt-input"]');
console.log({
  exists: !!promptInput,
  tag: promptInput?.tagName,
  attrs: Array.from(promptInput?.attributes ?? []).map(a => `${a.name}=${a.value}`)
});
```
