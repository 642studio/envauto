# Troubleshooting

Errores reales que pegamos durante el desarrollo y deploy de envautomatico, ordenados por dónde aparecen, con la causa y la solución.

## Setup local

### `pip install -e .` falla con "File 'setup.py' or 'setup.cfg' not found"

```
ERROR: File "setup.py" or "setup.cfg" not found. Directory cannot be installed in editable mode
```

**Causa**: tu pip es muy viejo (21.2.4 o anterior) y no soporta instalación editable desde `pyproject.toml` puro (PEP 660 requiere pip 21.3+).

**Solución**: actualizá pip antes de instalar.

```bash
pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"
```

### `Package envautomatico requires a different Python: 3.9.6 not in '>=3.11'`

**Causa**: el `pyproject.toml` original pedía Python 3.11+ y tu venv corre con Python 3.9 (el que viene con macOS).

**Solución**: ya bajamos el requisito a 3.9 en commits siguientes (el código usa `from __future__ import annotations` para que la sintaxis moderna sea compatible). Si aún ves este error, asegurate de tener la última versión del `pyproject.toml`.

### Brew Python 3.12 falla con `pyexpat` symbol not found

```
ImportError: dlopen(...pyexpat.cpython-312-darwin.so): Symbol not found:
_XML_SetAllocTrackerActivationThreshold
Expected in: /usr/lib/libexpat.1.dylib
```

**Causa**: la instalación de Python 3.12 de Homebrew quedó linkeada contra una versión de `libexpat` más nueva que la que tu macOS tiene en `/usr/lib/`.

**Solución rápida**: usar el Python que viene con macOS en lugar del de brew.

```bash
rm -rf .venv
/usr/bin/python3 -m venv .venv
source .venv/bin/activate
```

**Solución definitiva**: reinstalar Python de brew para que se rebuilde contra librerías actuales.

```bash
brew update
brew reinstall python@3.12
```

### Crear venv falla con `ensurepip` returned non-zero exit status 1

**Causa**: bug puntual de la instalación de Python que hace que `ensurepip` no pueda armar pip dentro del venv.

**Solución**: crear el venv sin pip y bajarlo aparte.

```bash
rm -rf .venv
/opt/homebrew/bin/python3.12 -m venv .venv --without-pip
source .venv/bin/activate
curl -sS https://bootstrap.pypa.io/get-pip.py | python
pip install -e ".[dev]"
```

### Comentarios en zsh dan "command not found: #"

**Causa**: tu shell tiene `interactive_comments` desactivado por default.

**Solución**: activarlo permanentemente.

```bash
echo 'setopt interactive_comments' >> ~/.zshrc
source ~/.zshrc
```

O simplemente pegá los comandos sin las líneas de comentario.

## Deploy al VPS

### `rsync` falla con "Permission denied" creando `/opt/envautomatico`

```
rsync: [Receiver] mkdir "/opt/envautomatico" failed: Permission denied (13)
```

**Causa**: tu usuario SSH no puede escribir en `/opt/`. Hay que crear el directorio con sudo y darle ownership a tu usuario antes del rsync.

**Solución**:

```bash
ssh coreprorex@192.168.1.160
sudo mkdir -p /opt/envautomatico
sudo chown coreprorex:coreprorex /opt/envautomatico
exit
```

Después reintentás el rsync.

### `docker compose` da "permission denied" en `unix:///var/run/docker.sock`

**Causa**: tu usuario no está en el grupo `docker`, así que no puede hablar con el daemon.

**Solución permanente**:

```bash
sudo usermod -aG docker coreprorex
newgrp docker
```

`newgrp` aplica el grupo en la sesión actual sin tener que reconectar SSH.

**Solución temporal**:

```bash
sudo docker compose up -d --build
```

## Runtime / Playwright

### `BrowserType.launch: Executable doesn't exist at /ms-playwright/...`

```
Looks like Playwright was just updated to 1.59.0.
Please update docker image as well.
- current: mcr.microsoft.com/playwright/python:v1.48.0-jammy
- required: mcr.microsoft.com/playwright/python:v1.59.0-jammy
```

**Causa**: la versión de la librería `playwright` (Python) y la versión del binario del navegador en la imagen Docker no coinciden. Cada release de Playwright trae un Chromium específico.

