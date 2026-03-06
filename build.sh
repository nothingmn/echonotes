#!/usr/bin/env bash

set -euo pipefail

IMAGE_NAME="echonotes:latest"
MODEL_CACHE_DIR="$(pwd)/model-cache"
CONFIG_DIR="$(pwd)/config"
WARM_MODEL_CACHE=1
NO_CACHE=0
GPU_WARMUP=0

usage() {
    cat <<'EOF'
Usage: ./build.sh [options]

Options:
  --image-name <name>        Docker image name to build (default: echonotes:latest)
  --config-dir <path>        Config directory for reading config.yml during warmup (default: ./config)
  --model-cache-dir <path>   Host model cache directory mounted to /app/model-cache (default: ./model-cache)
  --gpu                      Warm the model cache with `--gpus all`
  --no-cache                 Build the Docker image with --no-cache
  --skip-warm-model-cache    Build the image but skip model cache warmup
  --help                     Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --image-name)
            IMAGE_NAME="$2"
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
            GPU_WARMUP=1
            shift
            ;;
        --no-cache)
            NO_CACHE=1
            shift
            ;;
        --skip-warm-model-cache)
            WARM_MODEL_CACHE=0
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

mkdir -p "$MODEL_CACHE_DIR"

BUILD_CMD=(docker build -t "$IMAGE_NAME")
if [[ "$NO_CACHE" -eq 1 ]]; then
    BUILD_CMD+=(--no-cache)
fi
BUILD_CMD+=(.)

echo "Building Docker image $IMAGE_NAME..."
"${BUILD_CMD[@]}"

if [[ "$WARM_MODEL_CACHE" -ne 1 ]]; then
    echo "Skipping model cache warmup."
    exit 0
fi

if find "$MODEL_CACHE_DIR" -mindepth 1 -print -quit | grep -q .; then
    echo "Model cache directory is already populated; skipping warmup."
    exit 0
fi

echo "Model cache directory is empty; warming WhisperX cache..."

RUN_CMD=(docker run --rm -v "$MODEL_CACHE_DIR:/app/model-cache")
if [[ "$GPU_WARMUP" -eq 1 ]]; then
    RUN_CMD+=(--gpus all)
fi
if [[ -d "$CONFIG_DIR" ]]; then
    RUN_CMD+=(-v "$CONFIG_DIR:/app/config:ro")
fi

RUN_CMD+=(
    "$IMAGE_NAME"
    python
    -c
    "import os, sys, torch, whisperx, yaml; \
sys.path.insert(0, '/app'); \
from main import get_default_whisper_model, whisperx_torch_load_compat; \
config = {}; \
config_path = '/app/config/config.yml'; \
if os.path.exists(config_path): \
    with open(config_path, 'r') as f: \
        config = yaml.safe_load(f) or {}; \
device = 'cuda' if torch.cuda.is_available() else 'cpu'; \
compute_type = 'float16' if device == 'cuda' else 'int8'; \
default_model = get_default_whisper_model(); \
model_name = config.get('whisper_model') or default_model; \
print(f'Warming WhisperX cache for model={model_name} device={device} cache={os.environ.get(\"HF_HOME\")}'); \
with whisperx_torch_load_compat(): whisperx.load_model(model_name, device, compute_type=compute_type)"
)

"${RUN_CMD[@]}"

echo "Model cache warmup complete."
