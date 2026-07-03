#!/usr/bin/env bash
# Launch Hermes-4-14B as the agent's local backend (llama-server on :8080).
# First run downloads ~9 GB from Hugging Face and caches it in ~/Library/Caches/llama.cpp.
exec llama-server \
  -hf bartowski/NousResearch_Hermes-4-14B-GGUF:Q4_K_M \
  --port 8080 \
  -c 8192 \
  --jinja
