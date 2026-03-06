#!/usr/bin/env bash

set -euo pipefail

IMAGE_NAME=""
MODEL_CACHE_DIR="$(pwd)/model-cache"
CONFIG_DIR="$(pwd)/config"
WARM_MODEL_CACHE=1
WARM_MODEL_CACHE_ONLY=0
NO_CACHE=0
IMAGE_VARIANT="cpu"
CUDA_TAG="cuda12.8"
GPU_BASE_IMAGE="nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04"
TORCH_INDEX_URL=""
TORCH_EXTRA_INDEX_URL=""
TORCH_PACKAGE_SPEC=""
TORCHAUDIO_PACKAGE_SPEC=""
TORCH_INSTALL_NO_DEPS="0"
TORCH_PYTHON_DEPS=""
WHISPER_MODEL_NAME=""

KNOWN_WHISPER_MODELS=(
    "tiny.en"
    "tiny"
    "base.en"
    "base"
    "small.en"
    "small"
    "medium.en"
    "medium"
    "large-v1"
    "large-v2"
    "large-v3"
    "large"
    "distil-large-v2"
    "distil-medium.en"
    "distil-small.en"
    "distil-large-v3"
    "distil-large-v3.5"
    "large-v3-turbo"
    "turbo"
)

list_known_whisper_models() {
    printf '%s\n' "${KNOWN_WHISPER_MODELS[@]}"
}

is_valid_whisper_model() {
    local candidate="$1"
    local model
    for model in "${KNOWN_WHISPER_MODELS[@]}"; do
        if [[ "$model" == "$candidate" ]]; then
            return 0
        fi
    done
    return 1
}

choose_whisper_model_interactively() {
    if [[ ! -t 0 ]]; then
        echo "No model specified and no interactive TTY is available." >&2
        echo "Use --model with one of:" >&2
        list_known_whisper_models >&2
        exit 1
    fi

    echo "Select a WhisperX model to warm:"
    select model in "${KNOWN_WHISPER_MODELS[@]}"; do
        if [[ -n "${model:-}" ]]; then
            WHISPER_MODEL_NAME="$model"
            break
        fi
        echo "Invalid selection." >&2
    done
}

run_model_cache_warmup() {
    local image_name="$1"
    local model_cache_dir="$2"
    local config_dir="$3"
    local image_variant="$4"
    local selected_model="$5"
    local run_cmd
    local warmup_python

    warmup_python=$(cat <<'PY'
import os
import sys

import torch
import whisperx
import yaml

sys.path.insert(0, "/app")

from main import get_default_whisper_model, whisperx_torch_load_compat

config = {}
config_path = "/app/config/config.yml"
if os.path.exists(config_path):
    with open(config_path, "r") as f:
        config = yaml.safe_load(f) or {}

device = "cuda" if torch.cuda.is_available() else "cpu"
compute_type = "float16" if device == "cuda" else "int8"
model_name = os.environ.get("ECHONOTES_WARM_MODEL") or config.get("whisper_model") or get_default_whisper_model()
print(f"Warming WhisperX cache for model={model_name} device={device} cache={os.environ.get('HF_HOME')}")

with whisperx_torch_load_compat():
    whisperx.load_model(model_name, device, compute_type=compute_type)
PY
)

    echo "Warming WhisperX cache..."

    run_cmd=(docker run --rm -v "$model_cache_dir:/app/model-cache")
    if [[ "$image_variant" == "gpu" ]]; then
        run_cmd+=(--gpus all)
    fi
    if [[ -d "$config_dir" ]]; then
        run_cmd+=(-v "$config_dir:/app/config:ro")
    fi
    if [[ -n "$selected_model" ]]; then
        run_cmd+=(-e "ECHONOTES_WARM_MODEL=$selected_model")
    fi

    run_cmd+=(
        "$image_name"
        python
        -c
        "$warmup_python"
    )

    "${run_cmd[@]}"

    echo "Model cache warmup complete."
}

usage() {
    cat <<'EOF'
Usage: ./build.sh [options]

Options:
  --image-name <name>        Docker image name to build (default: echonotes:latest or echonotes:latest-cuda12.8)
  --config-dir <path>        Config directory for reading config.yml during warmup (default: ./config)
  --model-cache-dir <path>   Host model cache directory mounted to /app/model-cache (default: ./model-cache)
  --model <name>             WhisperX model name to warm (defaults to config/default model; prompts in warm-only mode)
  --variant <cpu|gpu>        Build the CPU or GPU image variant (default: cpu)
  --gpu                      Shorthand for --variant gpu
  --cuda-tag <tag>           CUDA tag suffix used in default image naming (default: cuda12.8)
  --gpu-base-image <image>   Override the NVIDIA CUDA runtime image used for GPU builds
  --no-cache                 Build the Docker image with --no-cache
  --torch-index-url <url>    Override the primary PyTorch wheel index used during docker build
  --torch-extra-index-url <url> Override the extra PyTorch wheel index used during docker build
  --list-models              Print supported WhisperX model names and exit
  --warm-model-cache-only    Skip docker build and only warm /app/model-cache using an existing local image
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
        --model)
            WHISPER_MODEL_NAME="$2"
            shift 2
            ;;
        --variant)
            IMAGE_VARIANT="$2"
            shift 2
            ;;
        --gpu)
            IMAGE_VARIANT="gpu"
            shift
            ;;
        --cuda-tag)
            CUDA_TAG="$2"
            shift 2
            ;;
        --gpu-base-image)
            GPU_BASE_IMAGE="$2"
            shift 2
            ;;
        --no-cache)
            NO_CACHE=1
            shift
            ;;
        --torch-index-url)
            TORCH_INDEX_URL="$2"
            shift 2
            ;;
        --torch-extra-index-url)
            TORCH_EXTRA_INDEX_URL="$2"
            shift 2
            ;;
        --list-models)
            list_known_whisper_models
            exit 0
            ;;
        --warm-model-cache-only)
            WARM_MODEL_CACHE_ONLY=1
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

