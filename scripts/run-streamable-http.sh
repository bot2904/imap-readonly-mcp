#!/usr/bin/env bash
# Run imap-readonly-mcp with Streamable HTTP transport without Docker.
#
# Usage:
#   scripts/run-streamable-http.sh [config/accounts.yaml]
#
# Env-only account configuration example:
#   MAIL_ACCOUNT__PROTOCOL=imap \
#   MAIL_ACCOUNT__HOST=imap.example.com \
#   MAIL_ACCOUNT__USERNAME=user@example.com \
#   MAIL_ACCOUNT__PASSWORD=change-me \
#   scripts/run-streamable-http.sh
#
# Optional environment overrides:
#   HOST=127.0.0.1 PORT=8765 MCP_PATH=/mcp CACHE_PATH=./email_cache.sqlite
#   FASTMCP_LOG_LEVEL=DEBUG scripts/run-streamable-http.sh

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: $0 [config/accounts.yaml]

Runs imap-readonly-mcp with Streamable HTTP transport without Docker.

Examples:
  $0 config/accounts.yaml

  MAIL_ACCOUNT__PROTOCOL=imap \\
  MAIL_ACCOUNT__HOST=imap.example.com \\
  MAIL_ACCOUNT__PORT=993 \\
  MAIL_ACCOUNT__USERNAME=user@example.com \\
  MAIL_ACCOUNT__PASSWORD=change-me \\
  MAIL_ACCOUNT__ALLOWED_FOLDERS='["INBOX", "Archive"]' \\
  $0

Environment overrides:
  HOST=127.0.0.1
  PORT=8765
  MCP_PATH=/mcp
  CACHE_PATH=./email_cache.sqlite
  FASTMCP_LOG_LEVEL=DEBUG
EOF
  exit 0
fi

CONFIG_PATH="${1:-${MAIL_CONFIG_FILE:-$PROJECT_ROOT/config/accounts.yaml}}"
HOST_VALUE="${HOST:-${FASTMCP_HOST:-0.0.0.0}}"
PORT_VALUE="${PORT:-${FASTMCP_PORT:-8765}}"
MCP_PATH_VALUE="${MCP_PATH:-${FASTMCP_STREAMABLE_HTTP__PATH:-/mcp}}"
CACHE_PATH_VALUE="${CACHE_PATH:-${MAIL_CACHE_PATH:-$PROJECT_ROOT/email_cache.sqlite}}"
ACCOUNT_ENV_CONFIGURED="false"

if [[ -n "${MAIL_ACCOUNT:-}" ]]; then
  ACCOUNT_ENV_CONFIGURED="true"
else
  while IFS='=' read -r env_key _; do
    if [[ "$env_key" == MAIL_ACCOUNT__* ]]; then
      ACCOUNT_ENV_CONFIGURED="true"
      break
    fi
  done < <(env)
fi

if [[ ! -f "$CONFIG_PATH" && "$ACCOUNT_ENV_CONFIGURED" != "true" ]]; then
  cat >&2 <<EOF
Config file not found: $CONFIG_PATH

Create one from the example first:
  cp "$PROJECT_ROOT/config/accounts.example.yaml" "$PROJECT_ROOT/config/accounts.yaml"
  \$EDITOR "$PROJECT_ROOT/config/accounts.yaml"

Or pass a config path:
  $0 /path/to/accounts.yaml

Or configure the account with MAIL_ACCOUNT__... environment variables:
  MAIL_ACCOUNT__PROTOCOL=imap \\
  MAIL_ACCOUNT__HOST=imap.example.com \\
  MAIL_ACCOUNT__USERNAME=user@example.com \\
  MAIL_ACCOUNT__PASSWORD=change-me \\
  $0
EOF
  exit 2
fi

export FASTMCP_TRANSPORT="streamable-http"
export FASTMCP_HOST="$HOST_VALUE"
export FASTMCP_PORT="$PORT_VALUE"
export FASTMCP_STREAMABLE_HTTP__PATH="$MCP_PATH_VALUE"
export MAIL_CACHE_PATH="$CACHE_PATH_VALUE"
export MAIL_CONFIG_FILE="$CONFIG_PATH"

if [[ -f "$CONFIG_PATH" ]]; then
  printf '[imap-readonly-mcp] config: %s\n' "$CONFIG_PATH"
else
  printf '[imap-readonly-mcp] config: environment variables (no file at %s)\n' "$CONFIG_PATH"
fi
printf '[imap-readonly-mcp] url:    http://%s:%s%s\n' "$FASTMCP_HOST" "$FASTMCP_PORT" "$FASTMCP_STREAMABLE_HTTP__PATH"
printf '[imap-readonly-mcp] cache:  %s\n' "$MAIL_CACHE_PATH"

cd "$PROJECT_ROOT"

if [[ -x "$PROJECT_ROOT/.venv/bin/imap-readonly-mcp" ]]; then
  exec "$PROJECT_ROOT/.venv/bin/imap-readonly-mcp" --config "$CONFIG_PATH" --transport streamable-http
fi

if command -v imap-readonly-mcp >/dev/null 2>&1; then
  exec imap-readonly-mcp --config "$CONFIG_PATH" --transport streamable-http
fi

if command -v uv >/dev/null 2>&1; then
  exec uv run --project "$PROJECT_ROOT" imap-readonly-mcp --config "$CONFIG_PATH" --transport streamable-http
fi

# Last-resort local source-tree execution. Dependencies must already be installed.
export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec python -m imap_readonly_mcp.server --config "$CONFIG_PATH" --transport streamable-http
