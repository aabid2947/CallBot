#!/usr/bin/env bash
# CallBot / VoiceStream deploy script for Amazon Linux 2023.
# Targets: ec2-user @ AWS EC2, callbot.duckdns.org.
#
# Zero prompts. All config lives in $APP_DIR/.env which YOU create
# before running this script. See .env.example for the full list.
#
# REQUIRED in .env:
#   GROQ_API_KEY=...
#   DEEPGRAM_API_KEY=...
#   TURN_URLS=turn:...
#   TURN_USERNAME=...
#   TURN_CREDENTIAL=...
#
# OPTIONAL in .env (skipped if blank):
#   DUCKDNS_TOKEN=...   -> script updates DNS + installs auto-update cron
#   LE_EMAIL=...        -> script runs certbot to install Let's Encrypt cert
#   LLM_MODEL=, STT_MODEL=, TTS_VOICE=, BUSINESS_NAME=, DATABASE_URL=,
#   HOST=, PORT=        -> have sane defaults if absent
#
# Idempotent. Safe to re-run.
#
# Usage from a fresh box:
#   1) ssh ec2-user@<vps-ip>
#   2) sudo dnf install -y git                # bootstrap the bootstrap
#   3) git clone https://github.com/aabid2947/CallBot.git
#   4) cd CallBot
#   5) cp .env.example .env && nano .env      # fill in the values
#   6) ./scripts/deploy_vps.sh

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOMAIN="callbot.duckdns.org"
REPO_URL="https://github.com/aabid2947/CallBot.git"
SERVICE_NAME="callbot"
RUN_USER="ec2-user"

phase() { echo; echo "============================================================"; echo "  $1"; echo "============================================================"; }
ok()    { echo "  [OK] $1"; }
note()  { echo "  [..] $1"; }
warn()  { echo "  [!!] $1" >&2; }
die()   { echo "  [XX] $1" >&2; exit 1; }

# --- 0. Preflight -----------------------------------------------------------

phase "0. Preflight"
[ "$(whoami)" = "$RUN_USER" ] || die "run as $RUN_USER, not $(whoami)"
sudo -n true 2>/dev/null || die "passwordless sudo required"
[ "$(uname)" = "Linux" ] || die "Linux only"
grep -q 'Amazon Linux' /etc/os-release || warn "expected Amazon Linux 2023; continuing"
ok "running as $RUN_USER with sudo from $APP_DIR"

# --- 1. System packages (install BEFORE we need git / python3.11 / etc) -----

phase "1. System packages (dnf)"
sudo dnf -y -q install \
    git \
    cronie \
    python3.11 python3.11-pip python3.11-devel \
    gcc \
    nginx \
    augeas-libs
sudo systemctl enable --now crond >/dev/null
ok "git, cron, python3.11, gcc, nginx installed"

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

# --- 2. Load .env -----------------------------------------------------------

phase "2. Load .env"
ENV_FILE="$APP_DIR/.env"
[ -f "$ENV_FILE" ] || die ".env not found at $ENV_FILE — copy .env.example to .env and fill it in first"
# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

# Required runtime keys — fail fast with a clear message.
for var in GROQ_API_KEY DEEPGRAM_API_KEY TURN_URLS TURN_USERNAME TURN_CREDENTIAL; do
    [ -n "${!var:-}" ] || die "$var is empty in $ENV_FILE (required)"
done

# Optional deploy-time keys (informational).
[ -n "${DUCKDNS_TOKEN:-}" ] && ok "DUCKDNS_TOKEN present -> DNS will be auto-updated" || note "DUCKDNS_TOKEN absent -> skipping DNS update (point $DOMAIN to this VPS manually if needed)"
[ -n "${LE_EMAIL:-}"      ] && ok "LE_EMAIL present -> Let's Encrypt cert will be installed" || note "LE_EMAIL absent -> skipping TLS (server will run on :80 only)"

# Defaults if .env didn't set them.
APP_PORT="${PORT:-8000}"
ok ".env loaded; required keys are set; using PORT=$APP_PORT"

# --- 3. DNS sanity check ----------------------------------------------------

phase "3. DNS sanity check"
MY_IP="$(curl -fsS --max-time 5 https://api.ipify.org)"
DUCK_IP="$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || true)"
echo "  This VPS public IP : $MY_IP"
echo "  $DOMAIN currently  : ${DUCK_IP:-<unresolved>}"

if [ -n "${DUCKDNS_TOKEN:-}" ] && [ "$MY_IP" != "$DUCK_IP" ]; then
    note "DuckDNS does not point here yet. Updating now via the token..."
    UPD="https://www.duckdns.org/update?domains=callbot&token=${DUCKDNS_TOKEN}&ip=${MY_IP}"
    RESP="$(curl -fsS --max-time 10 "$UPD" || true)"
    if [ "$RESP" = "OK" ]; then
        ok "DuckDNS updated -> $MY_IP. Wait ~60s for global propagation."
    else
        die "DuckDNS update failed: response was '$RESP' (check token)"
    fi
elif [ "$MY_IP" = "$DUCK_IP" ]; then
    ok "DuckDNS already points here"
else
    warn "DNS does not match this VPS and no DUCKDNS_TOKEN to auto-update"
    warn "  -> certbot will fail later unless you update DNS manually"
