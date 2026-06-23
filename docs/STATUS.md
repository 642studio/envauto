# Estado del proyecto

Última actualización: 2026-06-23

## Funciona (end-to-end por la API)

**6 generadores validados** con jobs que terminan `completed` y devuelven un asset real descargable, servido en `/files/`:

| Generador | Salida | Opciones soportadas |
| --------- | ------ | ------------------- |
| `image`    | jpg/png/webp | `aspect_ratio`, `variations` (1/3), `reference_images` (hasta 5) |
| `video`    | mp4   | `aspect_ratio` (16:9/9:16), `audio`, `first_frame`, `last_frame`*, `reference_images` |
| `sound`    | mp3   | `duration` (1–25s), `loop` |
| `music`    | mp3   | `energy` (auto…very high) |
| `graphics` | png   | `aspect_ratio`, `variations`, `reference_images`, `transparent_background`** |

\* `last_frame` (videoGen) suele estar deshabilitado en la cuenta (rollout de Envato); si lo está, se omite con warning.
\*\* `transparent_background` activa la opción en Envato pero el PNG descargado sale opaco (la transparencia real solo está en el SVG, aún no automatizable).

- `voice` ── se maneja por fuera con ElevenLabs.
- `mockup` ── pendiente (vive en `labs.envato.com`, mapeo desde cero).

Además: API REST con bearer token, job queue serial (`queued → running → completed | failed`), debug automático en fallos (screenshot + HTML + URL en `storage/debug/`), deploy a VPS con Docker.

## Causa raíz de los bloqueos (resueltos)

Lo que tenía trabado al proyecto NO era detección anti-bot/headless (hipótesis vieja, **descartada**: se generan assets en headless sin problema). Eran bugs concretos:

1. **API no arrancaba en Python 3.9** ── los modelos Pydantic usaban `float | None`, que Pydantic evalúa en runtime y no existe en 3.9. → `Optional[...]` en `schemas.py`.
2. **Un overlay tapaba el botón Generate** (`image-gen-shortcuts-feature-callout`) e interceptaba el click → la generación nunca arrancaba. → se cierra en `navigate()`.
3. **El editor de prompt ignoraba el primer `keyboard.type`** si la página recién cargó → el POST de generación salía con `prompt=` vacío y Envato fallaba EN SILENCIO (se veía sobre todo en video). → `_type_prompt` escribe y VERIFICA con reintento.
4. **Un navegador/contexto de larga vida** hacía que Envato rechazara en silencio las generaciones de video. → se lanza un Chromium fresco por job.
5. **El SessionKeeper guardaba `storage_state` concurrentemente** sobre el contexto del job en curso (rompía generaciones de >60s). → se eliminó ese save periódico (cada job persiste al cerrar su contexto).

Ver `docs/TROUBLESHOOTING.md` para el detalle de cada uno.

## Roadmap

### Próximos generadores

- `mockupGen` ── `https://labs.envato.com/apps/mockup-gen/` (subdominio aparte, mapear desde cero).
- `voiceGen` ── no se implementa (se usa ElevenLabs).

### Mejoras pendientes

- graphics: bajar PNG con alpha real (hoy opaco) o exponer el SVG transparente.
- music: soportar `genres`, `themes`, `include_lyrics` (pickers multi-select).
- Devolver/exponer las N variaciones (hoy se baja solo la primera).

### Mejoras de robustez

- Métricas: tiempo promedio por generador, tasa de éxito, expiraciones de sesión por semana.
- Reintentos automáticos cuando un job falla con timeout (con limit).
- Webhook callbacks: en vez de polling, configurar URL para que se notifique cuando un job termine.
- Persistencia de la cola en SQLite para sobrevivir restarts del contenedor (hoy se pierde todo).
- Multi-context para procesar más de un job a la vez si el throughput lo justifica.

### Ergonomía

- Un mini frontend HTML servido en `/` para encolar jobs y ver resultados sin curl.
- Comando `docker compose run cli login` para hacer login dentro del contenedor cuando no se quiere repetir el ciclo desde local.
- Auto-refresh de sesión: detectar cuando faltan días para expirar y avisar por email/Slack.

## Decisiones tomadas

- **Browser automation, no API reverse engineering**. Es más estable y no requiere reverse-engineerear tokens internos.
- **Una sola cola, un solo worker**. Más simple. Multi-worker viene si hace falta.
- **Output como URLs locales servidas por la propia API**, no S3. Para v1 alcanza, S3 viene si se necesita.
- **Login interactivo en local + storage_state al VPS**. La única forma sensata de manejar 2FA sin reescribir flujos auth.
- **Python 3.9+ como mínimo**. Compatible con macOS system Python para reducir fricción de setup.
- **Docker como único método de deploy soportado**. Evita variaciones de entorno entre VPS.

## Decisiones pendientes

- ¿Versión final del proxy delante del puerto 8000? Caddy con HTTPS automático es la opción default si se expone a Internet.
- ¿Persistencia de jobs? Hoy son in-memory. Si los jobs duran horas o el contenedor reinicia, se pierde el historial.
- ¿Cómo manejar generaciones que devuelven múltiples archivos? imageGen devuelve N variaciones. Hoy bajamos solo la primera. Discutir si conviene zip o devolver lista.
