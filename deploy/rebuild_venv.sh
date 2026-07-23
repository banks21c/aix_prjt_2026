#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/ubuntu/nextfinup"
VENV_DIR="$PROJECT_DIR/venv"
SERVICE_NAME="nextfinup.service"
RESTART_SERVICE=true
CHECK_ONLY=false
FORCE=false

usage() {
  echo "Usage: $0 [--check-only] [--force] [--no-restart]"
  echo "  --check-only  only report whether venv is broken, do not rebuild (exit 2 if broken)"
  echo "  --force       rebuild even if venv looks OK"
  echo "  --no-restart  rebuild venv but do not restart $SERVICE_NAME"
  exit 1
}

for arg in "$@"; do
  case "$arg" in
    --check-only) CHECK_ONLY=true ;;
    --force) FORCE=true ;;
    --no-restart) RESTART_SERVICE=false ;;
    -h|--help) usage ;;
    *) echo "Unknown option: $arg"; usage ;;
  esac
done

cd "$PROJECT_DIR"

SYSTEM_PY_VERSION="$(python3 --version 2>&1 | awk '{print $2}')"
echo "System python3 version: $SYSTEM_PY_VERSION"

VENV_PY_VERSION="missing"
if [ -x "$VENV_DIR/bin/python3" ]; then
  VENV_PY_VERSION="$("$VENV_DIR/bin/python3" --version 2>&1 | awk '{print $2}')"
fi
echo "venv python3 version:   $VENV_PY_VERSION"

BROKEN=false
if [ "$VENV_PY_VERSION" = "missing" ]; then
  BROKEN=true
elif ! "$VENV_DIR/bin/python3" -c "import gunicorn, django" >/dev/null 2>&1; then
  BROKEN=true
fi

if [ "$BROKEN" = false ] && [ "$FORCE" = false ]; then
  echo "venv looks OK (gunicorn/django import fine). Nothing to do. Use --force to rebuild anyway."
  exit 0
fi

if [ "$BROKEN" = true ]; then
  echo "venv is broken or missing."
else
  echo "venv looks OK, but --force was given."
fi

if [ "$CHECK_ONLY" = true ]; then
  echo "--check-only given, exiting without rebuilding."
  exit 2
fi

if [ -d "$VENV_DIR" ]; then
  BACKUP_DIR="$PROJECT_DIR/venv.bak.$(date +%Y%m%d%H%M%S)"
  echo "Backing up existing venv to $BACKUP_DIR"
  mv "$VENV_DIR" "$BACKUP_DIR"
fi

echo "Creating new venv with system python3 ($SYSTEM_PY_VERSION)"
python3 -m venv "$VENV_DIR"

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q

echo "Installing requirements.txt (can take several minutes: playwright/scikit-learn/lightgbm are large)..."
pip install -r requirements.txt

echo "Running Django system check..."
python manage.py check

deactivate

if [ "$RESTART_SERVICE" = true ]; then
  echo "Restarting $SERVICE_NAME"
  sudo systemctl reset-failed "$SERVICE_NAME" || true
  sudo systemctl restart "$SERVICE_NAME"
  sleep 3
  sudo systemctl status "$SERVICE_NAME" --no-pager -l || true

  if sudo ss -tlnp | grep -q ':9000'; then
    echo "OK: something is listening on port 9000."
  else
    echo "WARNING: nothing listening on port 9000. Check: sudo journalctl -u $SERVICE_NAME -n 50"
  fi

  curl -s -o /dev/null -w "Local backend HTTP %{http_code}\n" http://127.0.0.1:9000/ || echo "curl to local backend failed"
else
  echo "Skipping service restart (--no-restart given). Run manually:"
  echo "  sudo systemctl reset-failed $SERVICE_NAME && sudo systemctl restart $SERVICE_NAME"
fi

echo "Done."
