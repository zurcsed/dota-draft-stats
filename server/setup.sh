#!/bin/bash
# One-time setup of the Valve match collector on a small Ubuntu/Debian server. Run as root:
#     bash setup.sh
# What it does: installs Python and git, takes the code from the public feed repository, creates a
# user without a password, and starts three systemd services:
#   valve-collect  collects the matches all the time and restarts itself after a crash or a reboot
#   valve-export   (daily timer) makes valve_stats.json from the last 7 days and prunes old matches
#   valve-serve    gives the file to the feed builder at http://<server ip>:8080/valve_stats.json
set -euo pipefail

APP=/opt/dota-collector
REPO=https://github.com/zurcsed/dota-draft-stats.git

apt-get update -y
apt-get install -y python3 git

id collector >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin collector
if [ -d "$APP/.git" ]; then
    runuser -u collector -- git -C "$APP" pull --ff-only
else
    git clone "$REPO" "$APP"
fi
mkdir -p "$APP/cache" "$APP/logs" "$APP/public"
chown -R collector:collector "$APP"

# the Steam key goes here (only root and the collector can read it)
if [ ! -f /etc/dota-collector.env ]; then
    echo 'STEAM_API_KEY=PUT_THE_KEY_HERE' > /etc/dota-collector.env
fi
chown root:collector /etc/dota-collector.env
chmod 640 /etc/dota-collector.env

install -m 644 "$APP/server/valve-collect.service" /etc/systemd/system/valve-collect.service
install -m 644 "$APP/server/valve-export.service" /etc/systemd/system/valve-export.service
install -m 644 "$APP/server/valve-export.timer" /etc/systemd/system/valve-export.timer
install -m 644 "$APP/server/valve-serve.service" /etc/systemd/system/valve-serve.service
systemctl daemon-reload

if grep -q PUT_THE_KEY_HERE /etc/dota-collector.env; then
    echo
    echo "Next: put your Steam key into /etc/dota-collector.env, then run:"
    echo "    systemctl enable --now valve-collect valve-serve valve-export.timer"
else
    systemctl enable --now valve-collect valve-serve valve-export.timer
    echo "Started. Look at the progress: journalctl -u valve-collect -n 20 --no-pager"
fi
