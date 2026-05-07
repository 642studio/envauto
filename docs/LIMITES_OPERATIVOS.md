# Límites Operativos de EnvautoAPI

Fecha de validación: 2026-05-07  
Contexto: despliegue actual en VPS con contenedor `envautomatico`.

## Resumen

La API no implementa rate limit HTTP explícito (no hay control de requests por minuto ni respuestas `429`), pero **sí tiene límites técnicos fuertes**:

- Procesamiento serial: **1 job a la vez**.
- Cola en memoria sin `maxsize`.
- Timeout máximo por job: **10 minutos**.
- Estado de jobs no persistente (se pierde al reiniciar contenedor/proceso).

## Límites confirmados

## 1) Concurrencia real = 1

La cola usa un único worker FIFO (`app/core/queue.py`) y el browser está protegido por lock (`app/core/browser.py`).  
Aunque entren múltiples `POST /generate/*`, se ejecutan secuencialmente.

Implicación: el throughput depende 100% del tiempo promedio por generación.

## 2) Sin throttling HTTP interno

`POST /generate/{generator}` encola jobs tras validar token, sin ventana de rate-limit (`app/routes/generate.py`).

Implicación: si llegan ráfagas, crece la cola en vez de rechazar tráfico.

## 3) Cola en memoria sin tope

`asyncio.Queue()` se crea sin tamaño máximo y los jobs se guardan en un diccionario en RAM (`app/core/queue.py`).

Implicación: bajo backlog alto puede subir uso de memoria y latencia de espera.

## 4) Timeout de generación = 10 min

Configurado en `generation_timeout_ms = 600_000` (`app/config.py`) y aplicado por adapters.

Implicación: jobs lentos o colgados terminarán en `failed` por timeout.

## 5) Prompt máximo 4000 caracteres

Validación en schema (`app/models/schemas.py`).

## 6) Persistencia parcial

- **Sí persiste**: archivos generados (`storage/`).
- **No persiste**: estado de cola/jobs (vive en memoria).

Implicación: reinicio = pérdida del historial en `/jobs`.

## 7) Dependencia de sesión Envato

Si expira la sesión, `/health` reporta `authenticated=false` y los jobs fallarán hasta renovar `storage_state.json`.

## Recomendaciones para automatizaciones (n8n)

1. Tratar EnvautoAPI como worker serial (concurrencia global = 1).
2. Polling:
   - `image` / `music`: 10 s
   - `video` / `graphics`: 20 s
3. Definir límite de cola de entrada (ej. 20 pendientes) antes de pausar intake.
4. Agregar alertas:
   - `authenticated=false`
   - exceso de `failed`
   - cola envejecida
5. Usar reintentos con backoff, no retries infinitos.

## Nota de capacidad

Capacidad aproximada por hora:

`jobs/hora = 3600 / segundos_promedio_por_job`

Ejemplos:
- 60 s/job -> ~60 jobs/h
- 120 s/job -> ~30 jobs/h
- 240 s/job -> ~15 jobs/h

