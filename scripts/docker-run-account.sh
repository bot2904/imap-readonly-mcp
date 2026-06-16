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
      --dry-run         Print the docker command instead of executing it.
  -h, --help            Show this help.

Environment overrides:
  IMAGE                 Same as --image.
  HOST_PORT             Host port to publish (default: FASTMCP_PORT, usually 8765).
  CONTAINER_NAME        Container name (default: imap-readonly-mcp-ACCOUNT_KEY).
  FASTMCP_PORT          Container port (default: 8765).
  FASTMCP_HOST          Container bind host (default: 0.0.0.0).
  FASTMCP_STREAMABLE_HTTP__PATH  MCP path (default: /mcp).
  FASTMCP_LOG_LEVEL     Log level (default: INFO).
  MAIL_CACHE_PATH       Cache path inside container (default: /tmp/email_cache.sqlite).

Examples:
  scripts/docker-run-account.sh --env-dir ../mail-secrets fastmail
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
IMAGE_VALUE="${IMAGE:-$DEFAULT_IMAGE}"
BUILD_IMAGE="auto"
DRY_RUN="false"
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
declare -A PASS_ENV=()

add_pass_env() {
  local name="$1"
  export "$name"
  PASS_ENV["$name"]=1
}

while IFS= read -r source_name; do
  suffix="${source_name#${ACCOUNT_PREFIX}}"
  target_name="MAIL_ACCOUNT__${suffix}"
  printf -v "$target_name" '%s' "${!source_name}"
  SELECTED_ENV_NAMES+=("$target_name")
  add_pass_env "$target_name"
done < <(compgen -A variable "$ACCOUNT_PREFIX" | sort)

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
  if [[ -v "$name" ]]; then
    add_pass_env "$name"
  fi
done

# Preserve any other explicit FastMCP vars from the loaded .env/environment.
while IFS= read -r name; do
  add_pass_env "$name"
done < <(compgen -A variable FASTMCP_ | sort)

HOST_PORT_VALUE="${HOST_PORT:-$FASTMCP_PORT}"
CONTAINER_NAME_VALUE="${CONTAINER_NAME:-imap-readonly-mcp-${ACCOUNT_KEY}}"

declare -a ENV_ARGS=()
while IFS= read -r name; do
  ENV_ARGS+=(--env "$name")
done < <(printf '%s\n' "${!PASS_ENV[@]}" | sort)

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

COMMAND=(
  docker run --rm
  --name "$CONTAINER_NAME_VALUE"
  -p "${HOST_PORT_VALUE}:${FASTMCP_PORT}"
  "${ENV_ARGS[@]}"
  "${DOCKER_RUN_ARGS[@]}"
  "$IMAGE_VALUE"
  --config /dev/null
  --transport "$FASTMCP_TRANSPORT"
)

printf '[imap-readonly-mcp] env:     %s\n' "$ENV_FILE"
printf '[imap-readonly-mcp] account: %s (%d mapped vars)\n' "$ACCOUNT_KEY" "${#SELECTED_ENV_NAMES[@]}"
printf '[imap-readonly-mcp] image:   %s\n' "$IMAGE_VALUE"
printf '[imap-readonly-mcp] url:     http://localhost:%s%s\n' "$HOST_PORT_VALUE" "$FASTMCP_STREAMABLE_HTTP__PATH"

if [[ "$DRY_RUN" == "true" ]]; then
  printf '+ '
  printf '%q ' "${COMMAND[@]}"
  printf '\n'
  exit 0
fi

exec "${COMMAND[@]}"
