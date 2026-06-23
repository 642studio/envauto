# Automatización — n8n on-demand por webhook

Workflow importable: [`n8n-webhook-envato.json`](n8n-webhook-envato.json).

Recibe un POST con un prompt, encola la generación en la API de envauto, hace polling
hasta que termina y **responde con el `asset_url`** (generación síncrona).

## Importar

1. En n8n: **Workflows → Import from File** → elegí `n8n-webhook-envato.json`.
2. Abrí el nodo **Config** y editá:
   - `baseUrl` ── URL de la API (ej. `http://192.168.1.160:8000`).
   - `token` ── el `API_TOKEN` del `.env` del VPS.
3. Activá el workflow (toggle **Active**). Copiá la **Production URL** del nodo Webhook.

## Reachability (importante)

n8n tiene que poder alcanzar `baseUrl`:
- **n8n self-hosted en la misma LAN/host que el VPS** → `http://192.168.1.160:8000` anda.
- **n8n cloud** → el VPS necesita exposición pública (dominio + reverse proxy con HTTPS,
  ej. Caddy) o un túnel. La IP `192.168.1.160` es privada y no se alcanza desde Internet.

## Flujo del workflow

```
Webhook (POST) → Config → POST /generate/{generator} → guardar jobId
   → [ Esperar 8s → GET /jobs/{id} → ¿status terminó? ]  (loop hasta completed|failed)
   → Responder { status, asset_url, error, job }
```

El loop vuelve a "Esperar" mientras el `status` sea `queued` o `running`.

## Llamarlo

```bash
curl -X POST "https://TU-N8N/webhook/envato" \
  -H "Content-Type: application/json" \
  -d '{
    "generator": "image",
    "prompt": "a minimalist logo of a blue fox, flat vector",
    "params": { "aspect_ratio": "1:1", "variations": 1 }
  }'
# → {"status":"completed","asset_url":"http://192.168.1.160:8000/files/image/....jpg", ...}
```

`generator` puede ser `image`, `video`, `sound`, `music`, `graphics`. `params` es
opcional y específico de cada generador (ver el README principal).

## Notas

- **Síncrono**: el webhook queda esperando hasta que la generación termina. image/sound/
  music/graphics tardan ~15–60s; **video ~2 min** — el cliente que llama tiene que tolerar
  esa espera. Para asincronía, separar en dos webhooks (uno encola y devuelve `jobId`, otro
  consulta) o sumar un callback.
- La API es **serial** (un job a la vez). Si llegan varios requests en paralelo, se encolan.
- Si `/health` da `authenticated:false`, la sesión de Envato murió: regenerá el
  `storage_state.json` y subilo (ver `docs/DEPLOY.md`).
