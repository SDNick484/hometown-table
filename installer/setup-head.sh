#!/bin/sh
# Hometown Table installer for Alpine Linux (OpenRC), next to the other apps on CT 210. Build with installer/build.sh.
# Re-runnable: replaces /opt/hometown each time, never touches existing data or settings, and only *adds* missing
# settings to the config file.
#
#   sh setup-hometown.sh               install / upgrade
#   sh setup-hometown.sh --uninstall   remove app, services and cron jobs (keeps data, config and backups)
set -eu
APP_DIR=/opt/hometown
MCP_DIR=/opt/hometown-mcp
MCP_VENV=$MCP_DIR/venv
DATA_DIR=/var/lib/hometown
CONF=/etc/conf.d/hometown
OPS_CONF="${OPS_CONF:-/etc/conf.d/ct210-ops}"
SVC_USER=hometown
MCP_USER=hometown-mcp
CRONS="daily/hometown-backup daily/hometown-tidy daily/hometown-sync weekly/hometown-scan"

if [ "$(id -u)" != 0 ]; then echo "Run as root"; exit 1; fi
command -v apk >/dev/null || { echo "This installer is for Alpine (apk)."; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
  for s in hometown-mcp hometown; do
    rc-service "$s" stop 2>/dev/null || true
    rc-update del "$s" default 2>/dev/null || true
    rm -f "/etc/init.d/$s"
  done
  for c in $CRONS; do rm -f "/etc/periodic/$c"; done
  rm -rf "$APP_DIR" "$MCP_DIR"
  echo "Removed app, services and cron jobs. Data kept in $DATA_DIR, config in $CONF, backups in /var/backups/ct210."
  echo "The Hometown lines in HEALTH_CHECKS ($OPS_CONF) are left for you to remove."
  exit 0
fi

# an existing install keeps its ports
conf_val() { sed -n "s/^$1=//p" "$CONF" 2>/dev/null | head -n1 | tr -d "\"'"; }
PORT=$(conf_val HOMETOWN_PORT); PORT=${PORT:-8782}
MCP_PORT=$(conf_val MCP_HTTP_PORT); MCP_PORT=${MCP_PORT:-8783}

echo "==> Installing packages"
# tzdata: the app's calendar is America/Los_Angeles and a bare Alpine has no zone files. sqlite: the backup.
apk add --no-cache python3 py3-flask py3-gunicorn py3-jwt py3-cryptography sqlite tzdata py3-virtualenv >/dev/null

[ -x /etc/init.d/hometown-mcp ] && rc-service hometown-mcp stop >/dev/null 2>&1 || true
[ -x /etc/init.d/hometown ] && rc-service hometown stop >/dev/null 2>&1 || true
for p in "$PORT" "$MCP_PORT"; do
  if netstat -ltn 2>/dev/null | awk '{print $4}' | grep -q ":$p\$"; then
    echo "Port $p is already in use by something else. Free it, or change HOMETOWN_PORT / MCP_HTTP_PORT in $CONF and re-run."
    exit 1
  fi
done
IP=$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -n1)
IP=${IP:-127.0.0.1}

echo "==> Creating users and folders"
# BusyBox adduser -S puts a new user in "nogroup" unless told otherwise, so make each one's own group first.
grep -q "^$SVC_USER:" /etc/group || addgroup -S "$SVC_USER"
if ! id "$SVC_USER" >/dev/null 2>&1; then
  adduser -S -D -H -h "$DATA_DIR" -s /sbin/nologin -G "$SVC_USER" "$SVC_USER"
fi
# The MCP service gets its own user: no database access, no home.
grep -q "^$MCP_USER:" /etc/group || addgroup -S "$MCP_USER"
id "$MCP_USER" >/dev/null 2>&1 || adduser -S -D -H -h /nonexistent -s /sbin/nologin -G "$MCP_USER" "$MCP_USER"
mkdir -p "$APP_DIR" "$DATA_DIR" /var/backups/ct210

echo "==> Unpacking app"
TMP=$(mktemp -d)
sed -n '/^__PAYLOAD_BELOW__$/,$p' "$0" | tail -n +2 | base64 -d | ( cd "$TMP" && tar -xzf - )
rm -rf "$APP_DIR.new" && mv "$TMP/hometown" "$APP_DIR.new"
rm -rf "$APP_DIR.old"; [ -d "$APP_DIR" ] && mv "$APP_DIR" "$APP_DIR.old"
mv "$APP_DIR.new" "$APP_DIR"; rm -rf "$APP_DIR.old" "$TMP"
chown -R root:root "$APP_DIR"
chown -R "$SVC_USER:$SVC_USER" "$DATA_DIR"
chmod 750 "$DATA_DIR"

