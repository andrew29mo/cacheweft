#!/bin/sh
# Trusted administrator inputs only. Does not start or enable the service.
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run installer as root' >&2; exit 1; }
[ "$#" = 2 ] || { echo 'Usage: install.sh WHEEL CONFIG' >&2; exit 1; }
wheel=$(realpath "$1")
configuration=$(realpath "$2")
unit=$(dirname "$(realpath "$0")")/cacheweft.service
[ -f "$wheel" ] && [ -f "$configuration" ] && [ -f "$unit" ]
# Never replace a running writer or recreate its state/lock files.
state=$(systemctl is-active cacheweft.service || true)
case "$state" in active|activating|deactivating|reloading) echo 'Stop service before installation' >&2; exit 1;; esac
getent passwd cacheweft >/dev/null || useradd --system --user-group --home-dir /var/lib/cacheweft --shell /usr/sbin/nologin cacheweft
[ "$(id -u cacheweft)" != 0 ]
install -d -m 0755 /opt/cacheweft /etc/cacheweft
python3 -c 'import sys; assert sys.version_info >= (3,12), "Python 3.12+ required"'
python3 -m venv /opt/cacheweft/venv
/opt/cacheweft/venv/bin/pip install --no-index --no-deps --force-reinstall "$wheel"
install -o cacheweft -g cacheweft -m 0600 "$configuration" /etc/cacheweft/config.json
install -o root -g root -m 0644 "$unit" /etc/systemd/system/cacheweft.service
systemctl daemon-reload
