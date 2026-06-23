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

echo "[vnc] arrancando x11vnc en 127.0.0.1:5900 (sin password, solo localhost) ..."
x11vnc -display :99 -localhost -nopw -forever -shared -rfbport 5900 >/tmp/x11vnc.log 2>&1 &
sleep 1

cat <<'MSG'
================================================================
  VNC listo. Desde tu Mac:
    1) Túnel SSH:   ssh -L 5900:localhost:5900 coreprorex@192.168.1.160
    2) Cliente VNC: conectá a  localhost:5900   (sin contraseña)
       (en macOS: Finder → Ir → Conectarse al servidor → vnc://localhost:5900)
  Vas a ver un Chromium con la página de login de Envato.
  Hacé el login completo (incluyendo 2FA) y volvé acá a presionar ENTER.
================================================================
MSG

export DISPLAY=:99
exec python scripts/login.py
