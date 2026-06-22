#!/usr/bin/env bash
# Run imap-readonly-mcp with docker run, selecting one keyed account from an external .env file.
#
# Expected .env shape:
#   MAIL_ACCOUNT_fastmail__PROTOCOL=imap
#   MAIL_ACCOUNT_fastmail__PORT=993
#   MAIL_ACCOUNT_fastmail__HOST=imap.fastmail.com
#   MAIL_ACCOUNT_fastmail__USERNAME=user@example.com
#   MAIL_ACCOUNT_fastmail__PASSWORD=change-me
#   MAIL_ACCOUNT_fastmail__ALLOWED_FOLDERS='["INBOX"]'
#
# The selected key (for example "fastmail") is remapped to the env vars consumed by
# the server:
#   MAIL_ACCOUNT__PROTOCOL, MAIL_ACCOUNT__HOST, ...

set -euo pipefail
shopt -s extglob

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

trim_leading() {
  local value="$1"
  value="${value##+([[:space:]])}"
  printf '%s' "$value"
}

trim_trailing() {
  local value="$1"
  value="${value%%+([[:space:]])}"
  printf '%s' "$value"
}

trim() {
  local value="$1"
  value="$(trim_leading "$value")"
  value="$(trim_trailing "$value")"
  printf '%s' "$value"
}