if [[ "$IMAGE_VARIANT" != "cpu" && "$IMAGE_VARIANT" != "gpu" ]]; then
    echo "Unsupported image variant: $IMAGE_VARIANT" >&2
    usage
    exit 1
fi

if [[ -n "$WHISPER_MODEL_NAME" ]] && ! is_valid_whisper_model "$WHISPER_MODEL_NAME"; then
    echo "Unsupported WhisperX model: $WHISPER_MODEL_NAME" >&2
    echo "Supported models:" >&2
    list_known_whisper_models >&2
    exit 1
fi

if [[ "$IMAGE_VARIANT" == "gpu" ]]; then
    DEFAULT_IMAGE_NAME="echonotes:latest-$CUDA_TAG"
    DEFAULT_TORCH_INDEX_URL="https://download.pytorch.org/whl/cu128"
    DEFAULT_TORCH_EXTRA_INDEX_URL=""
    DEFAULT_TORCH_PACKAGE_SPEC="torch==2.8.0"
    DEFAULT_TORCHAUDIO_PACKAGE_SPEC="torchaudio==2.8.0"
    DEFAULT_TORCH_INSTALL_NO_DEPS="1"
    DEFAULT_TORCH_PYTHON_DEPS="filelock,fsspec,jinja2,markupsafe,mpmath,networkx,sympy,typing-extensions"
else
    DEFAULT_IMAGE_NAME="echonotes:latest"
    DEFAULT_TORCH_INDEX_URL="https://pypi.org/simple"
    DEFAULT_TORCH_EXTRA_INDEX_URL="https://download.pytorch.org/whl/cpu"
    DEFAULT_TORCH_PACKAGE_SPEC="torch==2.8.0+cpu"
    DEFAULT_TORCHAUDIO_PACKAGE_SPEC="torchaudio==2.8.0+cpu"
    DEFAULT_TORCH_INSTALL_NO_DEPS="0"
    DEFAULT_TORCH_PYTHON_DEPS=""
fi

IMAGE_NAME="${IMAGE_NAME:-$DEFAULT_IMAGE_NAME}"
TORCH_INDEX_URL="${TORCH_INDEX_URL:-$DEFAULT_TORCH_INDEX_URL}"
TORCH_EXTRA_INDEX_URL="${TORCH_EXTRA_INDEX_URL:-$DEFAULT_TORCH_EXTRA_INDEX_URL}"
TORCH_PACKAGE_SPEC="${TORCH_PACKAGE_SPEC:-$DEFAULT_TORCH_PACKAGE_SPEC}"
TORCHAUDIO_PACKAGE_SPEC="${TORCHAUDIO_PACKAGE_SPEC:-$DEFAULT_TORCHAUDIO_PACKAGE_SPEC}"
TORCH_INSTALL_NO_DEPS="${TORCH_INSTALL_NO_DEPS:-$DEFAULT_TORCH_INSTALL_NO_DEPS}"
TORCH_PYTHON_DEPS="${TORCH_PYTHON_DEPS:-$DEFAULT_TORCH_PYTHON_DEPS}"

mkdir -p "$MODEL_CACHE_DIR"

if [[ "$WARM_MODEL_CACHE_ONLY" -eq 1 ]]; then
    if [[ -z "$WHISPER_MODEL_NAME" ]]; then
        choose_whisper_model_interactively
    fi

    if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
        echo "Docker image not found locally: $IMAGE_NAME" >&2
        echo "Build it first or pass --image-name for an existing local tag." >&2
        exit 1
    fi

    run_model_cache_warmup "$IMAGE_NAME" "$MODEL_CACHE_DIR" "$CONFIG_DIR" "$IMAGE_VARIANT" "$WHISPER_MODEL_NAME"
    exit 0
fi

BUILD_CMD=(
    docker build
    --build-arg "IMAGE_VARIANT=$IMAGE_VARIANT"
    --build-arg "GPU_BASE_IMAGE=$GPU_BASE_IMAGE"
    --build-arg "TORCH_INDEX_URL=$TORCH_INDEX_URL"
    --build-arg "TORCH_EXTRA_INDEX_URL=$TORCH_EXTRA_INDEX_URL"
    --build-arg "TORCH_PACKAGE_SPEC=$TORCH_PACKAGE_SPEC"
    --build-arg "TORCHAUDIO_PACKAGE_SPEC=$TORCHAUDIO_PACKAGE_SPEC"
    --build-arg "TORCH_INSTALL_NO_DEPS=$TORCH_INSTALL_NO_DEPS"
    --build-arg "TORCH_PYTHON_DEPS=$TORCH_PYTHON_DEPS"
    -t "$IMAGE_NAME"
)
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

if [[ -z "$WHISPER_MODEL_NAME" ]] && find "$MODEL_CACHE_DIR" -mindepth 1 -print -quit | grep -q .; then
    echo "Model cache directory is already populated; skipping warmup."
    exit 0
fi

run_model_cache_warmup "$IMAGE_NAME" "$MODEL_CACHE_DIR" "$CONFIG_DIR" "$IMAGE_VARIANT" "$WHISPER_MODEL_NAME"
