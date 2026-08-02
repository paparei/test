#!/bin/bash

# Navigate to the bot directory. Halt if the directory is missing.
cd ~/ggsel_seller_helper || exit

# 1. Build the new Docker image locally
sudo docker build -t paparei/ggsel-bot:latest .

# 2. Push the newly built image to Docker Hub
sudo docker push paparei/ggsel-bot:latest

# 3. Instruct Docker Compose to gracefully pull the new image and recreate the container
# We define the stack name (-p "ggselbot") to match Dockhand's internal naming convention
sudo docker compose \
  -p "ggselbot" \
  -f "/var/lib/docker/volumes/dockhand_dockhand_data/_data/stacks/LTS Server/ggselbot/compose.yaml" \
  up -d