load_dotenv() {
  local file="$1"
  local line key value

  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    line="$(trim "$line")"

    [[ -z "$line" || "$line" == \#* ]] && continue

    if [[ "$line" =~ ^export[[:space:]]+(.+)$ ]]; then
      line="${BASH_REMATCH[1]}"
    fi

    [[ "$line" == *=* ]] || continue

    key="$(trim "${line%%=*}")"
    value="${line#*=}"
    value="$(trim_leading "$value")"

    if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      printf 'Ignoring invalid env var name in %s: %s\n' "$file" "$key" >&2
      continue
    fi

    if [[ "$value" =~ ^\'(.*)\'[[:space:]]*(#.*)?$ ]]; then
      value="${BASH_REMATCH[1]}"
    elif [[ "$value" =~ ^\"(.*)\"[[:space:]]*(#.*)?$ ]]; then
      value="${BASH_REMATCH[1]}"
      value="${value//\\n/$'\n'}"
      value="${value//\\r/$'\r'}"
      value="${value//\\t/$'\t'}"
      value="${value//\\\"/\"}"
      value="${value//\\\\/\\}"
    else
      if [[ "$value" =~ ^(.*)[[:space:]]+#.*$ ]]; then
        value="${BASH_REMATCH[1]}"
      fi
      value="$(trim_trailing "$value")"
    fi

    printf -v "$key" '%s' "$value"
    export "$key"
  done < "$file"
}

declare -A EXPLICIT_MAIL_ACCOUNT_OVERRIDES=()
while IFS= read -r name; do
  EXPLICIT_MAIL_ACCOUNT_OVERRIDES["$name"]="${!name}"
done < <(compgen -A variable MAIL_ACCOUNT__ | sort)

is_truthy() {
  case "$1" in
    1|[Tt][Rr][Uu][Ee]|[Yy][Ee][Ss]|[Oo][Nn]) return 0 ;;
    *) return 1 ;;
  esac
}

var_is_set() {
  local name="$1"
  eval "[[ \${$name+x} ]]"
}

is_builtin_network() {
  case "$1" in
    ''|bridge|host|none) return 0 ;;
    *) return 1 ;;
  esac
}

usage() {
  cat <<'EOF'
Usage:
  scripts/docker-run-account.sh [options] ACCOUNT_KEY [ENV_DIR_OR_FILE] [-- docker-run-args...]

Options:
  -e, --env-file FILE   Read keyed account variables from FILE.
  -d, --env-dir DIR     Read keyed account variables from DIR/.env.
      --image IMAGE     Docker image to run (default: bot2904/imap-readonly-mcp).
      --build           Build the image before running.
      --no-build        Skip the automatic build for the default image.
      --network NAME    Connect to Docker network NAME (default: pi2904network).
      --publish         Publish the MCP port to the host. Off by default.
      --dry-run         Print the docker command instead of executing it.
  -h, --help            Show this help.

Environment overrides:
  IMAGE                 Same as --image.
  DOCKER_NETWORK        Same as --network. Default network is auto-created.
  PUBLISH_PORTS         true/1/yes/on to publish the MCP port to the host.
  HOST_PORT             Host port when publishing (default: FASTMCP_PORT).
  CONTAINER_NAME        Container name (default: imap-readonly-mcp-ACCOUNT_KEY).
  FASTMCP_PORT          Container port (default: 8765).
  FASTMCP_HOST          Container bind host (default: 0.0.0.0).
  FASTMCP_STREAMABLE_HTTP__PATH  MCP path (default: /mcp).
  FASTMCP_LOG_LEVEL     Log level (default: INFO).
  MAIL_CACHE_PATH       Cache path inside container (default: /tmp/email_cache.sqlite).
  MAIL_ACCOUNT__*       Override selected account variables after ACCOUNT_KEY mapping,
                        for example MAIL_ACCOUNT__ALLOWED_FOLDERS='["INBOX"]'.

Examples:
  scripts/docker-run-account.sh --env-dir ../mail-secrets fastmail
  scripts/docker-run-account.sh --network mcp --env-dir ../mail-secrets fastmail
  scripts/docker-run-account.sh --publish --env-dir ../mail-secrets fastmail
  scripts/docker-run-account.sh --env-file ../mail-secrets/.env fastmail -d
  IMAGE=bot2904/imap-readonly-mcp:latest \
    scripts/docker-run-account.sh --no-build --env-dir ../mail-secrets fastmail

Given this in ../mail-secrets/.env:
  MAIL_ACCOUNT_fastmail__PROTOCOL=imap
  MAIL_ACCOUNT_fastmail__PORT=993
  MAIL_ACCOUNT_fastmail__HOST=imap.fastmail.com
  MAIL_ACCOUNT_fastmail__USERNAME=user@example.com
  MAIL_ACCOUNT_fastmail__PASSWORD=change-me
  MAIL_ACCOUNT_fastmail__ALLOWED_FOLDERS='["INBOX"]'

The script exports these to the container:
  MAIL_ACCOUNT__PROTOCOL=imap
  MAIL_ACCOUNT__PORT=993
  MAIL_ACCOUNT__HOST=imap.fastmail.com
  MAIL_ACCOUNT__USERNAME=user@example.com
  MAIL_ACCOUNT__PASSWORD=change-me
  MAIL_ACCOUNT__ALLOWED_FOLDERS='["INBOX"]'
EOF
}

ENV_FILE="${MAIL_ENV_FILE:-}"
DEFAULT_IMAGE="bot2904/imap-readonly-mcp"
DEFAULT_NETWORK="pi2904network"
IMAGE_VALUE="${IMAGE:-$DEFAULT_IMAGE}"
BUILD_IMAGE="auto"
DRY_RUN="false"
DOCKER_NETWORK_OPTION=""
PUBLISH_PORTS_OPTION=""
ACCOUNT_KEY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -e|--env-file)
      [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
      ENV_FILE="$2"
      shift 2
      ;;
    -d|--env-dir)
      [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
      ENV_FILE="$2/.env"
      shift 2
      ;;
    --image)
      [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
      IMAGE_VALUE="$2"
      shift 2
      ;;
    --build)
      BUILD_IMAGE="true"
      shift
      ;;
    --no-build)
      BUILD_IMAGE="false"
      shift
      ;;
    --network)
      [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
      DOCKER_NETWORK_OPTION="$2"
      shift 2
      ;;
    --publish)
      PUBLISH_PORTS_OPTION="true"
      shift
      ;;
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      # First unknown option belongs to docker run; ACCOUNT_KEY must already be set.
      if [[ -z "$ACCOUNT_KEY" ]]; then
        echo "Unknown option before ACCOUNT_KEY: $1" >&2
        echo >&2
        usage >&2
        exit 2
      fi
      break
      ;;
    *)
      ACCOUNT_KEY="$1"
      shift
      break
      ;;
  esac
done

if [[ -z "$ACCOUNT_KEY" ]]; then
  echo "ACCOUNT_KEY is required." >&2
  echo >&2
  usage >&2
  exit 2
fi

if [[ ! "$ACCOUNT_KEY" =~ ^[A-Za-z0-9_]+$ ]]; then
  echo "ACCOUNT_KEY must contain only letters, numbers, and underscores: $ACCOUNT_KEY" >&2
  exit 2
fi

