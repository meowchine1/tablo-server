#!/bin/bash

sudo tee /etc/systemd/system/tablo-server.service > /dev/null <<EOF
[Unit]
Description=Tablo NMEA Generator
After=dev-ttyAMA3.device
Requires=dev-ttyAMA3.device

[Service]
Type=simple
WorkingDirectory=/home/tablo/tablo-server
ExecStart=/usr/bin/python3 /home/tablo/tablo-server/generate_send_nmea_gprmc.py
Restart=on-failure
RestartSec=5
User=tablo

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable tablo-server.service
sudo systemctl start tablo-server.service

sudo systemctl status tablo-server.service
