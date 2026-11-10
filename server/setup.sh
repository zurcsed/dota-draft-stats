#!/bin/bash
# One-time setup of the Valve match collector on a small Ubuntu/Debian server. Run as root:
#     bash setup.sh
# What it does: installs Python and git, takes the code from the public feed repository, creates a
# user without a password, and starts three systemd services:
#   valve-collect  collects the matches all the time and restarts itself after a crash or a reboot
#   valve-export   (daily timer) makes valve_stats.json from the last 14 days and prunes old matches
#   valve-serve    gives the file to the feed builder at http://<server ip>:8080/valve_stats.json
# and a nightly timer:
#   protracker-fetch  Dota Pro Tracker's builds once a week (20:30 UTC, as agreed with the site), served
#                  at http://<server ip>:8080/protracker.json for the feed builder
# and the launch counter (launch-count, port 80): a random install number and the version a day from
# the friends' exe, no IP addresses; see them with: python3 /opt/dota-collector/launch_count.py --report
# Run it again after the code changes: it takes the new code (git pull) and installs the units.
set -euo pipefail

APP=/opt/dota-collector
REPO=https://github.com/zurcsed/dota-draft-stats.git

apt-get update -y
apt-get install -y python3 git

id collector >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin collector
if [ -d "$APP/.git" ]; then
    chown -R collector:collector "$APP"
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
install -m 644 "$APP/server/protracker-fetch.service" /etc/systemd/system/protracker-fetch.service
install -m 644 "$APP/server/protracker-fetch.timer" /etc/systemd/system/protracker-fetch.timer
install -m 644 "$APP/server/launch-count.service" /etc/systemd/system/launch-count.service
systemctl daemon-reload
systemctl enable --now protracker-fetch.timer  # needs no key
systemctl enable launch-count && systemctl restart launch-count  # restart: the new code after a pull

if grep -q PUT_THE_KEY_HERE /etc/dota-collector.env; then
    echo
    echo "Next: put your Steam key into /etc/dota-collector.env, then run:"
    echo "    systemctl enable --now valve-collect valve-serve valve-export.timer"
else
    systemctl enable --now valve-collect valve-serve valve-export.timer
    echo "Started. Look at the progress: journalctl -u valve-collect -n 20 --no-pager"
    echo "Dota Pro Tracker: next run $(systemctl show protracker-fetch.timer -p NextElapseUSecRealtime --value)"
fi
