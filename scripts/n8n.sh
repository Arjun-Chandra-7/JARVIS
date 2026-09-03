#!/usr/bin/env bash
# Launch a local n8n automation server using Node (npx) or Docker, completely free!
# n8n gives you a visual workflow builder on port 5678 to link thousands of apps.

set -euo pipefail

PORT=${N8N_PORT:-5678}
echo "🤖 [J.A.R.V.I.S.] Initializing Protocol Nexus — n8n Automation Engine on port $PORT..."

if command -v npx >/dev/null 2>&1; then
    echo "⚡ Launching via npx (Node.js)... Open http://localhost:$PORT in your browser!"
    exec npx -y n8n@latest
elif command -v docker >/dev/null 2>&1; then
    echo "🐳 Launching via Docker container... Open http://localhost:$PORT in your browser!"
    exec docker run -it --rm --name jarvis-n8n -p "$PORT:5678" -v ~/.n8n:/home/node/.n8n docker.n8n.io/n8n/n8n:latest
else
    echo "❌ Error: Neither Node.js (npx) nor Docker is installed."
    echo "Please install Node.js (sudo apt install nodejs npm) or Docker to run n8n locally for free."
    exit 1
fi
