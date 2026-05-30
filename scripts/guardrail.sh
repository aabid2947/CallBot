#!/usr/bin/env bash
#
# guardrail.sh — free-tier-safe resource watchdog for the CallBot VPS.
#
# Design (two layers, no overlap with systemd):
#
#   LAYER 1 — systemd cgroup limits on CallBot itself (set in callbot.service):
#     CPUQuota=160%      max 1.6 vCPUs (room for bursts, keeps 1 core for SSH/system)
#     MemoryHigh=550M    soft throttle starts here
#     MemoryMax=700M     hard kill if breached (kernel OOM, instant)
#     TasksMax=200       can't fork-bomb the box
#   These mean CallBot CAN NEVER burn the box: kernel + systemd enforce them
#   atomically. This script does not try to duplicate that work.
#
#   LAYER 2 — this script polices everything that is NOT a managed service:
#     interactive shells, test scripts, python REPLs, anything you launch
#     from ssh. These have no cgroup limits by default, and a runaway
#     `while True: pass` is what burns t3.micro CPU credits = $$.
#
# What this script will and will not touch:
#   - NEVER signals sshd, systemd, init, this script, root-owned procs,
#     or any process inside system.slice/*.service (= cgroup-bounded already).
#   - Strikes (counted per CHECK_INTERVAL) only against shell-spawned procs
#     owned by UID >= MIN_UID. Escalation: log -> renice +19 -> SIGTERM/SIGKILL.
#
# Extra: box-wide credit-burn watcher. If 1-minute load average stays above
# BURN_LOAD (default 1.5 on a 2-vCPU box ~= 75% combined), log a warning
# once per BURN_LOG_EVERY seconds. Informational only; t3.micro Standard mode
# accrues 6 credits/hour at idle, sustained 75% load burns ~12/hour net.
#
# Usage:
#   sudo ./guardrail.sh run        # foreground (Ctrl-C to stop)
#   sudo ./guardrail.sh install    # install + enable systemd service
#   sudo ./guardrail.sh status     # service status + last log lines
#   sudo ./guardrail.sh uninstall  # stop + remove the service
#

set -uo pipefail

###############################################################################
# Config
###############################################################################
CHECK_INTERVAL=5         # seconds between scans
CPU_LIMIT=60             # per-process CPU % (of one core) that counts as "too high"
MEM_LIMIT=30             # per-process memory % that counts as "too high"

WARN_AT=2                # strikes before renice +19
KILL_AT=8                # strikes before SIGTERM

MIN_UID=1000             # only police processes owned by UID >= this
MIN_FREE_MB=80           # emergency: kill biggest eligible proc if free<this

BURN_LOAD=1.5            # 1-min loadavg above which we log a credit warning
BURN_LOG_EVERY=60        # at most one credit warning per N seconds

LOG_FILE=/var/log/guardrail.log

PROTECT_NAMES=" sshd systemd systemd-journal systemd-logind systemd-network \
systemd-resolve systemd-udevd systemd-homed systemd-userwor chronyd dbus-broker \
amazon-ssm-agen auditd agetty getty login init kthreadd guardrail.sh "

###############################################################################
# Internals
###############################################################################
SELF_PID=$$
SCRIPT_PATH="$(readlink -f "$0")"
INSTALL_PATH=/usr/local/bin/guardrail.sh
UNIT_PATH=/etc/systemd/system/guardrail.service

declare -A STRIKES NAME
LAST_BURN_LOG=0

log() { echo "$(date '+%F %T') $*" | tee -a "$LOG_FILE" >&2; }

need_root() {
  if [[ $EUID -ne 0 ]]; then
    echo "Must run as root.  Use: sudo $0 $*" >&2; exit 1
  fi
}

# Return 0 (= protected, skip) for any process this script must not touch.
is_protected() {
  local pid=$1 comm uid cg
  [[ "$pid" == "$SELF_PID" || "$pid" == "1" ]] && return 0
  comm=$(cat "/proc/$pid/comm" 2>/dev/null) || return 0          # gone -> treat as protected
  [[ "$PROTECT_NAMES" == *" $comm "* ]] && return 0
  uid=$(stat -c %u "/proc/$pid" 2>/dev/null) || return 0
  (( uid < MIN_UID )) && return 0

  # Processes inside a systemd-managed .service have their own cgroup
  # CPU/Memory limits. Don't double-police them.
  cg=$(cat "/proc/$pid/cgroup" 2>/dev/null) || return 0
  [[ "$cg" == *"/system.slice/"*".service"* ]] && return 0

  return 1
}

# "pid cpu mem" per process, from the SECOND top sample (= instantaneous accurate).
sample() {
  top -bn2 -d 1 -w 512 2>/dev/null | awk '
    /^[[:space:]]*PID[[:space:]]+USER/ { blk++; next }
    blk==2 && NF>=10 { print $1, $9, $10 }
  '
}

free_mb()    { awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo; }
load_1min()  { awk '{print $1}' /proc/loadavg; }

kill_proc() {
  local pid=$1 why=$2
  log "KILL    pid=$pid (${NAME[$pid]:-?}) $why -> SIGTERM"
  kill -TERM "$pid" 2>/dev/null
  sleep 2
  if kill -0 "$pid" 2>/dev/null; then
    log "KILL    pid=$pid still alive -> SIGKILL"
    kill -KILL "$pid" 2>/dev/null
  fi
  unset 'STRIKES[$pid]' 'NAME[$pid]'
}

