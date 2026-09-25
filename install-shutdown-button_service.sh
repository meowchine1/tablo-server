#!/bin/bash

set -e

echo "=== Installing shutdown button ==="

echo "[1/6] Installing gpiozero..."
sudo apt update
sudo apt install -y python3-gpiozero

echo "[2/6] Creating shutdown button script..."
sudo tee /usr/local/bin/shutdown-button.py > /dev/null <<'PYTHON'
#!/usr/bin/env python3

from gpiozero import Button
from signal import pause
import subprocess

button = Button(17, pull_up=True, bounce_time=0.1)

def shutdown():
    subprocess.run(["/usr/sbin/shutdown", "-h", "now"], check=False)

button.when_pressed = shutdown

pause()
PYTHON

sudo chmod +x /usr/local/bin/shutdown-button.py

echo "[3/6] Creating systemd service..."
sudo tee /etc/systemd/system/shutdown-button.service > /dev/null <<'SERVICE'
[Unit]
Description=Raspberry Pi Shutdown Button

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/bin/shutdown-button.py
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
SERVICE

echo "[4/6] Reloading systemd..."
sudo systemctl daemon-reload

echo "[5/6] Enabling and starting service..."
sudo systemctl enable shutdown-button.service
sudo systemctl restart shutdown-button.service

echo "[6/6] Checking service status..."
sudo systemctl --no-pager status shutdown-button.service

echo
echo "=== Installation complete ==="
echo "Button: GPIO17 (physical pin 11)"
echo "GND:    physical pin 9"
echo "Press the button to shut down the Raspberry Pi."
