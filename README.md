# envautomatico

API en Python que automatiza la suite de generadores de **Envato AI** (image, video, music, voice, sound, graphics, mockup) usando Playwright. Pensado para correr en un VPS Linux y consumirse desde n8n, Make, Zapier o scripts propios. El nombre es un juego de palabras entre Envato y "automático".

## Por qué existe

Envato Elements ofrece generación ilimitada con la suscripción paga, pero el único acceso público es la web. envautomatico envuelve esa web en una API REST para que se pueda consumir desde cualquier flujo de automatización sin fricción humana.

## Arquitectura

```
   cliente (n8n, curl, ...)
        │
        ▼
   FastAPI ── auth (bearer token)
        │
        ▼
   JobQueue (1 worker, FIFO)
        │
        ▼
   Adapter (image/video/music/...)
        │
        ▼
   BrowserManager  ── Chromium fresco por job con la sesión Envato
        │
        ▼
   storage/  ── archivo descargado, expuesto en /files/
```

El `BrowserManager` lanza un Chromium **fresco para cada job** (cargando `storage_state.json`) y lo cierra al terminar, guardando la sesión. Mantener un navegador/contexto de larga vida hacía que Envato rechazara en silencio las generaciones de video; un navegador fresco por job —serializado por la cola, uno a la vez— evita ese bloqueo. El `SessionKeeper` hace ping cada 6 horas para que la cookie no muera por inactividad y marca la sesión como expirada si Envato redirige a `/sign_in` (la persistencia del `storage_state` ocurre al cerrar el contexto de cada job).

## Cómo se maneja la sesión de Envato

Esto es el corazón del diseño. **El login no se hace por petición**. Se hace una sola vez, a mano, desde tu máquina local:

1. Corres `python scripts/login.py` en tu Mac. Se abre Chromium real, hacés login completo (con 2FA si lo tenés), apretás Enter.
2. Playwright graba `auth/storage_state.json` con todas las cookies y `localStorage` de Envato.
3. Subís ese archivo al VPS por SCP/rsync, o por `POST /admin/storage-state`.
4. El contenedor arranca un único Chromium con esa sesión cargada y la usa para todas las peticiones.
5. La sesión de Envato dura semanas. Cuando expira, repetís el login local y subís el JSON nuevo.

## Documentación

- **[docs/DEPLOY.md](docs/DEPLOY.md)** ── runbook paso a paso para deployar al VPS Linux con Docker.
- **[docs/LIMITES_OPERATIVOS.md](docs/LIMITES_OPERATIVOS.md)** ── límites reales de operación, capacidad y recomendaciones para automatizaciones.
- **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** ── errores reales que ya pegamos y cómo resolvimos cada uno.
- **[docs/SELECTORS.md](docs/SELECTORS.md)** ── selectores DOM mapeados por generador, con notas sobre estabilidad.
- **[docs/STATUS.md](docs/STATUS.md)** ── estado actual del proyecto y próximos pasos.

## Quickstart en local (Mac/Linux)

```bash
git clone https://github.com/642studio/envauto.git envautomatico
cd envautomatico

# venv con Python 3.9+ (3.12 si lo tenés disponible)
/usr/bin/python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"
python -m playwright install chromium

# config
cp .env.example .env
# editá .env y poné un API_TOKEN fuerte

# login una sola vez
python scripts/login.py

# servir
uvicorn app.main:app --reload
```

## Quickstart en VPS

Ver [docs/DEPLOY.md](docs/DEPLOY.md) para el runbook completo. Resumen:

```bash
# en tu Mac, ya con auth/storage_state.json generado
rsync -avz --exclude='.venv' --exclude='__pycache__' --exclude='.git' --exclude='.env' --exclude='storage/*' \
  ./ usuario@vps:/opt/envautomatico/

# en el VPS
ssh usuario@vps
cd /opt/envautomatico
cp .env.example .env && nano .env   # API_TOKEN, PUBLIC_BASE_URL, HEADLESS=true
docker compose up -d --build
```

## Endpoints

Todos los endpoints (excepto `/health` y `/files/...`) requieren `Authorization: Bearer <API_TOKEN>`.

| Método | Ruta | Descripción |
| ------ | ---- | ----------- |
| GET    | `/health`                | Estado del servicio + si la sesión de Envato es válida |
| POST   | `/generate/{generator}`  | Encola una generación. `generator` ∈ `image`, `video`, `music`, `voice`, `sound`, `graphics`, `mockup` |
| GET    | `/jobs`                  | Lista todos los jobs (en memoria) |
| GET    | `/jobs/{id}`             | Estado y resultado de un job |
| POST   | `/admin/storage-state`   | Sube un nuevo `storage_state.json` (multipart, campo `file`) |
| GET    | `/files/{path}`          | Sirve los assets generados y screenshots de debug |

