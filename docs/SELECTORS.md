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
| Submit button | `button[type="submit"][data-analytics-name="gen_click"]:visible` |
| Comboboxes de opciones | `<button>` cuyo texto refleja el valor actual ("Square", "3 Variations", "Style") |
| Items del menú de opciones | `role="option"` con `name` igual al valor |

## imageGen

URL: `https://app.envato.com/image-gen`

Confirmado y mapeado contra la UI real (mayo 2026).

### Flujo

1. Navegar a `/image-gen`.
2. Click en `[data-cy="prompt-input"]` para enfocarlo.
3. `fill()` el prompt en el contenteditable (Playwright lo soporta).
4. Configurar opciones (aspect ratio, count, style) clickeando los comboboxes y los items.
5. Si aparece Cookiebot, cerrarlo antes de submit (decline/reject).
6. Click en `button[type="submit"][data-analytics-name="gen_click"]:visible` (texto "Generate").
7. Resultado: si la URL cambia a `/image-gen/genai-image/{uuid}`, se usa ese path; si no cambia, se resuelve leyendo el panel activo.
8. Éxito: `img[alt="Generated Image"]` dentro de `[data-cy="details-panel"]` con src en `gen-assets*.envatousercontent.com`.
9. Falla explícita: texto `All generations failed` o `Try again` en el panel activo (fail fast).

### Selectores

```python
URL = "https://app.envato.com/image-gen"
PROMPT_INPUT = '[data-cy="prompt-input"]'
SUBMIT_BUTTON = 'button[type="submit"][data-analytics-name="gen_click"]:visible'
RESULT_IMAGE = 'img[alt="Generated Image"]'
DETAILS_PANEL = '[data-cy="details-panel"]'

JOB_URL_PATTERN = r"/image-gen/genai-image/([0-9a-f-]+)"
FINAL_SRC_PATTERN = r"gen-assets-resized\.envatousercontent\.com|gen-assets\.envatousercontent\.com"
```

### Selectores de Cookiebot (bloqueo de submit)

Si el banner/modal de consentimiento está activo, intercepta el click del botón Generate.

```python
COOKIE_DIALOG = "#CybotCookiebotDialog"
COOKIE_DIALOG_ACTIVE = "#CybotCookiebotDialog.CybotCookiebotDialogActive"
COOKIE_REJECT_BUTTON = "#CybotCookiebotDialogBodyButtonDecline"
COOKIE_REJECT_BUTTON_TEXT = "Reject all"
```

El helper común intenta cerrar Cookiebot en `navigate` y justo antes de `submit`.

### Opciones soportadas

Detectadas como botones-combobox cuyo texto muestra el valor actual:

| Param | Valores posibles | Texto del botón |
| ----- | ---------------- | --------------- |
| `aspect_ratio` | `1:1` | `Square` |
| `aspect_ratio` | `16:9` | `Landscape` |
| `aspect_ratio` | `9:16` | `Portrait` |
| `aspect_ratio` | `4:3` | `Standard` |
| `aspect_ratio` | `3:4` | `Tall` |
| `variations` | 1 a 4 | `1 Variation` / `2 Variations` / `3 Variations` / `4 Variations` |
| `style` | nombres del picker | varía según presets de Envato |

### Descarga

En lugar de pelear con el menú nativo del navegador, usamos `page.request.get(src)` que reutiliza las cookies del contexto y baja directo. Más rápido y predecible.

```python
src = first(image_srcs)
original = src.replace("gen-assets-resized.", "gen-assets.")
response = await page.request.get(original)
target.write_bytes(await response.body())
```

## videoGen, musicGen, voiceGen, soundGen, graphicsGen

URLs:
- `https://app.envato.com/video-gen`
- `https://app.envato.com/music-gen`
- `https://app.envato.com/voice-gen`
- `https://app.envato.com/sound-gen`
- `https://app.envato.com/graphics-gen`

**No mapeados todavía**. Cuando se implementen, intentar primero los mismos selectores que imageGen y solo cambiar el patrón de URL del job (`/video-gen/genai-video/{uuid}`, etc.) y el alt de los assets.

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
