#!/usr/bin/env bash
# Launch the local backend: Qwen3-4B (llama-server on :8080).
# Small + generally capable, so it runs on mid-range machines (not just 24GB+).
# First run downloads ~2.5 GB from Hugging Face and caches it in ~/.cache/huggingface.
exec llama-server \
  -hf bartowski/Qwen_Qwen3-4B-GGUF:Q4_K_M \
  --port 8080 \
  -c 8192 \
  --jinja