# ---------------------------------------------------------------- config
add_setting() {  # add_setting FILE KEY VALUE COMMENT: only if KEY isn't there yet; the value goes to the file only
  grep -q "^$2=" "$1" 2>/dev/null && return 0
  printf '\n# %s\n%s="%s"\n' "$4" "$2" "$3" >> "$1"
  ADDED="${ADDED:-} $2"
}
genkey() { head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

if [ ! -f "$CONF" ]; then
  echo "==> Writing $CONF"
  : > "$CONF"
  printf '%s\n' "# Hometown Table configuration. After editing: rc-service hometown restart; rc-service hometown-mcp restart" > "$CONF"
fi
chmod 600 "$CONF"; chown root:root "$CONF"   # it holds the keys; OpenRC and cron run as root
add_setting "$CONF" DATA_DIR "$DATA_DIR" "Where hometown.db lives"
add_setting "$CONF" HOMETOWN_PORT "$PORT" "Port the app listens on"
add_setting "$CONF" HOMETOWN_BIND "0.0.0.0" "Address gunicorn binds (0.0.0.0 so the tunnel on another host can reach it)"
add_setting "$CONF" APP_NAME "Hometown Table" "Page title and home-screen name; keep it neutral, never a person's name"
add_setting "$CONF" TOWNS "" "The towns: name|lat|lng or name|lat|lng|radius_km separated by ; (empty: the eight default towns)"
add_setting "$CONF" TOWN_RADIUS_KM "" "Each town's scan box is its centre +- this many km (empty: 4)"
add_setting "$CONF" REST_DAYS "" "Not lately means nobody has been for more than this many days (empty: 45)"
add_setting "$CONF" CF_TEAM_DOMAIN "" "Cloudflare Access team domain, e.g. yourteam.cloudflareaccess.com"
add_setting "$CONF" CF_AUD "" "AUD tag of the Hometown Table Access application"
add_setting "$CONF" ADMIN_EMAILS "" "Editor emails, comma separated; empty refuses everyone"
add_setting "$CONF" LAN_EDIT 0 "1 = direct LAN requests (not through Cloudflare) are editor lan; recovery only"
add_setting "$CONF" HOMETOWN_READ_KEY "$(genkey)" "Bearer key for the MCP service on loopback: read only (generated by the installer)"
add_setting "$CONF" HOMETOWN_API_KEY "$(genkey)" "Bearer key for the MCP service on loopback: read and write (generated by the installer)"
add_setting "$CONF" GOOGLE_PLACES_KEY "" "Google Places API (New) key; empty turns scanning and list matching off"
add_setting "$CONF" GOOGLE_MAX_PRO_PER_MONTH "" "Hard monthly cap on Place Details (Pro) calls (empty: 3000)"
add_setting "$CONF" GOOGLE_PREFILL "" "1 = the accept form offers Use this for a live Google value (empty: 1)"
add_setting "$CONF" SCAN_TILE_KM "" "Scan tile size in km (empty: 1.5)"
add_setting "$CONF" SCAN_MIN_TILE_KM "" "Smallest tile a full tile is split down to, in km (empty: 0.25)"
add_setting "$CONF" SCAN_TYPES "" "Google place types the scan searches, comma separated (empty: restaurant)"
add_setting "$CONF" CLOSURE_CHECK_DAYS "" "Re-check a place's Google business status after this many days (empty: 28)"
# Synced visits (daily): a source with no URL or no token is off
add_setting "$CONF" DINNER_LOG_URL "" "Dinner Log base URL, e.g. https://dinner.example.com (empty: no visits synced from it)"
add_setting "$CONF" DINNER_LOG_TOKEN "" "Bearer token of Dinner Log's visit feed"
add_setting "$CONF" WTE_URL "" "Where to Eat base URL (empty: no visits synced from it)"
add_setting "$CONF" WTE_TOKEN "" "Bearer token of Where to Eat's visit feed"
# The MCP service (hometown-mcp)
add_setting "$CONF" HOMETOWN_MCP_WRITES none "MCP write level: none, log (log visits, star places) or all"
add_setting "$CONF" MCP_HTTP_PORT "$MCP_PORT" "Port the MCP service listens on"
add_setting "$CONF" MCP_HTTP_PATH /hometown/mcp "URL path of the MCP endpoint (the tunnel route)"
add_setting "$CONF" MCP_PUBLIC_HOSTS "" "Public hostname(s) of the MCP tunnel route, comma separated"
add_setting "$CONF" CF_ACCESS_TEAM_DOMAIN "" "Access team domain for the MCP's Access application (managed OAuth)"
add_setting "$CONF" CF_ACCESS_AUD "" "AUD tag of the MCP's Access application"
add_setting "$CONF" MCP_ALLOWED_EMAILS "" "Emails allowed to use the MCP, comma separated"
add_setting "$CONF" HOMETOWN_URL "http://127.0.0.1:$PORT" "Where the MCP service reaches the app"

# health checks: add Hometown Table and Hometown MCP to HEALTH_CHECKS if the ops config exists
# --- add_health_check begin
add_health_check() {  # add_health_check "Name|url|service" service  (sets HC_ADDED / HC_MANUAL)
  [ -f "$OPS_CONF" ] || return 0
  # already there; a longer service name that merely starts with this one (hometown-mcp) doesn't count
  grep -Eq "\|$2([^-A-Za-z0-9_]|\$)" "$OPS_CONF" && return 0
  if ! grep -q '^HEALTH_CHECKS=' "$OPS_CONF"; then
    cp "$OPS_CONF" "$OPS_CONF.bak"
    cat >> "$OPS_CONF" <<EOF

# Apps checked every 15 min (name|url|service); added by setup-hometown.sh
HEALTH_CHECKS="$1"
EOF
    HC_ADDED="${HC_ADDED:-} $2"
    return 0
  fi
  # append inside the existing (usually multi-line) HEALTH_CHECKS="..." value, before its closing quote
  cp "$OPS_CONF" "$OPS_CONF.bak"
  awk -v add="$1" '
    !done && /^HEALTH_CHECKS="/ { inb = 1; if ($0 == "HEALTH_CHECKS=\"") { print; next } }
    inb && /"[ \t]*$/ { sub(/"[ \t]*$/, ""); print; print add "\""; inb = 0; done = 1; next }
    { print }' "$OPS_CONF.bak" > "$OPS_CONF.new"
  if grep -Eq "\|$2([^-A-Za-z0-9_]|\$)" "$OPS_CONF.new" && sh -n "$OPS_CONF.new" 2>/dev/null; then
    cat "$OPS_CONF.new" > "$OPS_CONF"; HC_ADDED="${HC_ADDED:-} $2"
  else
    HC_MANUAL="${HC_MANUAL:-}
      $1"
  fi
  rm -f "$OPS_CONF.new"
}
# --- add_health_check end
add_health_check "Hometown Table|http://127.0.0.1:$PORT/healthz|hometown" hometown

