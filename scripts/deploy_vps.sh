#!/usr/bin/env bash
# CallBot / VoiceStream deploy script for Amazon Linux 2023.
# Targets: ec2-user @ 13.60.193.150 (AWS eu-north-1), callbot.duckdns.org.
#
# Idempotent: safe to re-run. Each phase checks before acting.
# Run as ec2-user (passwordless sudo expected).
#
# Usage:
#   ssh ec2-user@13.60.193.150
#   curl -fsSL https://raw.githubusercontent.com/aabid2947/CallBot/main/scripts/deploy_vps.sh -o deploy.sh
#   chmod +x deploy.sh
#   ./deploy.sh
#
# The script will prompt for the secrets it needs (GROQ key, Deepgram key,
# DuckDNS token, TURN creds). Nothing is logged or echoed.

set -euo pipefail

DOMAIN="callbot.duckdns.org"
APP_DIR="$HOME/CallBot"
REPO_URL="https://github.com/aabid2947/CallBot.git"
SERVICE_NAME="callbot"
APP_PORT="8000"
RUN_USER="ec2-user"

phase() { echo; echo "============================================================"; echo "  $1"; echo "============================================================"; }
ok()    { echo "  [OK] $1"; }
note()  { echo "  [..] $1"; }
warn()  { echo "  [!!] $1" >&2; }
die()   { echo "  [XX] $1" >&2; exit 1; }

# --- Preflight ---------------------------------------------------------------

phase "0. Preflight"
[ "$(whoami)" = "$RUN_USER" ] || die "run as $RUN_USER, not $(whoami)"
sudo -n true 2>/dev/null || die "passwordless sudo required"
[ "$(uname)" = "Linux" ] || die "Linux only"
grep -q 'Amazon Linux' /etc/os-release || warn "expected Amazon Linux 2023; continuing"
ok "running as $RUN_USER with sudo"

# --- Collect secrets up front (so the script can run unattended after) ------

phase "1. Collect secrets (typed input is hidden)"

read_secret() {  # arg1=var-name, arg2=prompt
    local _v
    read -r -s -p "  $2: " _v
    echo
    [ -n "$_v" ] || die "$1 cannot be empty"
    printf -v "$1" '%s' "$_v"
}

read_secret GROQ_API_KEY      "GROQ_API_KEY (https://console.groq.com/keys)"
read_secret DEEPGRAM_API_KEY  "DEEPGRAM_API_KEY (https://console.deepgram.com)"
read_secret DUCKDNS_TOKEN     "DUCKDNS_TOKEN  (https://www.duckdns.org -> shown after sign in)"

echo
echo "  TURN provider: openrelay.metered.ca was UNREACHABLE from this box."
echo "  Recommended: Cloudflare Realtime TURN (free, anycast, EU PoP)."
echo "    1) https://dash.cloudflare.com -> Calls -> Create TURN App."
echo "    2) Copy the 'TURN URL' / 'username' / 'credential' fields."
echo "  If you only have host:port from a provider, prefix with 'turn:' below."
echo
read -r -p "  TURN_URLS (comma-separated, each MUST start with turn:/turns:/stun:/stuns:): " TURN_URLS
read -r -p "  TURN_USERNAME: " TURN_USERNAME
read_secret TURN_CREDENTIAL "TURN_CREDENTIAL"

# Optional: email for Let's Encrypt expiry notices
read -r -p "  Email for Let's Encrypt notifications: " LE_EMAIL
[ -n "$LE_EMAIL" ] || die "Let's Encrypt requires an email"

# --- Sanity-check that the domain points here -------------------------------

phase "2. DNS sanity check"
MY_IP="$(curl -fsS --max-time 5 https://api.ipify.org)"
DUCK_IP="$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || true)"
echo "  This VPS public IP : $MY_IP"
echo "  $DOMAIN currently  : ${DUCK_IP:-<unresolved>}"