**Solución**: pinear ambos a la misma versión.

En `pyproject.toml`:

```toml
"playwright==1.59.0",
```

En `Dockerfile`:

```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.59.0-jammy
```

Después rebuild: `docker compose up -d --build`.

### `Locator.click: strict mode violation: locator resolved to 2 elements`

**Causa**: el selector matchea más de un elemento en la página. Playwright en strict mode (default) no clickea ambiguamente. Suele pasar cuando Envato renderiza versión desktop y mobile en paralelo y CSS oculta una.

**Solución**: filtrar por visibilidad y tomar el primero.

```python
SUBMIT_BUTTON = 'button[type="submit"][data-analytics-name="gen_click"]:visible'

# en el código:
await page.locator(self.SUBMIT_BUTTON).first.click()
```

El `:visible` descarta los elementos ocultos por CSS, y `.first` es la red de seguridad si aún quedan dos visibles.

### `Locator.click` falla con `CybotCookiebotDialog ... intercepts pointer events`

**Causa**: Cookiebot abre un modal de consentimiento que queda por encima de la UI y bloquea el click en `Generate`.

**Solución**: cerrar Cookiebot antes de enviar el prompt y justo antes del submit.

Selectores estables usados por el helper:

```python
"#CybotCookiebotDialog"
"#CybotCookiebotDialog.CybotCookiebotDialogActive"
"#CybotCookiebotDialogBodyButtonDecline"  # Reject all
```

Si no cierra por ID, usar fallback por texto visible (`Reject all`).

### Job se queda en `queued` y nunca arranca

**Síntoma**: encolás un job, esperás minutos, sigue en `queued` con `started_at: null`.

**Causa A**: el contenedor está corriendo código viejo y un job anterior trabó la cola con un timeout. El lock no se liberó y los nuevos jobs esperan eternamente.

**Solución**: `docker compose down && docker compose up -d --build` para reiniciar limpio.

**Causa B**: el worker no se inició. Mirá los logs:

```bash
docker compose logs envautomatico | grep -i "worker"
```

Tenés que ver `Job worker iniciado`. Si no aparece, hay un error en el lifespan de FastAPI.

### `/health` devuelve `authenticated: false`

**Causa**: el `storage_state.json` no existe en el contenedor, está corrupto, o Envato invalidó la sesión.

**Solución**:

1. Verificá que el archivo existe y pesa lo razonable:

```bash
ssh coreprorex@vps "ls -la /opt/envautomatico/auth/storage_state.json"
```

2. Si existe pero el flag dice `false`, regenerá la sesión desde tu Mac:

```bash
python scripts/login.py
scp auth/storage_state.json coreprorex@vps:/opt/envautomatico/auth/
ssh coreprorex@vps "cd /opt/envautomatico && docker compose restart"
```

### El job termina con `TimeoutError: Locator.wait_for: Timeout 60000ms`

**Causa**: el adapter no encontró un selector esperado dentro del timeout. Las causas habituales son:

1. La sesión está rota y la página redirige a `/sign_in`.
2. Envato cambió el DOM y el selector ya no aplica.
3. Headless Chromium triggerea protección anti-bot y la página devuelve un layout distinto.
4. La página todavía está renderizando React cuando expira el timeout.

**Solución**: nuestro adapter ya guarda screenshot + HTML cuando falla. Buscalos en `storage/debug/` y mirá visualmente qué cargó.

```bash
ssh coreprorex@vps "ls -la /opt/envautomatico/storage/debug/"
```

Y abrí en tu navegador local:

```
http://192.168.1.160:8000/files/debug/image-<timestamp>.png
```

El archivo `.url.txt` te dice dónde quedó el browser al fallar. El `.html` te deja inspeccionar el DOM exacto.

### El job termina con `Timeout 300000ms` esperando `/image-gen/genai-image/...`

**Causa**: la UI de Envato puede quedarse en `/image-gen` aunque la generación se haya procesado o fallado en el panel lateral. Esperar solo por cambio de URL termina en timeout falso.

**Solución**: estrategia dual en `image` adapter:

1. Intento corto de `wait_for_url` al patrón `/image-gen/genai-image/{uuid}`.
2. Fallback a leer `[data-cy="details-panel"]` y resolver por estado visible:
   - Éxito: `img[alt="Generated Image"]` con `src` de `gen-assets*.envatousercontent.com`.
   - Error explícito: `All generations failed` o `Try again` (fail fast, sin esperar 300s).

Con esto el job deja de quedar colgado cuando Envato no navega.

## API

### `POST /generate/image` devuelve `{"detail":"Not authenticated"}`

**Causa**: el header `Authorization` no llegó, o el token no coincide con `API_TOKEN` del `.env` del VPS.

**Solución**:

1. Verificá que la variable está seteada en tu shell:

```bash
echo "$ENVAUTO_TOKEN"
```

Si imprime vacío, reseteala:

```bash
TOKEN=$(ssh coreprorex@vps "grep API_TOKEN /opt/envautomatico/.env | cut -d= -f2")
echo "$TOKEN"
```

2. Asegurate de no haber agregado `<` y `>` al copiar (esos son placeholders en la doc, no van literales).

3. Si el token pegado tiene espacios o saltos de línea, `curl` los reenvía y rompe el bearer. Volvé a copiarlo limpio.

### Variables de shell se borran entre terminales

**Causa**: `TOKEN=...` solo dura mientras la terminal está abierta. Cada `source ~/.zshrc` o nueva terminal arranca con un entorno limpio.

**Solución**: agregá la variable al `.zshrc` con `export`:

```bash
echo 'export ENVAUTO_TOKEN="abc..."' >> ~/.zshrc
source ~/.zshrc
```

Ahora en cualquier terminal usás `$ENVAUTO_TOKEN` y siempre está disponible.

### `curl` se rompe cuando pegás con `\` y líneas en blanco

**Causa**: `\` continúa la línea solo si la siguiente línea viene inmediatamente. Si hay enter en blanco entre medio, zsh corta la cadena y interpreta cada línea como comando separado.

**Solución**: pegá el comando en una sola línea, sin `\`:

```bash
curl -X POST http://192.168.1.160:8000/generate/image -H "Authorization: Bearer $ENVAUTO_TOKEN" -H "Content-Type: application/json" -d '{"prompt":"test"}'
```

O pegá el bloque sin líneas en blanco entre las continuaciones.

## Anti-bot / Envato

### El job genera screenshot en `/login` o pantalla de "verify human"

**Causa**: Envato detectó automatización. Pasa más en headless que en headed.

**Mitigaciones que ya están en el código**:

- User-Agent consistente con el OS del contenedor (Linux x86_64, no Mac).
- `--disable-blink-features=AutomationControlled` al lanzar Chromium.
- Locale `en-US` y timezone `America/New_York` explícitos.
- Sesión real cargada via `storage_state` (cookies legítimas).

**Si aún así te detectan**, las opciones por orden de simplicidad:

1. Agregar `playwright-stealth` que parchea fingerprints comunes.
2. Correr en modo headed con `Xvfb` (display virtual) en lugar de headless.
3. Agregar delays aleatorios entre acciones para parecer más humano.

## Debugging que nos sirvió

### Ver exactamente qué carga el navegador headless

El base adapter (`app/adapters/base.py`) está envuelto en `try/except` que guarda screenshot + HTML + URL al fallar. Cada job fallido deja tres archivos en `storage/debug/`:

```
image-<timestamp>.png       # screenshot full page
image-<timestamp>.html      # DOM completo  
image-<timestamp>.url.txt   # URL final (post redirects)
```

Accesibles desde el navegador en `http://VPS:8000/files/debug/<archivo>`.

### Ver actividad de red en vivo

```bash
ssh coreprorex@vps "cd /opt/envautomatico && docker compose logs -f --tail=50"
```

Cada paso del adapter loguea (loguru con nivel INFO por default).

### Probar el adapter sin la API

Útil para iterar selectores rápido:

```python
import asyncio
from playwright.async_api import async_playwright
from app.adapters.image import ImageGenAdapter

async def test():
    adapter = ImageGenAdapter()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context(storage_state="auth/storage_state.json")
        page = await ctx.new_page()
        result = await adapter.run(page, {"prompt": "test"})
        print(result)
        await browser.close()

asyncio.run(test())
```

Headless en `False` te deja ver el browser y debuguear visualmente.
