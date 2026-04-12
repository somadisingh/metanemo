#!/bin/bash
# Start Nemotron inference server using local venv

set -e

VENV_PATH="${HOME}/pseudo-meta-glass-venv"
MODEL_PATH="${HOME}/models/llm/nvidia--NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4"
PORT=8000

echo "Activating virtual environment..."
source "${VENV_PATH}/bin/activate"

echo "Starting Nemotron inference server..."
echo "Model: ${MODEL_PATH}"
echo "Port: ${PORT}"

export MODEL_PATH="${MODEL_PATH}"
export MAX_NEW_TOKENS=256

cd "$(dirname "$0")"
python server.py
