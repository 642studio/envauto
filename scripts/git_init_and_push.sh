#!/usr/bin/env bash
# Inicializa el repo, hace el commit inicial y sube todo a GitHub.
# Correr UNA SOLA VEZ desde la raíz del proyecto.
#
# Uso:
#   chmod +x scripts/git_init_and_push.sh
#   ./scripts/git_init_and_push.sh
#
# Antes de correr, asegurate de:
#   - Estar en /Users/642studio/Documents/Claude/Projects/envautomatico
#   - Tener acceso al repo https://github.com/642studio/envauto.git
#     (que sea de tu cuenta o que tengas push permission)
#
# El script verifica que ningún secreto se cuele al commit.
set -euo pipefail

REMOTE_URL="https://github.com/642studio/envauto.git"
REMOTE_NAME="origin"
BRANCH="main"

echo "==> Limpieza previa por si hay un .git roto"
if [ -d .git ]; then
  echo "    Encontré un .git previo. Lo borro para arrancar limpio."
  rm -rf .git
fi

echo "==> git init en branch '$BRANCH'"
git init -b "$BRANCH"

echo "==> Configuración de identidad"
git config user.name "Clarissa"
git config user.email "cigm99@gmail.com"

echo "==> Verificación de seguridad: que .gitignore tape los secretos"
LEAKS=$(git ls-files --others --exclude-standard --cached | grep -E "(\.env$|storage_state\.json|/storage/[^.])" || true)
if [ -n "$LEAKS" ]; then
  echo "    ERROR: estos archivos parecen secretos y van a entrar al repo:"
  echo "$LEAKS"
  echo "    Revisá .gitignore antes de continuar. Aborto."
  exit 1
fi
echo "    OK, ningún secreto a la vista"

echo "==> Staging de todos los archivos respetando .gitignore"
git add -A

echo "==> Verificación final antes del commit"
git ls-files | grep -E "(\.env$|storage_state\.json)" && {
  echo "    ABORTO: detecté un secreto staged. Revisá .gitignore."
  exit 1
} || echo "    OK"

echo "==> Commit inicial"
git commit -m "Initial commit: envautomatico v0.1.0

API en Python que automatiza la suite de generadores de Envato AI
(image, video, music, voice, sound, graphics, mockup) usando Playwright.

Esta versión incluye:
- Scaffolding completo (FastAPI + Playwright + cola async + SessionKeeper)
- Adapter de referencia: imageGen con selectores reales mapeados
- Login interactivo CLI (scripts/login.py)
- Deploy via Docker (Dockerfile + docker-compose.yml)
- Documentación: README, DEPLOY.md, TROUBLESHOOTING.md, SELECTORS.md, STATUS.md"

echo "==> Configurar remote $REMOTE_NAME -> $REMOTE_URL"
git remote add "$REMOTE_NAME" "$REMOTE_URL"

echo "==> Push (te va a pedir credenciales de GitHub si no tenés llave SSH o PAT)"
echo ""
echo "    Si te pide usuario/contraseña, GitHub ya no acepta password normal."
echo "    Tenés que usar un Personal Access Token (PAT):"
echo "      1) Ir a https://github.com/settings/tokens/new"
echo "      2) Crear token con scope 'repo'"
echo "      3) Pegar el token cuando te pida 'Password'"
echo ""

git push -u "$REMOTE_NAME" "$BRANCH"

echo ""
echo "==> Listo. Revisá: $REMOTE_URL"
