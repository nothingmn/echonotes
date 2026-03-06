#!/usr/bin/env bash

set -euo pipefail

IMAGE_NAME="echonotes:latest"
CONTAINER_NAME="echonotes"
DETACH=1
GPU=0
MODEL_CACHE_DIR=""

usage() {
    cat <<'EOF'
Usage: ./run.sh [options] --incoming <folder> --vault <folder> --config-dir <folder>

Options:
  --image-name <name>        Docker image name to run (default: echonotes:latest)
  --container-name <name>    Docker container name (default: echonotes)
  --incoming <folder>        Host folder mounted to /app/incoming
  --vault <folder>           Host folder mounted to /app/vault
  --config-dir <folder>      Host folder mounted to /app/config
  --model-cache-dir <folder> Optional host folder mounted to /app/model-cache
  --gpu                      Run with --gpus all
  --foreground               Run attached instead of detached
  --help                     Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --image-name)
            IMAGE_NAME="$2"
            shift 2
            ;;
        --container-name)
            CONTAINER_NAME="$2"
            shift 2
            ;;
        --incoming)
            INCOMING_DIR="$2"
            shift 2
            ;;
        --vault)
            VAULT_DIR="$2"
            shift 2
            ;;
        --config-dir)
            CONFIG_DIR="$2"
            shift 2
            ;;
        --model-cache-dir)
            MODEL_CACHE_DIR="$2"
            shift 2
            ;;
        --gpu)
            GPU=1
            shift
            ;;
        --foreground)
            DETACH=0
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage
            exit 1
            ;;
    esac
done

if [[ -z "${INCOMING_DIR:-}" || -z "${VAULT_DIR:-}" || -z "${CONFIG_DIR:-}" ]]; then
    echo "Missing required arguments." >&2
    usage
    exit 1
fi

for dir_var in INCOMING_DIR VAULT_DIR CONFIG_DIR; do
    dir_path="${!dir_var}"
    if [[ ! -d "$dir_path" ]]; then
        echo "Directory does not exist: $dir_path" >&2
        exit 1
    fi
done

RUN_CMD=(docker run --name "$CONTAINER_NAME")
if [[ "$DETACH" -eq 1 ]]; then
    RUN_CMD+=(-d)
else
    RUN_CMD+=(--rm)
fi
if [[ "$GPU" -eq 1 ]]; then
    RUN_CMD+=(--gpus all)
fi

RUN_CMD+=(
    -v "$INCOMING_DIR:/app/incoming"
    -v "$VAULT_DIR:/app/vault"
    -v "$CONFIG_DIR:/app/config"
)

if [[ -n "$MODEL_CACHE_DIR" ]]; then
    mkdir -p "$MODEL_CACHE_DIR"
    RUN_CMD+=(-v "$MODEL_CACHE_DIR:/app/model-cache")
fi

RUN_CMD+=("$IMAGE_NAME")

echo "Running Docker container $CONTAINER_NAME..."
"${RUN_CMD[@]}"
