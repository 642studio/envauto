#!/bin/bash
# Arranca un display virtual (Xvfb), lo expone por VNC (x11vnc, solo localhost) y un
# window manager liviano, y después corre el login interactivo headed.
#
# El usuario:
#   1. abre un túnel SSH al 5900 del VPS y conecta un cliente VNC,
#   2. hace el login completo (Google SSO + 2FA) en el Chromium que ve,
#   3. vuelve a esta terminal y presiona ENTER.
# login.py verifica que la app cargue logueada y guarda auth/storage_state.json.
set -e

echo "[vnc] arrancando Xvfb en :99 ..."
Xvfb :99 -screen 0 1440x900x24 -ac >/tmp/xvfb.log 2>&1 &
sleep 1

echo "[vnc] arrancando window manager ..."
fluxbox >/tmp/fluxbox.log 2>&1 &
sleep 1

echo "[vnc] arrancando x11vnc en 0.0.0.0:5900 del container ..."
# NO usar -localhost: ataría x11vnc al loopback DEL CONTAINER y el port-mapping de
# Docker (host 127.0.0.1:5900 -> container 5900) no lo alcanzaría. La seguridad la da
# el compose, que publica solo en 127.0.0.1 del host, y el acceso es por túnel SSH.
x11vnc -display :99 -nopw -forever -shared -rfbport 5900 >/tmp/x11vnc.log 2>&1 &
sleep 1

echo "[vnc] listo. Conectá por VNC (vía túnel SSH al 5900) y hacé el login."

export DISPLAY=:99
# Auto-detección: no requiere ENTER, guarda solo cuando detecta el login.
exec python scripts/login_vnc_auto.py
