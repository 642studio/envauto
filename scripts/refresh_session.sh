#!/usr/bin/env bash
# Refresca la sesión de Envato en el VPS después de hacer el login en local.
#
# Por qué hace falta el paso de "re-bless": una sesión capturada en el Chromium de
# macOS (scripts/login.py) es rechazada por el Chromium Linux del VPS (Envato la ata
# al fingerprint del navegador). El contenedor de login la carga en un Chromium Linux,
# navega a la app y la re-guarda → queda válida para el servicio headless de producción.
#
# Uso (desde la raíz del repo, en tu Mac):
#   1) python scripts/login.py        # login nuevo (Google SSO + 2FA) en tu Mac
#   2) export SSHPASS='...'            # opcional: si usás password (si no, key/prompt)
#      ./scripts/refresh_session.sh
#
# Variables opcionales (con defaults):
#   VPS_USER (coreprorex)  VPS_HOST (192.168.1.160)  VPS_PATH (/opt/envautomatico)
#   API_PORT (8000)
set -euo pipefail

VPS_USER="${VPS_USER:-coreprorex}"
VPS_HOST="${VPS_HOST:-192.168.1.160}"
VPS_PATH="${VPS_PATH:-/opt/envautomatico}"
API_PORT="${API_PORT:-8000}"
LOCAL_STATE="auth/storage_state.json"
HEALTH_URL="http://${VPS_HOST}:${API_PORT}/health"

# Wrappers ssh/scp: usan sshpass si SSHPASS está seteada; si no, ssh/scp normal
# (sirve con llave configurada o pedirá la contraseña de forma interactiva).
if [[ -n "${SSHPASS:-}" ]] && command -v sshpass >/dev/null 2>&1; then
  SSH() { sshpass -e ssh -o StrictHostKeyChecking=accept-new "$@"; }
  SCP() { sshpass -e scp -o StrictHostKeyChecking=accept-new "$@"; }
else
  SSH() { ssh -o StrictHostKeyChecking=accept-new "$@"; }
  SCP() { scp -o StrictHostKeyChecking=accept-new "$@"; }
fi

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

# 0) Validaciones locales
[[ -f "$LOCAL_STATE" ]] || { echo "ERROR: no existe $LOCAL_STATE. Corré primero: python scripts/login.py"; exit 1; }
if command -v stat >/dev/null 2>&1; then
  age_min=$(( ( $(date +%s) - $(stat -f %m "$LOCAL_STATE" 2>/dev/null || stat -c %Y "$LOCAL_STATE") ) / 60 ))
  if (( age_min > 30 )); then
    read -r -p "$LOCAL_STATE tiene ${age_min} min. ¿Seguro que corriste login.py recién? [y/N] " ans
    [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "Abortado. Corré python scripts/login.py."; exit 1; }
  fi
fi

# 1) Subir la sesión nueva
say "Subiendo $LOCAL_STATE a ${VPS_USER}@${VPS_HOST}:${VPS_PATH}/auth/"
SCP "$LOCAL_STATE" "${VPS_USER}@${VPS_HOST}:${VPS_PATH}/auth/storage_state.json"

# 2) Re-bless: correr el contenedor de login (carga + re-guarda desde Chromium Linux)
say "Re-bendiciendo la sesión en el Chromium Linux del VPS..."
SSH "${VPS_USER}@${VPS_HOST}" "cd ${VPS_PATH} && docker compose -f docker-compose.login.yml up -d login >/dev/null 2>&1 && echo arrancado"

# 3) Esperar a que el contenedor de login termine (guarda solo y sale). Hasta ~100s.
say "Esperando a que el re-bless complete (hasta 100s)..."
rebless_ok=false
for i in $(seq 1 20); do
  sleep 5
  running=$(SSH "${VPS_USER}@${VPS_HOST}" "docker ps --filter name=envautomatico-login --format '{{.Names}}'" 2>/dev/null || true)
  if [[ -z "$running" ]]; then
    # salió: revisar si guardó OK
    if SSH "${VPS_USER}@${VPS_HOST}" "docker logs envautomatico-login 2>&1 | grep -q 'sesión Linux guardada'"; then
      rebless_ok=true
    fi
    break
  fi
done

if [[ "$rebless_ok" != true ]]; then
  cat <<MSG

[!] El re-bless automático no detectó la sesión logueada.
    Probablemente haya que hacer un LOGIN FRESCO por VNC (el contenedor de login
    sigue corriendo esperándote). Desde tu Mac:
       ssh -L 5900:localhost:5900 ${VPS_USER}@${VPS_HOST}
       (y abrí en el VNC)  vnc://localhost:5900   → hacé el login + 2FA
    Cuando termines, el contenedor guarda solo. Después corré:
       ssh ${VPS_USER}@${VPS_HOST} "cd ${VPS_PATH} && docker compose restart"
MSG
  exit 2
fi

# 4) Limpiar el contenedor de login y reiniciar el servicio principal
say "Re-bless OK. Limpiando y reiniciando el servicio..."
SSH "${VPS_USER}@${VPS_HOST}" "cd ${VPS_PATH} && docker compose -f docker-compose.login.yml rm -f login >/dev/null 2>&1; docker compose restart >/dev/null 2>&1 && echo reiniciado"
sleep 8

# 5) Verificar health
say "Verificando /health ..."
health=$(curl -s -m 10 "$HEALTH_URL" || true)
echo "$health"
if echo "$health" | grep -q '"authenticated":true'; then
  echo -e "\n\033[1;32m✓ Sesión refrescada y activa. Listo.\033[0m"
else
  echo -e "\n\033[1;33m[!] authenticated no es true todavía. Revisá: docker compose logs en el VPS.\033[0m"
  exit 3
fi
