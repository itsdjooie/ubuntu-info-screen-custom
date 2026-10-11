#!/usr/bin/env bash
# ==============================================================================
# MLHK Server Monitor - One-Line Installer & Systemd Service Setup
# Werkt op: Ubuntu, Debian, Proxmox VE 8.x / 9.x
# ==============================================================================
set -e

echo "======================================================"
echo "    MLHK SERVER MONITOR INSTALLER (Portrait Console)  "
echo "======================================================"

INSTALL_USER="mlhk-monitor"
INSTALL_DIR="/usr/local/bin"
SERVICE_FILE="/etc/systemd/system/mlhk-monitor.service"

# 1. Installeer vereiste pakketten
echo "[1/5] Installeren van benodigde tools (python3, fbset, btop)..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip fbset btop curl jq openssh-client

# 2. Maak service-user aan indien deze nog niet bestaat
if ! id "$INSTALL_USER" &>/dev/null; then
    echo "[2/5] Aanmaken van dedicated gebruiker '$INSTALL_USER'..."
    useradd -m -s /bin/bash "$INSTALL_USER"
    usermod -aG tty,video "$INSTALL_USER" 2>/dev/null || true
fi

# 3. Kopieer monitor executable
echo "[3/5] Kopiëren van mlhk-monitor script naar $INSTALL_DIR..."
cp mlhk-monitor.py "$INSTALL_DIR/mlhk-monitor"
chmod +x "$INSTALL_DIR/mlhk-monitor"

# 4. Stel TTY1 auto-login en console rechten in
echo "[4/5] Instellen van TTY1 console & permissies..."
mkdir -p /etc/systemd/system/getty@tty1.service.d/
cat << 'EOF' > /etc/systemd/system/getty@tty1.service.d/autologin.conf
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin mlhk-monitor --noclear %I $TERM
EOF

# Zorg dat mlhk-monitor direct start bij login op tty1
BASH_PROFILE="/home/$INSTALL_USER/.bash_profile"
cat << 'EOF' > "$BASH_PROFILE"
# Start MLHK Monitor automatisch als ingelogd op tty1
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    exec /usr/local/bin/mlhk-monitor
fi
EOF
chown "$INSTALL_USER:$INSTALL_USER" "$BASH_PROFILE"

# Udev regel voor schermbeheer / fbcon rotatie permissies
cat << 'EOF' > /etc/udev/rules.d/99-fb-permissions.rules
KERNEL=="fb0", RUN+="/bin/chmod 666 /sys/class/graphics/fb0/blank"
SUBSYSTEM=="graphics", RUN+="/bin/chmod 666 /sys/class/graphics/fbcon/rotate /sys/class/graphics/fbcon/rotate_all"
EOF
udevadm control --reload-rules || true

# 5. Afronding
echo "[5/5] Installatie voltooid!"
echo ""
echo "Herstart getty@tty1 om de monitor direct te tonen:"
echo "  systemctl daemon-reload"
echo "  systemctl restart getty@tty1"
echo ""
echo "Om de schermrotatie in te stellen naar 270° portret (1080x1920):"
echo "  echo 3 > /sys/class/graphics/fbcon/rotate_all"
echo "======================================================"
