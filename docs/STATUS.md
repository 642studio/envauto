# Estado del proyecto

Última actualización: 2026-05-05

## Funciona

- **Scaffolding completo** del proyecto: FastAPI, BrowserManager con contexto persistente, JobQueue, SessionKeeper, base adapter, login interactivo CLI, Dockerfile, docker-compose.
- **Deploy a VPS Linux** con Docker. Probado en `192.168.1.160` con usuario sin sudo automático en `/opt/`.
- **Sesión persistente de Envato**. El login se hace una vez en local con `scripts/login.py`, se sube el `storage_state.json` al VPS, y el contenedor levanta un Chromium que reutiliza la sesión para todas las peticiones. `/health` reporta `authenticated: true` correctamente.
- **API REST**: `/health`, `POST /generate/{tipo}`, `GET /jobs/{id}`, `POST /admin/storage-state`, `/files/...`. Todo con bearer token salvo `/health` y `/files/...`.
- **Job queue serial** con un solo worker. Encola, ejecuta y reporta status pasando por `queued -> running -> completed | failed`.
- **Debug automático en fallos**: cada job que falla guarda screenshot + HTML + URL en `storage/debug/`, accesibles desde el navegador en `/files/debug/`.
- **Selectores de imageGen mapeados** contra UI real:
  - Prompt input: `[data-cy="prompt-input"]` (contenteditable div).
  - Submit button: `button[type="submit"][data-analytics-name="gen_click"]:visible`.
  - URL pattern del job: `/image-gen/genai-image/{uuid}`.
  - Imágenes resultado: `img[alt="Generated Image"]` con src en `gen-assets-resized.envatousercontent.com`.

## En debug ahora mismo

**Síntoma**: el adapter de imageGen llega a clickear "Generate" pero la generación termina con error porque Envato muestra "All generations failed" en el historial.

**Hipótesis**: dos posibles causas y hay que distinguir entre ellas:

1. **Envato backend está fallando para todas las generaciones** (incluso manuales). Si esto es así, no es problema nuestro y se resuelve solo cuando Envato vuelve. Validable abriendo `app.envato.com/image-gen` manualmente y probando.

2. **Envato detecta automatización y bloquea silenciosamente** la generación pero deja la sesión válida. Validable comparando: si el mismo prompt funciona desde el browser manual y falla desde el adapter, es esto.

**Próximo paso**: validar la hipótesis con un test manual. Si es la #2, mitigaciones por orden de simplicidad:
- Agregar `playwright-stealth` para parchear fingerprints comunes.
- Aumentar delays naturales entre acciones (typing speed, pausas entre clicks).
- Correr Chromium en modo headed con `Xvfb` en lugar de headless.

## Roadmap

### Inmediato (al volver Envato o resolver detección)

1. Validar imageGen end-to-end con un job que termine `completed` y devuelva un asset descargable.
2. Confirmar que el `result.asset_url` abre la imagen real en el navegador.
3. Confirmar que el SessionKeeper guarda `storage_state` actualizado.

### Próximos generadores

Implementarlos uno por uno, copiando el patrón de `imageGen` y solo cambiando URL + selectores específicos:

- `videoGen` ── `https://app.envato.com/video-gen`
- `musicGen` ── `https://app.envato.com/music-gen`
- `voiceGen` ── `https://app.envato.com/voice-gen`
- `soundGen` ── `https://app.envato.com/sound-gen`
- `graphicsGen` ── `https://app.envato.com/graphics-gen`
- `mockupGen` ── `https://labs.envato.com/apps/mockup-gen/` (subdominio aparte, hay que mapear desde cero)

Para cada uno:

1. Crear `app/adapters/{nombre}.py` heredando de `GeneratorAdapter`.
2. Mapear selectores (probar primero los de imageGen, ajustar si difieren).
3. Registrar en `app/adapters/__init__.py`.
4. Validar con un job real.
5. Documentar selectores en `docs/SELECTORS.md`.

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