fi

# --- 4. DuckDNS auto-updater (only if token present) -----------------------

if [ -n "${DUCKDNS_TOKEN:-}" ]; then
    phase "4. DuckDNS auto-updater (cron every 5 min)"
    DUCK_DIR="$HOME/.duckdns"
    mkdir -p "$DUCK_DIR"
    cat > "$DUCK_DIR/update.sh" <<EOF
#!/usr/bin/env bash
curl -fsS --max-time 10 "https://www.duckdns.org/update?domains=callbot&token=${DUCKDNS_TOKEN}&ip=" >> "$DUCK_DIR/log" 2>&1
echo " \$(date -u +%FT%TZ)" >> "$DUCK_DIR/log"
EOF
    chmod 700 "$DUCK_DIR/update.sh"
    chmod 600 "$DUCK_DIR/log" 2>/dev/null || true
    ( crontab -l 2>/dev/null | grep -v 'duckdns/update.sh' ; echo "*/5 * * * * $DUCK_DIR/update.sh" ) | crontab -
    ok "cron entry installed (runs every 5 min)"
else
    phase "4. DuckDNS auto-updater (skipped — no DUCKDNS_TOKEN)"
fi

# --- 5. Swap (1 GB on a 916 MB box) ----------------------------------------

phase "5. Swap (1 GB)"
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

# --- 6. Python venv + deps --------------------------------------------------

phase "6. Python venv + dependencies"
cd "$APP_DIR"
if [ ! -d .venv ]; then
    python3.11 -m venv .venv
    ok "new .venv created at $APP_DIR/.venv"
else
    ok ".venv already exists"
fi
.venv/bin/python -m pip install -q --upgrade pip wheel setuptools
.venv/bin/python -m pip install -q -r requirements.txt
.venv/bin/python -m pip install -q -e ".[dev,web]"
ok "dependencies installed in .venv (server will run via .venv/bin/python)"

# --- 7. Seed test booking row ----------------------------------------------

phase "7. Seed test booking request"
"$APP_DIR/.venv/bin/python" "$APP_DIR/scripts/seed_test_request.py" || warn "seed failed (row may already exist — fine)"

# --- 8. systemd service (CallBot runs INSIDE the venv, cgroup-capped) ------

phase "8. systemd service for $SERVICE_NAME"
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

# --- Free-tier safety on t3.micro (2 vCPU, 916 MB RAM) ---
CPUQuota=160%
MemoryHigh=550M
MemoryMax=700M
TasksMax=200

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
ok "$SERVICE_NAME service enabled and running (cgroup limits enforced)"

# --- 9. nginx reverse proxy (HTTP; certbot will add 443 if LE_EMAIL set) ---

phase "9. nginx reverse proxy"
sudo tee /etc/nginx/conf.d/${SERVICE_NAME}.conf >/dev/null <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    # WebRTC media is P2P UDP and does NOT flow through nginx.
    # nginx only proxies the HTTPS signaling.

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
ok "nginx serving $DOMAIN on :80 -> 127.0.0.1:$APP_PORT"

# --- 10. Let's Encrypt TLS (only if LE_EMAIL set) --------------------------

if [ -n "${LE_EMAIL:-}" ]; then
    phase "10. Let's Encrypt TLS for $DOMAIN"
    note "Requires SG ports 80 and 443 open to 0.0.0.0/0"
    if sudo /usr/local/bin/certbot certificates 2>/dev/null | grep -q "$DOMAIN"; then
        ok "certificate for $DOMAIN already present"
    else
        sudo /usr/local/bin/certbot --nginx \
            --non-interactive \
            --agree-tos \
            -m "$LE_EMAIL" \
            -d "$DOMAIN" \
            --redirect
        ok "certificate issued; nginx now serves https://$DOMAIN/"
    fi
    sudo systemctl enable --now certbot-renew.timer 2>/dev/null || true
else
    phase "10. Let's Encrypt TLS (skipped — no LE_EMAIL)"
fi

# --- 11. Resource guardrail watchdog ---------------------------------------

phase "11. Resource guardrail watchdog"
if systemctl is-enabled guardrail.service >/dev/null 2>&1; then
    ok "guardrail already installed; restarting to pick up any changes"
    sudo systemctl restart guardrail.service
else
    sudo "$APP_DIR/scripts/guardrail.sh" install
    ok "guardrail installed"
fi

# --- 12. Done ---------------------------------------------------------------

phase "12. Done"
URL_SCHEME="http"
[ -n "${LE_EMAIL:-}" ] && URL_SCHEME="https"
echo
echo "  Service:      sudo systemctl status $SERVICE_NAME"
echo "  Live logs:    sudo journalctl -u $SERVICE_NAME -f"
echo "  App logs:     $APP_DIR/logs/voicestream.log"
echo "  Test URL:     ${URL_SCHEME}://$DOMAIN/"
echo "  Relay-only:   ${URL_SCHEME}://$DOMAIN/?relay   (forces TURN; strict pre-test)"
echo
echo "  Re-deploy after code change:"
echo "    cd $APP_DIR && git pull && sudo systemctl restart $SERVICE_NAME"
echo
echo "  Change config without redeploying:"
echo "    edit $APP_DIR/.env"
echo "    sudo systemctl restart $SERVICE_NAME"
echo
