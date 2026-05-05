# Deploy de envautomatico a VPS Linux

Runbook end-to-end para llevar el proyecto desde tu Mac a un VPS Ubuntu corriendo en Docker. Probado contra un VPS LAN en `192.168.1.160` con un usuario sin privilegios sudo automáticos.

## Prerrequisitos

**En tu Mac**:
- Python 3.9 o superior (el que viene con macOS sirve)
- Acceso SSH al VPS por contraseña o llave
- `rsync` (viene con macOS)

**En el VPS**:
- Ubuntu 22.04 o similar
- Acceso SSH como usuario con sudo
- Puerto 8000 abierto, o un reverse proxy delante (Caddy/nginx/Traefik)

## Paso 1: setup local y login interactivo

Solo se hace una vez. El objetivo es generar `auth/storage_state.json` con tu sesión real de Envato.

```bash
git clone https://github.com/642studio/envauto.git envautomatico
cd envautomatico

# venv con el Python que tengas (3.9+ funciona)
/usr/bin/python3 -m venv .venv
source .venv/bin/activate

# upgrade pip y dependencias
pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"
python -m playwright install chromium

# login interactivo
python scripts/login.py
```

`scripts/login.py` abre Chromium real (no headless), te lleva a la página de Envato, hacés login completo (incluyendo 2FA si lo tenés), y cuando estás dentro presionás Enter en la terminal. El script guarda `auth/storage_state.json`.

Verificá que existe:

```bash
ls -la auth/storage_state.json
```

Tiene que pesar varios KB (típicamente 15-50 KB).

## Paso 2: preparar el VPS

Conectate al VPS y creá el directorio del proyecto con permisos para tu usuario.

```bash
ssh coreprorex@192.168.1.160
sudo mkdir -p /opt/envautomatico
sudo chown coreprorex:coreprorex /opt/envautomatico
exit
```

`/opt/` requiere sudo para crear, pero si después le das `chown` a tu usuario, ya podés escribir sin más sudo.

## Paso 3: instalar Docker en el VPS

Si todavía no tenés Docker instalado:

```bash
ssh coreprorex@192.168.1.160
docker --version || curl -fsSL https://get.docker.com | sudo sh

# agregarte al grupo docker para no necesitar sudo cada vez
sudo usermod -aG docker coreprorex
newgrp docker  # aplica el grupo en la sesión actual
```

`newgrp docker` te ahorra cerrar y volver a abrir SSH. Si no funciona, hacé `exit` y volvé a entrar.

## Paso 4: subir el proyecto al VPS

Desde tu Mac, parado en el directorio del proyecto:

```bash
rsync -avz --progress \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='.git' \
  --exclude='.env' \
  --exclude='storage/*' \
  ./ coreprorex@192.168.1.160:/opt/envautomatico/
```

Subimos código y `auth/storage_state.json`. Excluimos:

- `.venv/` ── el virtualenv local, no aplica al contenedor.
- `.git/` ── opcional, según si querés versionar en el VPS.
- `.env` ── la config del VPS es distinta a la local.
- `storage/*` ── outputs locales, no hace falta llevarlos.

## Paso 5: configurar el .env del VPS

```bash
ssh coreprorex@192.168.1.160
cd /opt/envautomatico

# generar API token fuerte
openssl rand -hex 32  # copiá el resultado

cp .env.example .env
nano .env
```

Editá tres campos en el `.env`:

```
API_TOKEN=<el-token-que-generaste>
PUBLIC_BASE_URL=http://192.168.1.160:8000
HEADLESS=true
```

`PUBLIC_BASE_URL` es la URL externa desde la que los clientes van a acceder a los assets. Si tenés dominio, ponelo (`https://envautomatico.tudominio.com`). Si no, IP:puerto está bien.

Guardá con `Ctrl+O`, `Enter`, `Ctrl+X`.

## Paso 6: levantar el contenedor

```bash
docker compose up -d --build
docker compose logs -f
```

El primer build tarda 5-10 minutos porque baja la imagen base de Playwright (~1.5 GB con Chromium incluido). Las builds siguientes son segundos porque queda cacheada.

Esperá a ver `Application startup complete` en los logs y salí con `Ctrl+C`. El contenedor sigue corriendo en background.

## Paso 7: validar

Desde tu Mac:

```bash
# guardás el token persistente para no recordarlo
echo 'export ENVAUTO_TOKEN="el-token-del-env"' >> ~/.zshrc
source ~/.zshrc

# health
curl http://192.168.1.160:8000/health
# {"status":"ok","authenticated":true,"generators":["image"]}

# encolar test
curl -X POST http://192.168.1.160:8000/generate/image \
  -H "Authorization: Bearer $ENVAUTO_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"un gato astronauta sobre Marte"}'

# polling
JOB_ID="<id-devuelto>"
curl -H "Authorization: Bearer $ENVAUTO_TOKEN" http://192.168.1.160:8000/jobs/$JOB_ID
```

Si llega a `status: completed`, el `result.asset_url` apunta al archivo descargado.

## Operaciones recurrentes

### Actualizar código

Después de cualquier cambio en el código local:

```bash
# desde tu Mac
rsync -avz --progress --exclude='.venv' --exclude='__pycache__' --exclude='.git' --exclude='.env' --exclude='storage/*' ./ coreprorex@192.168.1.160:/opt/envautomatico/

# en el VPS
ssh coreprorex@192.168.1.160 "cd /opt/envautomatico && docker compose down && docker compose up -d --build"
```

El `--build` reconstruye la imagen con el código nuevo. Es rápido salvo que cambies dependencias en `pyproject.toml`.

### Refrescar la sesión de Envato

Cuando `/health` te devuelve `authenticated: false`, la sesión murió. Regeneración:

```bash
# en tu Mac
source .venv/bin/activate
python scripts/login.py

# subir el JSON nuevo (dos opciones)

# Opción A: por SCP
scp auth/storage_state.json coreprorex@192.168.1.160:/opt/envautomatico/auth/

# Opción B: por API
curl -X POST http://192.168.1.160:8000/admin/storage-state \
  -H "Authorization: Bearer $ENVAUTO_TOKEN" \
  -F "file=@auth/storage_state.json"

# reiniciar el contenedor para que tome la sesión nueva
ssh coreprorex@192.168.1.160 "cd /opt/envautomatico && docker compose restart"
```

### Ver logs

```bash
ssh coreprorex@192.168.1.160 "cd /opt/envautomatico && docker compose logs -f --tail=100"
```

### Apagar / encender

```bash
ssh coreprorex@192.168.1.160 "cd /opt/envautomatico && docker compose down"
ssh coreprorex@192.168.1.160 "cd /opt/envautomatico && docker compose up -d"
```

## Notas de seguridad

- El `API_TOKEN` es la única protección de los endpoints. Generalo con `openssl rand -hex 32` y nunca lo subas al repo.
- `auth/storage_state.json` contiene tus cookies de Envato logueado. Tratalo como una contraseña: nunca lo commitees, no lo compartas.
- Si exponés el VPS a Internet, poné un reverse proxy con HTTPS delante (Caddy es el camino más simple, hace TLS automático con Let's Encrypt).
- El puerto 8000 puede quedar cerrado al exterior si solo consumís desde tu LAN, n8n local, o un servidor que vive junto al VPS.