if [ "$MY_IP" != "$DUCK_IP" ]; then
    note "DuckDNS does not point here yet. Updating now via the token..."
    UPD="https://www.duckdns.org/update?domains=callbot&token=${DUCKDNS_TOKEN}&ip=${MY_IP}"
    RESP="$(curl -fsS --max-time 10 "$UPD" || true)"
    if [ "$RESP" = "OK" ]; then
        ok "DuckDNS updated -> $MY_IP. Wait ~60s for global propagation."
    else
        die "DuckDNS update failed: response was '$RESP' (check token)"
    fi
else
    ok "DuckDNS already points here"
fi

# --- Persistent DuckDNS updater (cron, every 5 min) -------------------------

phase "3. DuckDNS auto-updater"
DUCK_DIR="$HOME/.duckdns"
mkdir -p "$DUCK_DIR"
cat > "$DUCK_DIR/update.sh" <<EOF
#!/usr/bin/env bash
# Updates callbot.duckdns.org to this host's current public IP.
curl -fsS --max-time 10 "https://www.duckdns.org/update?domains=callbot&token=${DUCKDNS_TOKEN}&ip=" >> "$DUCK_DIR/log" 2>&1
echo " \$(date -u +%FT%TZ)" >> "$DUCK_DIR/log"
EOF
chmod 700 "$DUCK_DIR/update.sh"
chmod 600 "$DUCK_DIR/log" 2>/dev/null || true
( crontab -l 2>/dev/null | grep -v 'duckdns/update.sh' ; echo "*/5 * * * * $DUCK_DIR/update.sh" ) | crontab -
ok "cron entry installed (runs every 5 min)"

# --- Swap (1 GB) ------------------------------------------------------------

phase "4. Swap (1 GB) — RAM is only 916 MB"
if swapon --show | grep -q '/swapfile'; then
    ok "swap already present"
else
    sudo fallocate -l 1G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile >/dev/null
    sudo swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
    ok "swapfile mounted and added to /etc/fstab"
fi

# --- System packages --------------------------------------------------------

phase "5. System packages (dnf)"
sudo dnf -y -q install \
    git \
    python3.11 python3.11-pip python3.11-devel \
    gcc \
    nginx \
    augeas-libs  # needed by some certbot DNS plugins; harmless if unused
ok "git, python3.11, gcc, nginx installed"

# Certbot via pip (the dnf snap is patchy on AL2023; pip is recommended).
if ! command -v certbot >/dev/null 2>&1; then
    sudo python3.11 -m venv /opt/certbot-venv
    sudo /opt/certbot-venv/bin/pip install -q --upgrade pip
    sudo /opt/certbot-venv/bin/pip install -q certbot certbot-nginx
    sudo ln -sf /opt/certbot-venv/bin/certbot /usr/local/bin/certbot
    ok "certbot installed at /usr/local/bin/certbot"
else
    ok "certbot already present"
fi

# --- Application checkout ---------------------------------------------------

phase "6. Application checkout"
if [ -d "$APP_DIR/.git" ]; then
    note "existing checkout — pulling latest"
    git -C "$APP_DIR" pull --ff-only
else
    git clone "$REPO_URL" "$APP_DIR"
fi
ok "code at $APP_DIR"

# --- Python venv + deps -----------------------------------------------------

phase "7. Python venv + dependencies"
cd "$APP_DIR"
if [ ! -d .venv ]; then
    python3.11 -m venv .venv
fi
.venv/bin/python -m pip install -q --upgrade pip wheel setuptools
.venv/bin/python -m pip install -q -r requirements.txt
.venv/bin/python -m pip install -q -e ".[dev,web]"
ok "Python deps installed in $APP_DIR/.venv"

# --- .env -------------------------------------------------------------------

phase "8. Write .env"
umask 077
cat > "$APP_DIR/.env" <<EOF
# Generated by deploy_vps.sh on $(date -u +%FT%TZ)
GROQ_API_KEY=${GROQ_API_KEY}
DEEPGRAM_API_KEY=${DEEPGRAM_API_KEY}

# DB (default SQLite; switch to Supabase by changing this and re-installing
# the [postgres] extra: pip install -e ".[postgres]")
DATABASE_URL=sqlite:///${APP_DIR}/callbot.db

# Voice defaults — change here, no code edits
LLM_MODEL=llama-3.3-70b-versatile
STT_MODEL=nova-3
TTS_VOICE=aura-2-thalia-en
BUSINESS_NAME=City Care Hospital

