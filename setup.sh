#!/usr/bin/env bash
set -euo pipefail

echo "=== Startup Tracker Setup ==="

# 1. Python dependencies
echo "[1/3] Installing Python dependencies..."
pip install -r requirements.txt

# 2. Node.js check
if ! command -v node &>/dev/null; then
    echo "WARNING: Node.js not found. Twitter/LinkedIn monitoring requires Node.js."
    echo "  Install from: https://nodejs.org/"
else
    echo "  Node.js: $(node --version)"
fi

# 3. Apify MCP CLI
if command -v npm &>/dev/null; then
    echo "[2/3] Installing Apify MCP CLI..."
    npm install -g @apify/mcpc
else
    echo "[2/3] Skipped: npm not found"
fi

# 4. State directory
mkdir -p state

# 5. .env template
if [ ! -f .env ]; then
    echo "[3/3] Creating .env from template..."
    cp .env.example .env
    echo "  -> Edit .env and add your API keys:"
    echo "     TAVILY_API_KEY=your_key_here"
    echo "     APIFY_TOKEN=your_token_here"
else
    echo "[3/3] .env already exists"
fi

echo ""
echo "Setup complete. Run 'python tracker.py --validate' to check your configuration."
