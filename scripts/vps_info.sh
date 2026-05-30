#!/usr/bin/env bash
# Run this on the VPS BEFORE we write the deploy script.
# Usage:
#   ssh user@103.134.102.38
#   curl -fsSL https://raw.githubusercontent.com/aabid2947/CallBot/main/scripts/vps_info.sh | bash
# OR after `git clone`:
#   bash scripts/vps_info.sh
#
# Output is plain text; copy the WHOLE output back into the chat.
# No secrets are dumped; only system facts the deploy script needs.

set -u

print_section() {
    echo
    echo "================================================================"
    echo "  $1"
    echo "================================================================"
}

has() { command -v "$1" >/dev/null 2>&1; }
ver() { "$@" 2>&1 | head -1 || echo "(not installed)"; }

print_section "1. OS / kernel / arch"
uname -a 2>&1
if [ -r /etc/os-release ]; then
    grep -E '^(NAME|VERSION|PRETTY_NAME)=' /etc/os-release
fi
echo "uptime: $(uptime -p 2>/dev/null || uptime)"

print_section "2. Hardware"
echo "CPU:    $(nproc) cores | $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | sed 's/^ //')"
echo "Memory:"
free -h 2>&1 | head -3
echo
echo "Disk:"
df -h / /home /tmp /var 2>/dev/null | grep -v 'No such'

print_section "3. Network identity"
echo "Hostname:       $(hostname)"
echo "Public IPv4:    $(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo '(curl failed)')"
echo "Public IPv6:    $(curl -fsS --max-time 5 https://api6.ipify.org 2>/dev/null || echo '(no IPv6)')"
echo "Local IPs:"
ip -4 -o addr show 2>/dev/null | awk '{print "  " $2 " " $4}' || ifconfig 2>/dev/null | grep -E 'inet '
echo
echo "DNS check for callbot.duckdns.org:"
host callbot.duckdns.org 2>/dev/null || dig +short callbot.duckdns.org 2>/dev/null || getent hosts callbot.duckdns.org

print_section "4. User / permissions"
echo "Whoami:    $(whoami)"
echo "UID/GID:   $(id 2>&1)"
echo "Home:      $HOME"
echo "Sudo:      $(sudo -n true 2>&1 && echo 'passwordless sudo available' || echo 'sudo needs password or unavailable')"
echo "PWD:       $(pwd)"

print_section "5. Toolchain present?"
for cmd in python3 python pip pip3 git curl wget docker docker-compose systemctl nginx caddy certbot ufw iptables firewall-cmd ngrok; do
    if has "$cmd"; then
        printf "  %-16s %s\n" "$cmd" "$($cmd --version 2>&1 | head -1)"
    else
        printf "  %-16s MISSING\n" "$cmd"
    fi
done

print_section "6. Python details"
if has python3; then
    python3 -c "import sys; print('Executable:', sys.executable); print('Version:   ', sys.version)" 2>&1
    python3 -m venv --help >/dev/null 2>&1 && echo "venv module: OK" || echo "venv module: MISSING (apt install python3-venv)"
    python3 -m pip --version 2>&1 || echo "pip: MISSING (apt install python3-pip)"
fi

print_section "7. Ports already listening (root view if available)"
if has ss; then
    sudo -n ss -tulnp 2>/dev/null || ss -tuln 2>&1 | head -30
else
    sudo -n netstat -tulnp 2>/dev/null || netstat -tuln 2>&1 | head -30
fi

print_section "8. Firewall state"
if has ufw; then
    echo "--- ufw status ---"
    sudo -n ufw status verbose 2>&1 | head -20 || echo "(needs sudo)"
fi
if has firewall-cmd; then
    echo "--- firewalld zones ---"
    sudo -n firewall-cmd --list-all 2>&1 | head -20 || echo "(needs sudo)"
fi
if has iptables; then
    echo "--- iptables INPUT rules (first 20) ---"
    sudo -n iptables -L INPUT -n --line-numbers 2>&1 | head -20 || echo "(needs sudo)"
fi

print_section "9. systemd / init"
if has systemctl; then
    systemctl --version | head -1
    echo "  Booted with systemd: yes"
    echo "  Failed units (if any):"
    systemctl --failed --no-pager --no-legend 2>&1 | head -5
else
    echo "  systemd not detected"
fi

print_section "10. Existing web servers / TLS"
for svc in nginx caddy apache2 httpd; do
    if has "$svc"; then
        echo "  $svc: installed"
        sudo -n systemctl is-active "$svc" 2>&1 | head -1
    fi
done
if has certbot; then
    echo "  certbot: $(certbot --version 2>&1 | head -1)"
    echo "  certificates:"
    sudo -n certbot certificates 2>&1 | grep -E 'Certificate Name|Domains|Expiry' | head -20 || echo "  (needs sudo or no certs)"
fi

print_section "11. Reachability to upstreams (5s timeouts)"
for url in https://api.groq.com/openai/v1/models https://api.deepgram.com/v1/projects https://www.duckdns.org/ https://api.metered.ca/ https://openrelay.metered.ca/ https://github.com/; do
    code=$(curl -o /dev/null -s -w "%{http_code}" --max-time 5 "$url" || echo "TIMEOUT")
    printf "  %-45s %s\n" "$url" "$code"
done
echo
echo "TURN UDP reachability (openrelay 80/443):"
for port in 80 443; do
    if has nc; then
        timeout 3 nc -vz -u openrelay.metered.ca "$port" 2>&1 | tail -1
    fi
done

print_section "12. Project directory / existing deploy"
echo "Looking for an existing VoiceStream checkout..."
for dir in /opt/voicestream /opt/callbot /srv/voicestream /srv/callbot ~/voicestream ~/callbot ~/CallBot; do
    if [ -d "$dir" ]; then
        echo "  FOUND: $dir"
        echo "    contents (depth 1):"
        ls -la "$dir" 2>&1 | head -10 | sed 's/^/      /'
    fi
done

print_section "13. Time / NTP"
date -u
timedatectl 2>&1 | head -10 || echo "(timedatectl unavailable)"

print_section "14. Resource pressure right now"
echo "Load average: $(cat /proc/loadavg 2>/dev/null)"
echo "Top 5 processes by RSS:"
ps -eo pid,user,rss,comm --sort=-rss 2>&1 | head -6

print_section "DONE"
echo
echo "Copy EVERYTHING above this line back into the chat."
echo "Do NOT paste secrets — this script never prints any."