# Optional positional env dir/file after the account key.
if [[ $# -gt 0 && -z "$ENV_FILE" ]]; then
  if [[ -d "$1" ]]; then
    ENV_FILE="$1/.env"
    shift
  elif [[ -f "$1" ]]; then
    ENV_FILE="$1"
    shift
  fi
fi

if [[ -z "$ENV_FILE" ]]; then
  ENV_FILE="$PROJECT_ROOT/.env"
fi

if [[ ! -f "$ENV_FILE" ]]; then
  cat >&2 <<EOF
Env file not found: $ENV_FILE

Pass one explicitly, for example:
  $0 --env-dir ../mail-secrets $ACCOUNT_KEY
  $0 --env-file ../mail-secrets/.env $ACCOUNT_KEY
EOF
  exit 2
fi

# Remaining args are passed through to docker run before the image name.
DOCKER_RUN_ARGS=("$@")

# Load the source .env without shell expansion, so values like passwords with '$'
# and quoted JSON such as '["INBOX"]' are preserved literally.
load_dotenv "$ENV_FILE"

ACCOUNT_PREFIX="MAIL_ACCOUNT_${ACCOUNT_KEY}__"
declare -a SELECTED_ENV_NAMES=()
declare -a PASS_ENV_NAMES=()

append_unique_pass_env_name() {
  local name="$1"
  local existing

  for existing in "${PASS_ENV_NAMES[@]+"${PASS_ENV_NAMES[@]}"}"; do
    [[ "$existing" == "$name" ]] && return 0
  done

  PASS_ENV_NAMES+=("$name")
}

add_pass_env() {
  local name="$1"
  export "$name"
  append_unique_pass_env_name "$name"
}

while IFS= read -r source_name; do
  suffix="${source_name#${ACCOUNT_PREFIX}}"
  target_name="MAIL_ACCOUNT__${suffix}"
  printf -v "$target_name" '%s' "${!source_name}"
  SELECTED_ENV_NAMES+=("$target_name")
  add_pass_env "$target_name"
done < <(compgen -A variable "$ACCOUNT_PREFIX" | sort)

# Let wrapper scripts override the mapped account values with explicit
# MAIL_ACCOUNT__* environment variables, e.g.:
#   MAIL_ACCOUNT__ALLOWED_FOLDERS='["INBOX","Archive"]' \
#     scripts/docker-run-account.sh --env-dir ../mail-secrets fastmail
for target_name in "${!EXPLICIT_MAIL_ACCOUNT_OVERRIDES[@]}"; do
  printf -v "$target_name" '%s' "${EXPLICIT_MAIL_ACCOUNT_OVERRIDES[$target_name]}"
  add_pass_env "$target_name"
done

if [[ ${#SELECTED_ENV_NAMES[@]} -eq 0 ]]; then
  cat >&2 <<EOF
No variables found for account key '$ACCOUNT_KEY' in $ENV_FILE

Expected variables with prefix:
  ${ACCOUNT_PREFIX}

Example:
  ${ACCOUNT_PREFIX}PROTOCOL=imap
  ${ACCOUNT_PREFIX}HOST=imap.fastmail.com
  ${ACCOUNT_PREFIX}USERNAME=user@example.com
  ${ACCOUNT_PREFIX}PASSWORD=change-me
EOF
  exit 2
fi

missing=()
for required in MAIL_ACCOUNT__PROTOCOL MAIL_ACCOUNT__HOST MAIL_ACCOUNT__USERNAME MAIL_ACCOUNT__PASSWORD; do
  if [[ -z "${!required:-}" ]]; then
    missing+=("${ACCOUNT_PREFIX}${required#MAIL_ACCOUNT__}")
  fi
done

if [[ ${#missing[@]} -gt 0 ]]; then
  printf 'Missing required account variables for key %q:\n' "$ACCOUNT_KEY" >&2
  printf '  %s\n' "${missing[@]}" >&2
  exit 2
fi

# Runtime defaults for Streamable HTTP.
: "${FASTMCP_TRANSPORT:=streamable-http}"
: "${FASTMCP_HOST:=0.0.0.0}"
: "${FASTMCP_PORT:=8765}"
: "${FASTMCP_STREAMABLE_HTTP__PATH:=/mcp}"
: "${FASTMCP_LOG_LEVEL:=INFO}"
: "${MAIL_CACHE_PATH:=/tmp/email_cache.sqlite}"

for name in FASTMCP_TRANSPORT FASTMCP_HOST FASTMCP_PORT FASTMCP_STREAMABLE_HTTP__PATH FASTMCP_LOG_LEVEL MAIL_CACHE_PATH; do
  add_pass_env "$name"
done

# Optional top-level server settings if present in the loaded .env/environment.
for name in MAIL_FETCH_CONCURRENCY MAIL_DEFAULT_SEARCH_LIMIT MAIL_MAXIMUM_SEARCH_LIMIT MAIL_CONNECTION_RETRIES; do
  if var_is_set "$name"; then
    add_pass_env "$name"
  fi
done

# Preserve any other explicit FastMCP vars from the loaded .env/environment.
while IFS= read -r name; do
  add_pass_env "$name"
done < <(compgen -A variable FASTMCP_ | sort)

DOCKER_NETWORK_VALUE="${DOCKER_NETWORK_OPTION:-${DOCKER_NETWORK:-$DEFAULT_NETWORK}}"
PUBLISH_PORTS_VALUE="${PUBLISH_PORTS_OPTION:-${PUBLISH_PORTS:-false}}"
HOST_PORT_VALUE="${HOST_PORT:-$FASTMCP_PORT}"
CONTAINER_NAME_VALUE="${CONTAINER_NAME:-imap-readonly-mcp-${ACCOUNT_KEY}}"

declare -a NETWORK_ARGS=()
if [[ -n "$DOCKER_NETWORK_VALUE" ]]; then
  NETWORK_ARGS+=(--network "$DOCKER_NETWORK_VALUE")
fi

declare -a PORT_ARGS=(--expose "$FASTMCP_PORT")
if is_truthy "$PUBLISH_PORTS_VALUE"; then
  PORT_ARGS+=(-p "${HOST_PORT_VALUE}:${FASTMCP_PORT}")
fi

declare -a ENV_ARGS=()
while IFS= read -r name; do
  ENV_ARGS+=(--env "$name")
done < <(printf '%s\n' "${PASS_ENV_NAMES[@]}" | sort)

if [[ "$BUILD_IMAGE" == "auto" ]]; then
  if [[ "$IMAGE_VALUE" == "$DEFAULT_IMAGE" ]]; then
    BUILD_IMAGE="true"
  else
    BUILD_IMAGE="false"
  fi
fi

if [[ "$BUILD_IMAGE" == "true" ]]; then
  if [[ "$DRY_RUN" == "true" ]]; then
    printf '+ cd %q && docker build -t %q .\n' "$PROJECT_ROOT" "$IMAGE_VALUE"
  else
    (
      cd "$PROJECT_ROOT"
      docker build -t "$IMAGE_VALUE" .
    )
  fi
fi

if [[ -n "$DOCKER_NETWORK_VALUE" ]] && ! is_builtin_network "$DOCKER_NETWORK_VALUE"; then
  if [[ "$DRY_RUN" == "true" ]]; then
    printf '+ docker network inspect %q >/dev/null 2>&1 || docker network create %q >/dev/null\n' "$DOCKER_NETWORK_VALUE" "$DOCKER_NETWORK_VALUE"
  else
    docker network inspect "$DOCKER_NETWORK_VALUE" >/dev/null 2>&1 || docker network create "$DOCKER_NETWORK_VALUE" >/dev/null
  fi
fi

COMMAND=(
  docker run --rm
  --name "$CONTAINER_NAME_VALUE"
  "${NETWORK_ARGS[@]+"${NETWORK_ARGS[@]}"}"
  "${PORT_ARGS[@]+"${PORT_ARGS[@]}"}"
  "${ENV_ARGS[@]+"${ENV_ARGS[@]}"}"
  "${DOCKER_RUN_ARGS[@]+"${DOCKER_RUN_ARGS[@]}"}"
  "$IMAGE_VALUE"
  --config /dev/null
  --transport "$FASTMCP_TRANSPORT"
)

printf '[imap-readonly-mcp] env:     %s\n' "$ENV_FILE"
printf '[imap-readonly-mcp] account: %s (%d mapped vars)\n' "$ACCOUNT_KEY" "${#SELECTED_ENV_NAMES[@]}"
printf '[imap-readonly-mcp] image:   %s\n' "$IMAGE_VALUE"
printf '[imap-readonly-mcp] network: %s\n' "${DOCKER_NETWORK_VALUE:-default}"
if is_truthy "$PUBLISH_PORTS_VALUE"; then
  printf '[imap-readonly-mcp] url:     http://localhost:%s%s\n' "$HOST_PORT_VALUE" "$FASTMCP_STREAMABLE_HTTP__PATH"
else
  printf '[imap-readonly-mcp] access:  same Docker network only; no host port published\n'
  printf '[imap-readonly-mcp] url:     http://%s:%s%s\n' "$CONTAINER_NAME_VALUE" "$FASTMCP_PORT" "$FASTMCP_STREAMABLE_HTTP__PATH"
fi

if [[ "$DRY_RUN" == "true" ]]; then
  printf '+ '
  printf '%q ' "${COMMAND[@]}"
  printf '\n'
  exit 0
fi

exec "${COMMAND[@]}"
