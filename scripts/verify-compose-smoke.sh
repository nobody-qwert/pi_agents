#!/usr/bin/env bash
set -euo pipefail

mkdir -p projects
trap 'docker compose down' EXIT

docker compose up -d --wait postgres api web
curl --fail --silent --show-error --output /dev/null http://localhost:8000/health
curl --fail --silent --show-error --output /dev/null \
  -H 'X-Dev-User: user_smoke' \
  http://localhost:3000/api/v1/system/graph