### Ejemplo

```bash
TOKEN="tu-api-token"
HOST="http://192.168.1.160:8000"

# encolar
curl -X POST $HOST/generate/image \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"un gato astronauta sobre Marte","params":{"aspect_ratio":"1:1","variations":3}}'
# → {"id":"abc...","status":"queued",...}

# polling
curl -H "Authorization: Bearer $TOKEN" $HOST/jobs/abc...
# → {"status":"completed","result":{"asset_url":"http://.../files/image/xyz.png", ...}}
```

### Parámetros soportados por generador

Cada generador acepta `prompt` (string) y un objeto `params` con opciones específicas.

**`image`** (`/generate/image`):
- `aspect_ratio` ── `1:1`, `16:9`, `9:16`, `4:3`, `3:4`, `3:2`, `2:3`. Default: lo que tenga la cuenta.
- `variations` ── `1` o `3`. Default: lo que tenga la cuenta.
- `reference_images` ── lista de URLs (hasta 5). La API las descarga y las sube como referencia.

**`video`** (`/generate/video`):
- `aspect_ratio` ── `16:9` o `9:16`.
- `audio` ── booleano (`true` = con audio, `false` = sin audio).
- `first_frame` ── URL de imagen para el fotograma inicial.
- `last_frame` ── URL del fotograma final. *(Nota: el control suele estar deshabilitado en la cuenta — rollout de Envato; si lo está, se omite con warning.)*
- `reference_images` ── lista de URLs (hasta 5).

**`sound`** (`/generate/sound`):
- `duration` ── segundos (1–25). Default 5.
- `loop` ── booleano.

**`music`** (`/generate/music`):
- `energy` ── `auto`, `muted`, `low`, `medium`, `high`, `very high`.
- *(Pendientes: `genres`, `themes`, `include_lyrics`.)*

**`graphics`** (`/generate/graphics`):
- `aspect_ratio`, `variations`, `reference_images` ── igual que `image`.
- `transparent_background` ── booleano. *(Activa la opción en Envato, pero el PNG descargado sale opaco; la transparencia real solo está en el SVG, aún no automatizable.)*

`voice` y `mockup` no están implementados.

## Estado del proyecto

Funcionan end-to-end por la API **6 generadores**: `image`, `video`, `sound`, `music`, `graphics`. Cada job devuelve un asset real descargable (jpg/png/mp4/mp3) servido en `/files/`.

- `voice` ── se maneja por fuera con ElevenLabs.
- `mockup` ── pendiente (vive en `labs.envato.com`, requiere mapeo desde cero).

Mirá [docs/STATUS.md](docs/STATUS.md) para el detalle de qué funciona y qué viene.

## Estructura del repo

```
envautomatico/
├── app/
│   ├── main.py              # FastAPI entry + lifespan
│   ├── config.py            # settings desde .env
│   ├── core/
│   │   ├── browser.py       # Playwright manager (navegador fresco por job)
│   │   ├── auth.py          # helpers de sesión
│   │   ├── queue.py         # cola async de jobs
│   │   ├── session_keeper.py # tarea en background que cuida la sesión
│   │   └── storage.py       # paths y URLs públicas
│   ├── adapters/
│   │   ├── base.py          # GeneratorAdapter ABC + dump de debug
│   │   ├── image.py         # imageGen (+ referencias, hook _extra_options)
│   │   ├── video.py         # videoGen (frames, refs, audio)
│   │   ├── sound.py         # soundGen (duration, loop)
│   │   ├── music.py         # musicGen (energy)
│   │   └── graphics.py      # graphicsGen (hereda de image + transparent_background)
│   ├── routes/
│   │   ├── generate.py      # POST /generate/{tipo}
│   │   ├── jobs.py          # GET /jobs/{id}
│   │   ├── admin.py         # /health, POST /admin/storage-state
│   │   └── security.py      # bearer token middleware
│   └── models/
│       └── schemas.py       # Pydantic
├── scripts/
│   └── login.py             # CLI: abre browser headed, guarda storage_state
├── docs/                    # guías detalladas
├── storage/                 # outputs locales (gitignored)
├── auth/                    # storage_state.json (gitignored)
├── tests/
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── .gitignore
```

## Licencia

Privado, uso personal de la cuenta paga del propietario. No redistribuir el binario ni los assets generados sin respetar los términos de Envato Elements.
