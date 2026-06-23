# Imagen SOLO para el login interactivo dentro del VPS, con navegador headed visible
# por VNC. Usa la MISMA base que el Dockerfile principal para que el fingerprint del
# Chromium coincida: una sesión capturada acá valida en el contenedor de producción.
FROM mcr.microsoft.com/playwright/python:v1.59.0-jammy

WORKDIR /app

# Xvfb (display virtual), x11vnc (servidor VNC) y fluxbox (window manager liviano).
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb x11vnc fluxbox \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias del proyecto (login.py importa app.config).
COPY pyproject.toml ./
RUN pip install --no-cache-dir -U pip && pip install --no-cache-dir .

COPY scripts/vnc_login_entrypoint.sh /usr/local/bin/vnc_login_entrypoint.sh
RUN chmod +x /usr/local/bin/vnc_login_entrypoint.sh

ENV DISPLAY=:99 \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["/usr/local/bin/vnc_login_entrypoint.sh"]
