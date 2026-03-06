ARG CPU_BASE_IMAGE=python:3.10-slim
ARG GPU_BASE_IMAGE=nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04
ARG GPU_CUDA_DEVEL_IMAGE=nvidia/cuda:12.8.1-cudnn-devel-ubuntu22.04
ARG IMAGE_VARIANT=cpu

FROM ${CPU_BASE_IMAGE} AS cpu-base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    HF_HOME=/app/model-cache/hf \
    XDG_CACHE_HOME=/app/model-cache/xdg

WORKDIR /app

RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    poppler-utils \
    tesseract-ocr \
    tini \
    && rm -rf /var/lib/apt/lists/*

FROM ${GPU_BASE_IMAGE} AS gpu-base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    HF_HOME=/app/model-cache/hf \
    XDG_CACHE_HOME=/app/model-cache/xdg \
    LD_LIBRARY_PATH=/usr/local/nvidia/lib:/usr/local/nvidia/lib64

WORKDIR /app

RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    ffmpeg \
    poppler-utils \
    python3 \
    python3-pip \
    python3-venv \
    tesseract-ocr \
    tini \
    && ln -sf /usr/bin/python3 /usr/local/bin/python \
    && ln -sf /usr/bin/pip3 /usr/local/bin/pip \
    && rm -rf /var/lib/apt/lists/*

FROM ${GPU_CUDA_DEVEL_IMAGE} AS gpu-cuda-devel

FROM cpu-base AS cpu-runtime-base

FROM gpu-base AS gpu-runtime-base

COPY --from=gpu-cuda-devel /usr/local/cuda/compat /usr/local/cuda/compat
COPY --from=gpu-cuda-devel /usr/local/cuda/targets /usr/local/cuda/targets

FROM ${IMAGE_VARIANT}-runtime-base AS runtime

ARG TORCH_INDEX_URL=https://pypi.org/simple
ARG TORCH_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG TORCH_PACKAGE_SPEC=torch==2.8.0+cpu
ARG TORCHAUDIO_PACKAGE_SPEC=torchaudio==2.8.0+cpu
ARG TORCH_INSTALL_NO_DEPS=0
ARG TORCH_PYTHON_DEPS=

COPY requirements.txt /app/requirements.txt
RUN if [ -n "${TORCH_PYTHON_DEPS}" ]; then \
        pip install --index-url https://pypi.org/simple $(printf '%s' "${TORCH_PYTHON_DEPS}" | tr ',' ' '); \
    fi
RUN if [ "${TORCH_INSTALL_NO_DEPS}" = "1" ]; then \
        TORCH_INSTALL_ARGS="--no-deps"; \
    else \
        TORCH_INSTALL_ARGS=""; \
    fi; \
    if [ -n "${TORCH_EXTRA_INDEX_URL}" ]; then \
        pip install ${TORCH_INSTALL_ARGS} --index-url "${TORCH_INDEX_URL}" --extra-index-url "${TORCH_EXTRA_INDEX_URL}" "${TORCH_PACKAGE_SPEC}" "${TORCHAUDIO_PACKAGE_SPEC}"; \
    else \
        pip install ${TORCH_INSTALL_ARGS} --index-url "${TORCH_INDEX_URL}" "${TORCH_PACKAGE_SPEC}" "${TORCHAUDIO_PACKAGE_SPEC}"; \
    fi
RUN pip install -r /app/requirements.txt

RUN mkdir -p /app/config-defaults /app/incoming /app/vault /app/config /app/model-cache/hf /app/model-cache/xdg

COPY main.py README.md /app/
COPY config.sample.yml /app/config-defaults/config.yml
COPY summarize-notes.md /app/config-defaults/summarize-notes.md

VOLUME ["/app/incoming", "/app/vault", "/app/config", "/app/model-cache"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "/app/main.py"]
