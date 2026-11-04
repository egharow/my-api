#!/usr/bin/env bash
# Installs (or updates) the finance app on an Ubuntu server.  Run from the unzipped FinanceServer folder:
#     bash install.sh
# Safe to run again: it replaces the program, never the data in /opt/finance/data.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE=/opt/finance
APP=$BASE/app
DATA=$BASE/data
VENV=$BASE/venv

if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi

echo "== Installing packages"
$SUDO apt-get update -y
$SUDO apt-get install -y python3 python3-venv python3-pip rsync unzip curl ca-certificates
if ! command -v rclone >/dev/null 2>&1; then curl -fsSL https://rclone.org/install.sh | $SUDO bash; fi
if ! command -v tailscale >/dev/null 2>&1; then curl -fsSL https://tailscale.com/install.sh | $SUDO sh; fi

echo "== Some swap, so a large PDF cannot run a small server out of memory"
if [ ! -f /swapfile ] && [ "$(free -m | awk '/^Mem:/{print $2}')" -lt 3000 ]; then
  $SUDO fallocate -l 2G /swapfile && $SUDO chmod 600 /swapfile && $SUDO mkswap /swapfile && $SUDO swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | $SUDO tee -a /etc/fstab >/dev/null
fi

echo "== The finance user and folders"
id finance >/dev/null 2>&1 || $SUDO useradd --system --home $BASE --shell /usr/sbin/nologin finance
$SUDO mkdir -p $APP $DATA
$SUDO rsync -a --delete --exclude 'server/' --exclude 'my-data/' --exclude '__pycache__/' "$SRC/" "$APP/"

echo "== Python environment"
[ -d $VENV ] || $SUDO python3 -m venv $VENV
$SUDO $VENV/bin/pip install --quiet --upgrade pip
$SUDO $VENV/bin/pip install --quiet "openpyxl>=3.1" "xlrd>=2.0.1" "pdfplumber>=0.11"
$SUDO chown -R finance:finance $BASE

echo "== Settings file (keeps your edits)"
if [ ! -f /etc/finance.env ]; then
  $SUDO tee /etc/finance.env >/dev/null <<'ENV'
# The HTTPS name people use, from "sudo tailscale serve status", for example finance.tail1234.ts.net
FINANCE_ALLOWED_HOSTS=
# Your Drive folders, as named in rclone (see SERVER-SETUP.txt). Leave empty until rclone is set up.
FINANCE_DRIVE_IN=
FINANCE_DRIVE_OUT=
ENV
fi

echo "== The command 'finance'"
$SUDO tee /usr/local/bin/finance >/dev/null <<'CMD'
#!/usr/bin/env bash
# Runs the app's commands as the finance user, for example:  finance user add Ely   or   finance drive-sync
set -a; . /etc/finance.env; set +a
cd /opt/finance/app
exec sudo -u finance -E env HOME=/opt/finance PYTHONPATH=/opt/finance/app FINANCE_ALLOWED_HOSTS="${FINANCE_ALLOWED_HOSTS:-}" FINANCE_DRIVE_IN="${FINANCE_DRIVE_IN:-}" FINANCE_DRIVE_OUT="${FINANCE_DRIVE_OUT:-}" \
  /opt/finance/venv/bin/python -m ftracker --home /opt/finance/data "$@"
CMD
$SUDO chmod 755 /usr/local/bin/finance

echo "== The service"
$SUDO tee /etc/systemd/system/finance.service >/dev/null <<'UNIT'
[Unit]
Description=Household finance app
After=network-online.target
Wants=network-online.target

[Service]
User=finance
Group=finance
Environment=HOME=/opt/finance
Environment=PYTHONPATH=/opt/finance/app
EnvironmentFile=/etc/finance.env
WorkingDirectory=/opt/finance/app
ExecStart=/opt/finance/venv/bin/python -m ftracker --home /opt/finance/data serve --server --port 8765
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=/opt/finance

[Install]
WantedBy=multi-user.target
UNIT
$SUDO systemctl daemon-reload
$SUDO systemctl enable finance >/dev/null 2>&1
$SUDO systemctl restart finance
sleep 2
if curl -fsS http://127.0.0.1:8765/health >/dev/null; then echo "The app is running."; else echo "The app did not start. Look at:  sudo journalctl -u finance -n 50"; exit 1; fi

cat <<'DONE'

Installed. Next, in SERVER-SETUP.txt:
  - connect Tailscale        (sudo tailscale up ; sudo tailscale serve --bg 8765)
  - add yourself and Shir    (finance user add Ely)
  - connect Google Drive     (rclone, step 5)
  - copy your data over      (step 6)
DONE
