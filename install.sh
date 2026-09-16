#!/usr/bin/env bash
# Build first, switch second. Runtime configuration never enters the checkout.
set -Eeuo pipefail
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MODE=${1:-install}
[[ $EUID -eq 0 ]] || { echo 'Run the installer with sudo.' >&2; exit 1; }
[[ $(ps -p 1 -o comm= | tr -d '[:space:]') == systemd ]] || {
  echo 'Install on the Linux host running systemd, not inside a container.' >&2; exit 1;
}
for tool in python3 systemctl getent useradd install runuser tar; do
  command -v "$tool" >/dev/null || { echo "Required tool missing: $tool" >&2; exit 1; }
done
python3 - <<'PY'
import sys, sqlite3, venv
from zoneinfo import ZoneInfo
assert sys.version_info >= (3,10), 'Python 3.10 or newer is required.'
ZoneInfo('Europe/Warsaw')
PY
[[ -f "$SOURCE/pyproject.toml" && -f "$SOURCE/src/daybreak/engine.py" ]] || { echo 'Incomplete Daybreak checkout.' >&2; exit 1; }
[[ $MODE == --check ]] && { echo 'Host prerequisites passed.'; exit 0; }
[[ $MODE == install || $MODE == --console-update ]] || { echo 'Unknown install option.' >&2; exit 1; }

RELEASE="/opt/daybreak/releases/$(date -u +%Y%m%d-%H%M%S)-$$"
BACKUP="/var/backups/daybreak/$(date -u +%Y%m%d-%H%M%S)-$$"
OLD_TARGET=$(readlink /opt/daybreak/current 2>/dev/null || true)
WAS_ACTIVE=0
systemctl is-active --quiet daybreak && WAS_ACTIVE=1
ACTIVATED=0
DONE=0
cleanup() {
  rc=$?
  if [[ $rc -ne 0 && $ACTIVATED -eq 1 ]]; then
    echo 'Activation failed; restoring the previous service files.' >&2
    systemctl stop daybreak 2>/dev/null || true
    if [[ -n "$OLD_TARGET" ]]; then
      ln -sfn "$OLD_TARGET" /opt/daybreak/current.rollback
      mv -Tf /opt/daybreak/current.rollback /opt/daybreak/current
    fi
    [[ ! -f "$BACKUP/daybreak.service" ]] || cp -a "$BACKUP/daybreak.service" /etc/systemd/system/daybreak.service
    [[ ! -f "$BACKUP/daybreak-launcher" ]] || cp -a "$BACKUP/daybreak-launcher" /usr/local/bin/daybreak
    systemctl daemon-reload || true
    [[ $WAS_ACTIVE -eq 0 ]] || systemctl start daybreak || true
  fi
  if [[ $rc -ne 0 && $ACTIVATED -eq 0 && -d "$RELEASE" ]]; then rm -rf -- "$RELEASE"; fi
  exit "$rc"
}
trap cleanup EXIT

install -d -m 755 /opt/daybreak/releases
install -d -m 755 "$RELEASE/source"
cp -a "$SOURCE/src" "$SOURCE/pyproject.toml" "$RELEASE/source/"
# Include project metadata only, never .git, .env, credentials or runtime databases.
for f in README.md LICENSE; do [[ ! -f "$SOURCE/$f" ]] || cp "$SOURCE/$f" "$RELEASE/source/$f"; done
printf 'Building Daybreak in %s\n' "$RELEASE"
python3 -m venv "$RELEASE/venv" || { echo 'Python venv is missing. Install python3-venv on Debian/Ubuntu.' >&2; exit 1; }
"$RELEASE/venv/bin/python" -m pip install --disable-pip-version-check "$RELEASE/source"
"$RELEASE/venv/bin/python" -m pip check
"$RELEASE/venv/bin/python" - <<'PY'
from datetime import date
from daybreak.model import Settings, plan_for
from daybreak.tui import Console, DemoControl
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput
p=plan_for(Settings(),date(2026,9,17),{'date':'2026-09-17','start':'07:10'})
assert p.start.strftime('%H:%M')=='07:10' and p.end.strftime('%H:%M')=='08:30'
assert len(Console(DemoControl(),input=DummyInput(),output=DummyOutput(),demo=True).views)==9
print('Package import, schedule and console composition checks passed.')
PY

if ! getent passwd daybreak >/dev/null; then
  useradd --system --user-group --home-dir /var/lib/daybreak --shell /usr/sbin/nologin --comment 'Daybreak wake-up service' daybreak
fi
getent group daybreak >/dev/null || { echo 'The existing daybreak user has no daybreak group.' >&2; exit 1; }
install -d -m 700 -o daybreak -g daybreak /var/lib/daybreak
install -d -m 750 -o root -g daybreak /etc/daybreak
install -d -m 700 "$BACKUP"
[[ ! -f /etc/systemd/system/daybreak.service ]] || cp -a /etc/systemd/system/daybreak.service "$BACKUP/daybreak.service"
[[ ! -e /usr/local/bin/daybreak ]] || cp -a /usr/local/bin/daybreak "$BACKUP/daybreak-launcher"
# Old single-file Daybreak configuration remains untouched. The new app uses its
# own settings.json, credentials and daybreak.sqlite3 in the state directory.
systemctl stop daybreak 2>/dev/null || true
ACTIVATED=1
ln -sfn "$RELEASE" /opt/daybreak/current.next
mv -Tf /opt/daybreak/current.next /opt/daybreak/current
install -m 644 "$SOURCE/packaging/daybreak.service" /etc/systemd/system/daybreak.service
cat > /usr/local/bin/daybreak <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exec /opt/daybreak/current/venv/bin/daybreak "$@"
EOF
chmod 755 /usr/local/bin/daybreak
printf '%s\n' "$SOURCE" > /etc/daybreak/source-path
chmod 640 /etc/daybreak/source-path
chown root:daybreak /etc/daybreak/source-path
systemctl daemon-reload
systemctl start daybreak
for _ in $(seq 1 40); do
  [[ ! -S /run/daybreak/control.sock ]] || break
  sleep .25
done
systemctl is-active --quiet daybreak
[[ -S /run/daybreak/control.sock ]] || { echo 'Control socket did not become ready.' >&2; exit 1; }
DONE=1
printf '\nInstalled. Open: sudo daybreak\n'
printf 'First installation starts in setup mode: the daily routine is not armed.\n'
printf 'Complete Connection, Home Wi-Fi and the phone test in the console.\n'
printf 'Use System to enable the daily routine and autostart.\n'
