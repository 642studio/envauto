# Imagen oficial de Playwright con Python y Chromium ya instalados.
# Mantener esta versión sincronizada con la de "playwright" en pyproject.toml.
FROM mcr.microsoft.com/playwright/python:v1.59.0-jammy

WORKDIR /app

# Dependencias del sistema mínimas adicionales (la imagen ya trae casi todo).
RUN apt-get update && apt-get install -y --no-install-recommends \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Copiar metadata e instalar dependencias primero para aprovechar caché.
COPY pyproject.toml ./
RUN pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir .

# Copiar el resto del código.
COPY app ./app
COPY scripts ./scripts

# Carpetas persistentes (montar como volúmenes).
RUN mkdir -p /app/storage /app/auth

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HEADLESS=true

EXPOSE 8000

# tini como PID 1 para manejar señales correctamente con uvicorn.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
