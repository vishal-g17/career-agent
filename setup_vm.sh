#!/usr/bin/env bash
# ==============================================================================
# Autonomous Career & Outreach Multi-Agent System - VM Setup Script
# Target OS: Ubuntu 22.04 / 24.04 LTS or Debian 12
# ==============================================================================

set -e

echo "🚀 Starting Cloud VM Setup for LinkedIn Multi-Agent System..."

# 1. Update OS and install system prerequisites
echo "📦 Installing system packages & dependencies..."
sudo apt-get update -y
sudo apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    git \
    tmux \
    curl \
    wget \
    build-essential \
    libsqlite3-dev \
    xvfb

# 2. Setup Python Virtual Environment
INSTALL_DIR=$(pwd)
echo "🐍 Creating Python virtual environment in $INSTALL_DIR/venv..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

source venv/bin/activate
pip install --upgrade pip

# 3. Install Python requirements
echo "📚 Installing Python packages from requirements.txt..."
pip install -r requirements.txt

# 4. Install Playwright & Headless Chromium with system libraries
echo "🌐 Installing Playwright Chromium and system dependencies..."
playwright install --with-deps chromium

# 5. Check for .env file
if [ ! -f ".env" ]; then
    echo "⚠️ Warning: .env file not found. Copying .env.example -> .env"
    cp .env.example .env
    echo "👉 PLEASE EDIT .env WITH YOUR ACTUAL CREDENTIALS BEFORE STARTING SERVICES!"
fi

# 6. Ensure data directory exists
mkdir -p data logs

# 7. Generate systemd service files for 24/7 autonomous execution
CURRENT_USER=$(whoami)
PYTHON_BIN="$INSTALL_DIR/venv/bin/python3"

echo "⚙️ Creating systemd service units for 24/7 background execution..."

# Service 1: Discord Prompt Listener (Remote CLI & Prompt Engine)
sudo tee /etc/systemd/system/linkedin-discord-listener.service > /dev/null <<EOF
[Unit]
Description=LinkedIn Multi-Agent Discord Prompt Listener
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$INSTALL_DIR
Environment="PYTHONPATH=$INSTALL_DIR"
ExecStart=$PYTHON_BIN -u src/agents/discord_listener.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Service 2: Portal ATS Worker (Continuous Job Applications)
sudo tee /etc/systemd/system/linkedin-portal-worker.service > /dev/null <<EOF
[Unit]
Description=LinkedIn Multi-Agent Portal & ATS Worker
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$INSTALL_DIR
Environment="PYTHONPATH=$INSTALL_DIR"
ExecStart=$PYTHON_BIN -u scripts/run_portal_worker.py
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
EOF

# Service 3: Cold Email Worker (Scheduled safe outreach batches)
sudo tee /etc/systemd/system/linkedin-email-worker.service > /dev/null <<EOF
[Unit]
Description=LinkedIn Multi-Agent Cold Email Worker
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$INSTALL_DIR
Environment="PYTHONPATH=$INSTALL_DIR"
ExecStart=$PYTHON_BIN -u scripts/run_email_worker.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload

echo ""
echo "=========================================================================="
echo "🎉 VM SETUP COMPLETE!"
echo "=========================================================================="
echo "Next steps:"
echo "1. Verify/Edit your credentials:  nano .env"
echo "2. Copy your database:            scp ~/.linkedin_agent.db to ~/linkedin-multiagent/agent.db"
echo "3. Start and enable all services:"
echo "     sudo systemctl enable --now linkedin-discord-listener"
echo "     sudo systemctl enable --now linkedin-portal-worker"
echo "     sudo systemctl enable --now linkedin-email-worker"
echo ""
echo "4. Check service status:"
echo "     sudo systemctl status linkedin-discord-listener"
echo "=========================================================================="
