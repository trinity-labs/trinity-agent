#!/usr/bin/env sh
set -eu

# A client can close the JSON-lines pipe while Python is still writing. Avoid
# leaving a large core dump in the repository when that happens.
ulimit -c 0 2>/dev/null || true

TRINITY_SECRET_DIRECTORY="/home/coder/TRINITY-DOCKER/etc/trinity/docker-trinity"
for trinity_secret_name in TRINITY_OVHCLOUD_API_KEY TRINITY_DEEPSEEK_API_KEY TRINITY_OPENAI_API_KEY TRINITY_OVHCLOUD_APPLICATION_KEY TRINITY_OVHCLOUD_APPLICATION_SECRET TRINITY_OVHCLOUD_CONSUMER_KEY TRINITY_OVHCLOUD_PROJECT TRINITY_OVHCLOUD_API_BASE_URL; do
  trinity_secret_file="/run/secrets/$trinity_secret_name"
  if [ ! -r "$trinity_secret_file" ]; then
    trinity_secret_file="$TRINITY_SECRET_DIRECTORY/$trinity_secret_name"
  fi
  if [ -r "$trinity_secret_file" ]; then
    export "$trinity_secret_name=$(cat "$trinity_secret_file")"
  fi
done
case "${TRINITY_AGENT_PROVIDER:-ovhcloud}" in
  ovhcloud) trinity_provider_key=${TRINITY_OVHCLOUD_API_KEY:-} ;;
  deepseek) trinity_provider_key=${TRINITY_DEEPSEEK_API_KEY:-} ;;
  openai) trinity_provider_key=${TRINITY_OPENAI_API_KEY:-} ;;
  *) echo "TRINITY Agent: fournisseur inconnu." >&2; exit 1 ;;
esac
if [ -z "$trinity_provider_key" ] && [ "${1:-}" != "--billing-status" ]; then
  echo "TRINITY Agent: aucune clé API configurée pour ${TRINITY_AGENT_PROVIDER:-ovhcloud}." >&2
  exit 1
fi

export TRINITY_AGENT_WORKSPACE="${TRINITY_AGENT_WORKSPACE:-$PWD}"
export PYDANTIC_AI_NO_BANNER=1
export PYTHONPATH="/home/coder/TRINITY-DOCKER/var/docker/docker_ide${PYTHONPATH:+:$PYTHONPATH}"

exec /opt/trinity-agent/bin/python -m trinity_agent "$@"
