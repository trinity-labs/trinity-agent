#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
ACTION=${1:-status}
MODEL=${2:-dolphin}

case "$MODEL" in
  dolphin)
    export TRINITY_LOCAL_MODEL_FILE=Dolphin3.0-Llama3.1-8B-Q4_K_M.gguf
    export TRINITY_LOCAL_MODEL_ALIAS=dolphin3-pentest
    ;;
  hermes|nous)
    export TRINITY_LOCAL_MODEL_FILE=Hermes-3-Llama-3.1-8B.Q4_K_M.gguf
    export TRINITY_LOCAL_MODEL_ALIAS=hermes3-pentest
    ;;
  *) echo "Modèle attendu: dolphin ou hermes" >&2; exit 2 ;;
esac

case "$ACTION" in
  start|switch)
    docker compose -f "$ROOT/docker-compose.yml" --profile local-model up -d --force-recreate trinity-local-llm
    ;;
  stop)
    docker compose -f "$ROOT/docker-compose.yml" --profile local-model stop trinity-local-llm
    ;;
  status)
    docker compose -f "$ROOT/docker-compose.yml" --profile local-model ps trinity-local-llm
    ;;
  *) echo "Action attendue: start, switch, stop ou status" >&2; exit 2 ;;
esac
