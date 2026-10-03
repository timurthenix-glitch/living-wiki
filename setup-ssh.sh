#!/bin/bash
set -e

echo "=== Configuring SSH Access ==="
mkdir -p /root/.ssh
chmod 700 /root/.ssh

KEY1="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICqPPYyznGG8Ts7jV+FqRp+e3bzpS/UtM16VUwQ1lATa mura@DESKTOP-1"
KEY2="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGab+u+rtLjuusjf6GriAmePGDb6vDFeIGc7d7kSgJAo"

grep -qF "$KEY1" /root/.ssh/authorized_keys 2>/dev/null || echo "$KEY1" >> /root/.ssh/authorized_keys
grep -qF "$KEY2" /root/.ssh/authorized_keys 2>/dev/null || echo "$KEY2" >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

if command -v fail2ban-client >/dev/null 2>&1; then
    echo "Unbanning fail2ban..."
    fail2ban-client unban --all 2>/dev/null || true
fi

if command -v ufw >/dev/null 2>&1; then
    echo "Configuring UFW..."
    ufw allow 22/tcp 2>/dev/null || true
fi

sed -i 's/^#*PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config 2>/dev/null || true
sed -i 's/^#*PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config 2>/dev/null || true
systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true

echo "=== Updating Jarvis Website ==="
if [ -d "/opt/jarvis/web" ]; then
    cd /opt/jarvis/web
    git pull origin main || true
    npm run build:client || true
    docker compose restart web || true
fi

echo "=== READY: Antigravity SSH is now ACTIVE! ==="
