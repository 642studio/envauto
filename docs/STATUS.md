# Estado del proyecto

Última actualización: 2026-05-05

## Funciona

- **Scaffolding completo** del proyecto: FastAPI, BrowserManager con contexto persistente, JobQueue, SessionKeeper, base adapter, login interactivo CLI, Dockerfile, docker-compose.
- **Deploy a VPS Linux** con Docker. Probado en `100.99.244.54` con Docker compose.
- **Sesión persistente de Envato**. El login se hace una vez en local con `scripts/login.py`, se sube el `storage_state.json` al VPS, y el contenedor levanta un Chromium que reutiliza la sesión para todas las peticiones.
- **Hot-reload de sesión**: `POST /admin/storage-state` recrea el contexto del browser en caliente sin reiniciar el contenedor. `POST /admin/reload-session` recarga solo el contexto cuando el archivo ya está en disco.
- **Validación de sesión al inicio**: el SessionKeeper hace un ping a Envato en el primer ciclo del loop para detectar sesión expirada antes de aceptar jobs.
- **API REST**: `/health`, `POST /generate/{tipo}`, `GET /jobs/{id}`, `POST /admin/storage-state`, `POST /admin/reload-session`, `/files/...`. Todo con bearer token salvo `/health` y `/files/...`.
- **Job queue serial** con un solo worker. Encola, ejecuta y reporta status pasando por `queued -> running -> completed | failed`.
- **Debug automático en fallos**: cada job que falla guarda screenshot + HTML + URL en `storage/debug/`, accesibles desde el navegador en `/files/debug/`.
- **Selectores de imageGen mapeados** contra UI real:
  - Prompt input: `[data-cy="prompt-input"]` (contenteditable div).
  - Submit button: `button[type="submit"][data-analytics-name="gen_click"]:visible`.
  - URL pattern del job: `/image-gen/genai-image/{uuid}`.
  - Imágenes resultado: `img[alt="Generated Image"]` con src en `gen-assets-resized.envatousercontent.com`.

## Estado actual (2026-05-05)

**Síntoma**: `POST /generate/image` termina con `failed` y error `"La sesión de Envato no es válida"`. El adapter detecta que la página redirigió a `sign_in` al navegar a `app.envato.com/image-gen`.

**Causa**: las cookies en `auth/storage_state.json` del VPS expiraron. El contenedor las cargó al arrancar y las sigue usando aunque ya no sean válidas.

**Solución inmediata** (refresh de sesión):
```bash
# En tu Mac local:
python scripts/login.py
# → guarda auth/storage_state.json con cookies frescas

# Subir sin SCP (hot-reload, no requiere reiniciar):
curl -X POST "http://100.99.244.54:8000/admin/storage-state" \
  -H "Authorization: Bearer $ENVAUTO_TOKEN" \
  -F "file=@auth/storage_state.json"

# Verificar:
curl "http://100.99.244.54:8000/health"
# → debe mostrar "authenticated": true
```

**Hipótesis adicional (IndexedDB)**: si las cookies son válidas pero Envato redirige igual, el problema puede ser que la SPA guarda tokens de auth en IndexedDB, que Playwright no captura en `storage_state`. En ese caso, explorar inyección de IndexedDB vía `page.evaluate()` después del `new_context`.

## Roadmap

### Inmediato

1. Refrescar sesión en el VPS con el flujo de hot-reload (ver arriba).
2. Validar imageGen end-to-end con un job que termine `completed` y devuelva un asset descargable.
3. Confirmar que el `result.asset_url` abre la imagen real en el navegador.
4. Si sigue fallando con cookies frescas → investigar IndexedDB como causa raíz.

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