emergency_oom() {
  local avail; avail=$(free_mb)
  (( avail >= MIN_FREE_MB )) && return
  local worst=0 worst_pid="" pid cpu mem m
  while read -r pid cpu mem; do
    is_protected "$pid" && continue
    m=${mem%.*}; [[ -z "$m" ]] && m=0
    (( m > worst )) && { worst=$m; worst_pid=$pid; }
  done < <(sample)
  if [[ -n "$worst_pid" ]]; then
    NAME[$worst_pid]=$(cat "/proc/$worst_pid/comm" 2>/dev/null)
    log "OOM     free=${avail}MB < ${MIN_FREE_MB}MB"
    kill_proc "$worst_pid" "low-memory"
  fi
}

# Box-wide credit-burn warning. Doesn't kill anything; just visibility.
burn_check() {
  local load now; load=$(load_1min); now=$(date +%s)
  awk -v l="$load" -v t="$BURN_LOAD" 'BEGIN{exit !(l>t)}' || return
  (( now - LAST_BURN_LOG < BURN_LOG_EVERY )) && return
  LAST_BURN_LOG=$now
  log "BURN    loadavg-1m=$load > $BURN_LOAD (t3.micro: sustained > ~20% combined drains CPU credits)"
}

run_loop() {
  need_root run
  log "guardrail START  per-proc cpu>${CPU_LIMIT}%  mem>${MEM_LIMIT}%  warn@${WARN_AT} kill@${KILL_AT}  min_uid=${MIN_UID}  burn>${BURN_LOAD}"
  trap 'log "guardrail STOP"; exit 0' INT TERM

  while true; do
    emergency_oom
    burn_check

    declare -A SEEN=()
    while read -r pid cpu mem; do
      [[ "$pid" =~ ^[0-9]+$ ]] || continue
      is_protected "$pid" && continue
      local ci=${cpu%.*} mi=${mem%.*}
      [[ -z "$ci" ]] && ci=0; [[ -z "$mi" ]] && mi=0

      if (( ci > CPU_LIMIT || mi > MEM_LIMIT )); then
        SEEN[$pid]=1
        NAME[$pid]=$(cat "/proc/$pid/comm" 2>/dev/null)
        STRIKES[$pid]=$(( ${STRIKES[$pid]:-0} + 1 ))
        local s=${STRIKES[$pid]}
        if   (( s >= KILL_AT )); then
          kill_proc "$pid" "cpu=${ci}% mem=${mi}% strikes=$s"
        elif (( s == WARN_AT )); then
          renice 19 -p "$pid" >/dev/null 2>&1
          log "RENICE  pid=$pid (${NAME[$pid]:-?}) cpu=${ci}% mem=${mi}% -> nice +19"
        else
          log "WATCH   pid=$pid (${NAME[$pid]:-?}) cpu=${ci}% mem=${mi}% strike=$s"
        fi
      fi
    done < <(sample)

    # Decay or release procs that behaved (or died) this round.
    for pid in "${!STRIKES[@]}"; do
      [[ -n "${SEEN[$pid]:-}" ]] && continue
      if ! kill -0 "$pid" 2>/dev/null; then
        unset 'STRIKES[$pid]' 'NAME[$pid]'; continue
      fi
      STRIKES[$pid]=$(( STRIKES[$pid] - 1 ))
      if (( STRIKES[$pid] <= 0 )); then
        renice 0 -p "$pid" >/dev/null 2>&1
        unset 'STRIKES[$pid]' 'NAME[$pid]'
      fi
    done
    unset SEEN

    sleep "$CHECK_INTERVAL"
  done
}

###############################################################################
# install / uninstall / status
###############################################################################
install_svc() {
  need_root install
  install -m 0755 "$SCRIPT_PATH" "$INSTALL_PATH"
  cat > "$UNIT_PATH" <<EOF
[Unit]
Description=Resource Guardrail Watchdog (free-tier CPU/mem protection)
After=multi-user.target

[Service]
Type=simple
ExecStart=$INSTALL_PATH run
Restart=always
RestartSec=5
# The watchdog must never itself burn credits.
Nice=-5
CPUQuota=15%
MemoryMax=96M

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now guardrail.service
  echo "Installed and started. Auto-starts on every boot."
  echo "Live log: sudo journalctl -u guardrail -f   (or tail $LOG_FILE)"
}

uninstall_svc() {
  need_root uninstall
  systemctl disable --now guardrail.service 2>/dev/null
  rm -f "$UNIT_PATH" "$INSTALL_PATH"
  systemctl daemon-reload
  echo "Removed."
}

status_svc() {
  systemctl status guardrail.service --no-pager 2>/dev/null
  echo "----- recent log -----"
  tail -n 30 "$LOG_FILE" 2>/dev/null || echo "(no log yet)"
}

case "${1:-run}" in
  run)       run_loop ;;
  install)   install_svc ;;
  uninstall) uninstall_svc ;;
  status)    status_svc ;;
  *) echo "Usage: sudo $0 {run|install|status|uninstall}"; exit 1 ;;
esac