# TURN — every URL MUST start with turn:/turns:/stun:/stuns:
TURN_URLS=${TURN_URLS}
TURN_USERNAME=${TURN_USERNAME}
TURN_CREDENTIAL=${TURN_CREDENTIAL}

# Server bind
HOST=127.0.0.1
PORT=${APP_PORT}
EOF
chmod 600 "$APP_DIR/.env"
umask 022
ok ".env written (chmod 600)"

# --- Seed the test booking row ----------------------------------------------

phase "9. Seed test booking request"
"$APP_DIR/.venv/bin/python" "$APP_DIR/scripts/seed_test_request.py" || warn "seed failed (DB might already have an active row — fine)"

# --- systemd unit -----------------------------------------------------------

phase "10. systemd service"
sudo tee /etc/systemd/system/${SERVICE_NAME}.service >/dev/null <<EOF
[Unit]
Description=CallBot voice agent (VoiceStream)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/python -m server
Restart=always
RestartSec=5
# Memory guard so an OOM doesn't take the whole box down.
MemoryHigh=600M
MemoryMax=750M

# Logs to journalctl
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME" >/dev/null
sudo systemctl restart "$SERVICE_NAME"
sleep 3
sudo systemctl is-active --quiet "$SERVICE_NAME" || {
    sudo journalctl -u "$SERVICE_NAME" -n 40 --no-pager
    die "service failed to start"
}
ok "$SERVICE_NAME service enabled and running"

# --- nginx reverse proxy (HTTP first; certbot adds 443 next) ----------------

phase "11. nginx reverse proxy"
sudo tee /etc/nginx/conf.d/${SERVICE_NAME}.conf >/dev/null <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    # WebRTC media is P2P UDP and does NOT flow through nginx.
    # nginx only proxies the HTTPS signaling: GET /, /api/offer, /api/ice_servers, /health.

    client_max_body_size 1m;

    location / {
        proxy_pass http://127.0.0.1:${APP_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
    }
}
EOF
sudo nginx -t
sudo systemctl enable --now nginx >/dev/null
sudo systemctl reload nginx
ok "nginx serving $DOMAIN on :80"

# --- TLS via Let's Encrypt --------------------------------------------------

phase "12. Let's Encrypt TLS (callbot.duckdns.org)"
echo "  Reminder: AWS Security Group MUST allow inbound 80 and 443 from 0.0.0.0/0"
echo "  before certbot can complete the HTTP-01 challenge."
read -r -p "  Has the Security Group been updated? [y/N] " ack
case "$ack" in
    [yY]*) ;;
    *) warn "skipping TLS — re-run this script after opening 80/443 in the AWS SG"; exit 0 ;;
esac

if sudo /usr/local/bin/certbot certificates 2>/dev/null | grep -q "$DOMAIN"; then
    ok "certificate for $DOMAIN already present"
else
    sudo /usr/local/bin/certbot --nginx \
        --non-interactive \
        --agree-tos \
        -m "$LE_EMAIL" \
        -d "$DOMAIN" \
        --redirect
fi

# Auto-renew via systemd timer (certbot ships one)
sudo systemctl enable --now certbot-renew.timer 2>/dev/null || true
ok "TLS configured. https://$DOMAIN/ should now work."

# --- Final summary ----------------------------------------------------------

phase "13. Done"
echo
echo "  Service:      sudo systemctl status $SERVICE_NAME"
echo "  Logs (live):  sudo journalctl -u $SERVICE_NAME -f"
echo "  App logs:     $APP_DIR/logs/voicestream.log"
echo "  Test URL:     https://$DOMAIN/"
echo "  Relay-only:   https://$DOMAIN/?relay   (forces TURN; strict pre-test)"
echo
echo "  To redeploy after a code change:"
echo "    cd $APP_DIR && git pull && sudo systemctl restart $SERVICE_NAME"
echo
echo "  To swap models without redeploy:"
echo "    edit $APP_DIR/.env (LLM_MODEL=, STT_MODEL=, TTS_VOICE=, TURN_URLS=)"
echo "    sudo systemctl restart $SERVICE_NAME"
echo
