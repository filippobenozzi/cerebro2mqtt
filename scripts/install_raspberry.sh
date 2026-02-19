#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${APP_NAME:-cerebro2mqtt}"
INSTALL_DIR="${INSTALL_DIR:-/opt/cerebro2mqtt}"
SERVICE_NAME="${SERVICE_NAME:-${APP_NAME}.service}"
APP_USER="${APP_USER:-root}"
APP_GROUP="${APP_GROUP:-${APP_USER}}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SERIAL_PORT="${SERIAL_PORT:-/dev/ttyS0}"
CONFIG_REL_PATH="config/config.json"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

fail() {
  printf 'ERRORE: %s\n' "$*" >&2
  exit 1
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    fail "Esegui come root: sudo bash ${0}"
  fi
}

check_source_tree() {
  [[ -f "${REPO_DIR}/requirements.txt" ]] || fail "requirements.txt non trovato in ${REPO_DIR}"
  [[ -f "${REPO_DIR}/app/main.py" ]] || fail "app/main.py non trovato in ${REPO_DIR}"
  [[ -f "${REPO_DIR}/${CONFIG_REL_PATH}" ]] || fail "${CONFIG_REL_PATH} non trovato in ${REPO_DIR}"
}

install_system_packages() {
  log "Installo dipendenze di sistema"
  apt-get update
  apt-get install -y --no-install-recommends \
    ca-certificates \
    psmisc \
    python3 \
    python3-venv \
    python3-pip \
    rsync
}

configure_serial_port_guard() {
  local port_name
  port_name="$(basename "${SERIAL_PORT}")"

  log "Disabilito getty seriale su ${port_name} (persistente)"
  systemctl disable --now "serial-getty@${port_name}.service" 2>/dev/null || true
  systemctl mask "serial-getty@${port_name}.service" 2>/dev/null || true

  # Alias usato spesso su Raspberry
  systemctl disable --now serial-getty@serial0.service 2>/dev/null || true
  systemctl mask serial-getty@serial0.service 2>/dev/null || true
}

ensure_user_group() {
  if [[ "${APP_USER}" == "root" ]]; then
    return
  fi

  if ! getent group "${APP_GROUP}" >/dev/null 2>&1; then
    log "Creo gruppo ${APP_GROUP}"
    groupadd --system "${APP_GROUP}"
  fi

  if ! id -u "${APP_USER}" >/dev/null 2>&1; then
    log "Creo utente di servizio ${APP_USER}"
    useradd --system --gid "${APP_GROUP}" --home "${INSTALL_DIR}" --shell /usr/sbin/nologin "${APP_USER}"
  fi
}

sync_application() {
  local backup_config=""

  if [[ -f "${INSTALL_DIR}/${CONFIG_REL_PATH}" ]]; then
    backup_config="$(mktemp)"
    cp "${INSTALL_DIR}/${CONFIG_REL_PATH}" "${backup_config}"
    log "Backup configurazione esistente"
  fi

  log "Sincronizzo file applicazione in ${INSTALL_DIR}"
  mkdir -p "${INSTALL_DIR}"

  rsync -a --delete \
    --exclude '.git/' \
    --exclude '.venv/' \
    --exclude '__pycache__/' \
    --exclude '.pycache/' \
    --exclude '.pytest_cache/' \
    --exclude 'data/' \
    --exclude '*.pyc' \
    --exclude "${CONFIG_REL_PATH}" \
    "${REPO_DIR}/" "${INSTALL_DIR}/"

  mkdir -p "${INSTALL_DIR}/config"

  if [[ -n "${backup_config}" ]]; then
    mv "${backup_config}" "${INSTALL_DIR}/${CONFIG_REL_PATH}"
    log "Ripristino configurazione precedente"
  else
    cp "${REPO_DIR}/${CONFIG_REL_PATH}" "${INSTALL_DIR}/${CONFIG_REL_PATH}"
    log "Copio configurazione di default"
  fi

  if [[ "${APP_USER}" != "root" ]]; then
    chown -R "${APP_USER}:${APP_GROUP}" "${INSTALL_DIR}"
  fi
}

install_python_dependencies() {
  log "Creo ambiente virtuale Python"
  "${PYTHON_BIN}" -m venv "${INSTALL_DIR}/.venv"

  log "Installo dipendenze Python"
  "${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip
  "${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"
}

install_systemd_service() {
  local port_name
  port_name="$(basename "${SERIAL_PORT}")"

  log "Creo unit systemd ${SERVICE_NAME}"

  cat > "${SERVICE_FILE}" <<UNIT
[Unit]
Description=Cerebro2MQTT bridge service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${INSTALL_DIR}
Environment=CEREBRO_CONFIG=${INSTALL_DIR}/${CONFIG_REL_PATH}
Environment=LOG_LEVEL=INFO
ExecStart=${INSTALL_DIR}/.venv/bin/python -m app.main
ExecStartPre=/bin/sh -c 'systemctl stop serial-getty@${port_name}.service 2>/dev/null || true'
ExecStartPre=/bin/sh -c 'systemctl stop serial-getty@serial0.service 2>/dev/null || true'
ExecStartPre=/bin/sh -c 'fuser -k ${SERIAL_PORT} >/dev/null 2>&1 || true'
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
UNIT

  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}"
  systemctl restart "${SERVICE_NAME}"
}

print_summary() {
  log "Installazione completata"
  log "Servizio: ${SERVICE_NAME}"
  log "Config: ${INSTALL_DIR}/${CONFIG_REL_PATH}"
  log "Log: journalctl -u ${SERVICE_NAME} -f"
  log "Web UI: http://<ip-raspberry>:80"
  systemctl --no-pager --full status "${SERVICE_NAME}" | sed -n '1,12p'
}

main() {
  require_root
  check_source_tree
  install_system_packages
  configure_serial_port_guard
  ensure_user_group
  sync_application
  install_python_dependencies
  install_systemd_service
  print_summary
}

main "$@"
