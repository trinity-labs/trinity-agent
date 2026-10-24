#!/usr/bin/env sh
set -eu

OVH_SECRET_DIRECTORY="/home/coder/TRINITY-DOCKER/etc/trinity/docker-trinity"
for ovh_secret_name in OVH_AI_API_KEY DEEPSEEK_API_KEY OVH_APPLICATION_KEY OVH_APPLICATION_SECRET OVH_CONSUMER_KEY OVH_CLOUD_PROJECT OVH_API_BASE_URL; do
  ovh_secret_file="/run/secrets/$ovh_secret_name"
  if [ ! -r "$ovh_secret_file" ]; then
    ovh_secret_file="$OVH_SECRET_DIRECTORY/$ovh_secret_name"
  fi
  if [ -r "$ovh_secret_file" ]; then
    export "$ovh_secret_name=$(cat "$ovh_secret_file")"
  fi
done
OVH_AI_API_KEY="${OVH_AI_API_KEY:-${OVHCLOUD_API_KEY:-}}"
if [ "${OVH_AGENT_PROVIDER:-ovhcloud}" = "deepseek" ]; then
  if [ -z "${DEEPSEEK_API_KEY:-}" ] && [ "${1:-}" != "--billing-status" ]; then
    echo "TRINITY Agent: aucune clé DeepSeek configurée." >&2
    exit 1
  fi
elif [ -z "$OVH_AI_API_KEY" ] && [ "${1:-}" != "--billing-status" ]; then
  echo "OVH Agent: aucune clé OVH configurée." >&2
  exit 1
fi

export OVH_AI_API_KEY
export OVHCLOUD_API_KEY="$OVH_AI_API_KEY"
export OVH_AGENT_WORKSPACE="${OVH_AGENT_WORKSPACE:-$PWD}"

exec /opt/ovh-agent/bin/python \
  /home/coder/TRINITY-DOCKER/var/docker/docker_ide/ovh-agent.py \
  "$@"
