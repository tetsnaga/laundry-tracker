#!/usr/bin/env bash
set -euo pipefail

readonly REPO_SSH_URL="git@github.com:tetsnaga/laundry-tracker.git"
readonly APP_DIR="/opt/laundry-tracker"
readonly STATE_DIR="/var/lib/laundry-tracker"
readonly SECRETS_DIR="/etc/laundry-tracker"
readonly SERVICE_USER="laundry-collector"
readonly SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

deploy_key_path="${1:-}"
if [[ -z "${deploy_key_path}" || ! -f "${deploy_key_path}" ]]; then
  echo "Usage: sudo ./oracle/install.sh /path/to/github-deploy-private-key" >&2
  exit 2
fi

if command -v apt-get >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates git openssh-client python3 rsync util-linux
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y ca-certificates git openssh-clients python3 rsync util-linux
else
  echo "This installer supports Ubuntu and Oracle Linux package managers." >&2
  exit 3
fi

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --home-dir "${STATE_DIR}" --create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

install -d -o root -g root -m 0755 "${APP_DIR}"
install -o root -g root -m 0644 "${SOURCE_DIR}/collect.py" "${APP_DIR}/collect.py"
install -o root -g root -m 0644 "${SOURCE_DIR}/wash_test.py" "${APP_DIR}/wash_test.py"
install -o root -g root -m 0755 "${SOURCE_DIR}/oracle/collect-local.sh" "${APP_DIR}/collect-local.sh"
install -o root -g root -m 0755 "${SOURCE_DIR}/oracle/publish.sh" "${APP_DIR}/publish.sh"
install -o root -g root -m 0755 "${SOURCE_DIR}/oracle/collect-and-push.sh" "${APP_DIR}/collect-and-push.sh"
install -o root -g root -m 0755 "${SOURCE_DIR}/oracle/status.sh" "${APP_DIR}/status.sh"

install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0700 "${STATE_DIR}/.ssh"
install -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0600 "${deploy_key_path}" "${STATE_DIR}/.ssh/github-deploy-key"
ssh-keyscan -H github.com >"${STATE_DIR}/.ssh/known_hosts"
chown "${SERVICE_USER}:${SERVICE_USER}" "${STATE_DIR}/.ssh/known_hosts"
chmod 0600 "${STATE_DIR}/.ssh/known_hosts"

cat >"${STATE_DIR}/.ssh/config" <<'EOF'
Host github.com
  HostName github.com
  User git
  IdentityFile /var/lib/laundry-tracker/.ssh/github-deploy-key
  IdentitiesOnly yes
  StrictHostKeyChecking yes
EOF
chown "${SERVICE_USER}:${SERVICE_USER}" "${STATE_DIR}/.ssh/config"
chmod 0600 "${STATE_DIR}/.ssh/config"

install -d -o root -g "${SERVICE_USER}" -m 0750 "${SECRETS_DIR}"
read -r -p "WASH email: " wash_email
read -r -s -p "WASH password (hidden): " wash_password
echo
if [[ -z "${wash_email}" || -z "${wash_password}" ]]; then
  echo "WASH email and password cannot be empty." >&2
  exit 4
fi
printf '%s' "${wash_email}" >"${SECRETS_DIR}/wash-email"
printf '%s' "${wash_password}" >"${SECRETS_DIR}/wash-password"
chown root:"${SERVICE_USER}" "${SECRETS_DIR}/wash-email" "${SECRETS_DIR}/wash-password"
chmod 0440 "${SECRETS_DIR}/wash-email" "${SECRETS_DIR}/wash-password"
unset wash_email wash_password

if [[ ! -d "${STATE_DIR}/history/.git" ]]; then
  runuser -u "${SERVICE_USER}" -- git clone --single-branch --branch data "${REPO_SSH_URL}" "${STATE_DIR}/history"
fi
install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0700 "${STATE_DIR}/data"
# Preserve previously published observations when upgrading an existing VM.
runuser -u "${SERVICE_USER}" -- rsync -a --ignore-existing "${STATE_DIR}/history/data/" "${STATE_DIR}/data/"
runuser -u "${SERVICE_USER}" -- git -C "${STATE_DIR}/history" config user.name "oracle-laundry-collector"
runuser -u "${SERVICE_USER}" -- git -C "${STATE_DIR}/history" config user.email "oracle-laundry-collector@users.noreply.github.com"

install -o root -g root -m 0644 "${SOURCE_DIR}/oracle/laundry-collector.service" /etc/systemd/system/laundry-collector.service
install -o root -g root -m 0644 "${SOURCE_DIR}/oracle/laundry-collector.timer" /etc/systemd/system/laundry-collector.timer
install -o root -g root -m 0644 "${SOURCE_DIR}/oracle/laundry-publisher.service" /etc/systemd/system/laundry-publisher.service
install -o root -g root -m 0644 "${SOURCE_DIR}/oracle/laundry-publisher.timer" /etc/systemd/system/laundry-publisher.timer
systemctl daemon-reload

echo
echo "Installed. Run a proof collection with:"
echo "  sudo systemctl start laundry-collector.service"
echo "  sudo systemctl status laundry-collector.service --no-pager"
echo "  sudo systemctl start laundry-publisher.service"
echo
echo "After the proof collection appears on the data branch, enable the timer with:"
echo "  sudo systemctl enable --now laundry-collector.timer"
echo "  sudo systemctl enable --now laundry-publisher.timer"
