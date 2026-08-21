#!/bin/bash
set -e

VPS="root@204.168.197.101"
REMOTE_DIR="/root/writeoff_bot"

echo "==> Syncing files..."
rsync -avz --exclude='.venv' --exclude='data/' --exclude='.env' --exclude='__pycache__' \
  --exclude='*.pyc' --exclude='.git' \
  "/Users/sashasitnickyi/dev/Poster Write-off Bot/" \
  "$VPS:$REMOTE_DIR/"

echo "==> Installing dependencies..."
ssh "$VPS" "cd $REMOTE_DIR && pip install uv -q && uv sync --no-dev 2>&1 | tail -5"

echo "==> Installing systemd service..."
ssh "$VPS" "cp $REMOTE_DIR/writeoff-bot.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable writeoff-bot"

echo "==> Configuring nginx..."
ssh "$VPS" "cat > /etc/nginx/sites-available/writeoff-bot << 'EOF'
# Write-off bot — /writeoff/ proxy
# included from the main poster-bot nginx config
EOF"

echo "Done. Run: ssh $VPS 'systemctl restart writeoff-bot'"
