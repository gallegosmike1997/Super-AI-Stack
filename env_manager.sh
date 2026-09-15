#!/usr/bin/env bash
set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "$1" in
  activate-root)
    echo "[Super AI Stack] Activating root venv"
    source "$PROJECT_ROOT/.venv/bin/activate"
    ;;
  activate-service)
    if [ -z "$2" ]; then
      echo "Usage: ./env_manager.sh activate-service <service_name>"
      exit 1
    fi
    SERVICE_DIR="$PROJECT_ROOT/$2"
    if [ ! -d "$SERVICE_DIR" ]; then
      echo "Service '$2' not found."
      exit 1
    fi
    echo "[Super AI Stack] Activating venv for service: $2"
    source "$SERVICE_DIR/venv/bin/activate"
    ;;
  list)
    echo "[Super AI Stack] Services:"
    ls "$PROJECT_ROOT"
    ;;
  *)
    echo "Usage:"
    echo "  ./env_manager.sh activate-root"
    echo "  ./env_manager.sh activate-service <service_name>"
    echo "  ./env_manager.sh list"
    ;;
esac