# ---------------------------------------------------------------- services
install -m 755 "$APP_DIR/installer/init-hometown" /etc/init.d/hometown
install -m 755 "$APP_DIR/installer/init-hometown-mcp" /etc/init.d/hometown-mcp

# The MCP stack is not packaged for Alpine, so this one service gets a pip venv. A pip or network failure only skips
# hometown-mcp; the app still installs and starts.
echo "==> Installing the MCP venv"
mkdir -p "$MCP_DIR"
{ [ -x "$MCP_VENV/bin/python" ] && "$MCP_VENV/bin/python" -c pass 2>/dev/null; } || { rm -rf "$MCP_VENV"; python3 -m virtualenv -q "$MCP_VENV"; }
if "$MCP_VENV/bin/pip" install --no-cache-dir -q -r "$APP_DIR/requirements-mcp.txt"; then
  MCP_OK=1
else
  MCP_OK=0
  echo "    WARNING: could not set up the MCP venv (needs PyPI access); hometown-mcp will not start. Re-run to retry."
fi

# ---------------------------------------------------------------- cron
echo "==> Installing cron jobs"
mkdir -p /etc/periodic/daily /etc/periodic/weekly
for c in $CRONS; do
  job=${c#*/}
  # each job is installed only if its file is in this build (the tidy and scan jobs arrive with later tasks)
  if [ -f "$APP_DIR/ops/$job" ]; then install -m 755 "$APP_DIR/ops/$job" "/etc/periodic/$c"; else rm -f "/etc/periodic/$c"; fi
done
rc-update add crond default >/dev/null 2>&1 || true
rc-service crond start >/dev/null 2>&1 || true

echo "==> Starting service"
rc-update add hometown default >/dev/null 2>&1 || true
rc-service hometown start
i=0
until wget -qO- "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; do
  i=$((i+1)); [ $i -gt 25 ] && { echo "Service didn't come up; see /var/log/hometown.log"; tail -n 20 /var/log/hometown.log; exit 1; }
  sleep 1
done

echo "==> Taking a first backup"
[ -f /etc/periodic/daily/hometown-backup ] && { /etc/periodic/daily/hometown-backup || echo "    (backup reported a problem; see /var/log/ct210-ops.log)"; }

MCP_STATE=off
if [ "$MCP_OK" = 1 ] && [ -f "$APP_DIR/mcp_http.py" ] && [ -n "$(conf_val CF_ACCESS_AUD)" ] && [ -n "$(conf_val MCP_PUBLIC_HOSTS)" ]; then
  echo "==> Starting hometown-mcp"
  rc-service hometown-mcp start || true
  MCP_STATE=failed
  i=0
  while [ $i -lt 20 ]; do
    wget -qO- "http://127.0.0.1:$MCP_PORT/healthz" >/dev/null 2>&1 && { MCP_STATE=up; break; }
    i=$((i+1)); sleep 1
  done
  [ "$MCP_STATE" = up ] || { echo "    hometown-mcp didn't answer; see /var/log/hometown-mcp.log"; tail -n 10 /var/log/hometown-mcp.log 2>/dev/null || true; }
fi
# Only a service that is answering goes into the runlevel and the 15-minute health checks; an unconfigured one would
# otherwise be flagged (and respawned at boot) for nothing.
if [ "$MCP_STATE" = up ]; then
  rc-update add hometown-mcp default >/dev/null 2>&1 || true
  add_health_check "Hometown MCP|http://127.0.0.1:$MCP_PORT/healthz|hometown-mcp" hometown-mcp
fi

echo
echo "Hometown Table is running:  http://$IP:$PORT/"
echo "Health: $(wget -qO- "http://127.0.0.1:$PORT/healthz" | tr -d '\n')"
[ -n "${ADDED:-}" ] && echo "Settings added to $CONF:${ADDED} (the two bearer keys were generated and are not shown)"
[ -n "${HC_ADDED:-}" ] && echo "  - Added to HEALTH_CHECKS in $OPS_CONF:${HC_ADDED} (backup in $OPS_CONF.bak)"
[ -n "${HC_MANUAL:-}" ] && echo "  - Couldn't edit HEALTH_CHECKS in $OPS_CONF: add these lines:${HC_MANUAL}"
[ -f "$OPS_CONF" ] || echo "  - No $OPS_CONF: backups still run nightly into /var/backups/ct210"
echo "Next steps (vim $CONF, then rc-service hometown restart):"
echo "  - Cloudflare Tunnel: a public hostname for the app -> http://$IP:$PORT, and a route for the MCP path"
echo "  - Access: one application for the app hostname (policy: the two editors), one for the MCP path (managed OAuth)"
[ -n "$(conf_val CF_TEAM_DOMAIN)" ] || echo "  - Set CF_TEAM_DOMAIN in $CONF"
[ -n "$(conf_val CF_AUD)" ] || echo "  - Set CF_AUD in $CONF (the app's Access application)"
[ -n "$(conf_val ADMIN_EMAILS)" ] || echo "  - Set ADMIN_EMAILS in $CONF: until then nobody can sign in"
[ -n "$(conf_val GOOGLE_PLACES_KEY)" ] || echo "  - Set GOOGLE_PLACES_KEY in $CONF (Places API (New) key, restricted to that API): scanning is off until then"
case "$MCP_STATE" in
  up) echo "Hometown MCP is running (write level: $(conf_val HOMETOWN_MCP_WRITES))" ;;
  failed) echo "Hometown MCP did not come up: see /var/log/hometown-mcp.log" ;;
  *)
    echo "Hometown MCP was NOT started:"
    [ "$MCP_OK" = 1 ] || echo "  - The MCP venv could not be built (see the warning above); re-run when PyPI is reachable"
    [ -f "$APP_DIR/mcp_http.py" ] || echo "  - This build has no mcp_http.py yet"
    for k in MCP_PUBLIC_HOSTS CF_ACCESS_AUD; do
      [ -n "$(conf_val $k)" ] || echo "  - Set $k in $CONF"
    done
    echo "  - Then re-run this installer: it starts hometown-mcp and adds it to HEALTH_CHECKS"
    ;;
esac
echo "  - In claude.ai add the connector, and in Settings link each editor's email to a person"
echo "Config: $CONF   Data: $DATA_DIR   Logs: /var/log/hometown.log, /var/log/hometown-mcp.log, /var/log/ct210-ops.log"
exit 0
