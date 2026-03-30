#!/bin/bash
set -e

echo "=== Observius Dev Setup ==="

# Check prerequisites
command -v docker >/dev/null 2>&1 || { echo "Docker is required. Install: https://docs.docker.com/get-docker/"; exit 1; }
command -v docker compose >/dev/null 2>&1 || command -v docker-compose >/dev/null 2>&1 || { echo "Docker Compose is required."; exit 1; }

# Create .env from example if it doesn't exist
if [ ! -f .env ]; then
  cp .env.example .env
  echo ""
  echo "Created .env from .env.example"
  echo "  You MUST set ANTHROPIC_API_KEY in .env before running tasks."
  echo "  Get one at: https://console.anthropic.com/settings/keys"
  echo ""
else
  echo ".env already exists, skipping"
fi

# Install dashboard dependencies (needed even for Docker if you want to run locally)
if [ -d dashboard ]; then
  echo "Installing dashboard dependencies..."
  cd dashboard && npm install && cd ..
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Set ANTHROPIC_API_KEY in .env"
echo "  2. Run: make dev"
echo "  3. Open: http://localhost:3000"
echo "  4. Login with: cu_test_testkey1234567890abcdef12"
echo "  5. Go to /tasks/new to create your first task"
echo ""
