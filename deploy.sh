#!/usr/bin/env bash
set -Eeuo pipefail

BOT_DIR="/home/rei/ggsel_seller_helper"
COMPOSE_FILE="/var/lib/docker/volumes/dockhand_dockhand_data/_data/stacks/LTS Server/ggselbot/compose.yaml"
IMAGE="paparei/ggsel-bot"

cd "$BOT_DIR"

VERSION="$(sed -n 's/^__version__ = "\([^"]*\)"/\1/p' __init__.py)"
if [[ -z "$VERSION" ]]; then
  echo "Could not read the bot version from __init__.py" >&2
  exit 1
fi

if [[ ! -s .env ]]; then
  echo "Missing or empty environment file: $BOT_DIR/.env" >&2
  exit 1
fi

echo "Building $IMAGE:$VERSION"
sudo docker build --pull \
  -t "$IMAGE:$VERSION" \
  -t "$IMAGE:latest" \
  .

sudo docker push "$IMAGE:$VERSION"
sudo docker push "$IMAGE:latest"

sudo docker compose \
  -p "ggselbot" \
  -f "$COMPOSE_FILE" \
  up -d --pull always --force-recreate ggsel-bot

echo "Running container image:"
sudo docker inspect ggsel-bot --format '{{.Config.Image}} {{.Image}}'

sudo docker compose \
  -p "ggselbot" \
  -f "$COMPOSE_FILE" \
  logs --tail=50 ggsel-bot
