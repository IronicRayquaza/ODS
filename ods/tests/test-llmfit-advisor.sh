#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURES_DIR="$ROOT_DIR/tests/fixtures/llmfit"

# Source the advisor with SCRIPT_DIR set so model map path resolves
SCRIPT_DIR="$ROOT_DIR/scripts"
source "$ROOT_DIR/scripts/llmfit-advisor.sh"

_assert_eq() {
    local got="$1" expected="$2" label="$3"
    if [ "$got" != "$expected" ]; then
        echo "[FAIL] ${label}: expected '${expected}', got '${got}'"
        exit 1
    fi
    echo "[PASS] ${label}"
}

# Test each hardware fixture maps to expected ODS GGUF + tier

# NVIDIA 24GB → T3 (14B model fits)
_parse_llmfit_output "$(cat "$FIXTURES_DIR/nvidia-24gb.json")"
_assert_eq "$LLMFIT_ODS_GGUF" "Qwen3-14B-Q4_K_M.gguf" "nvidia-24gb maps to T3 GGUF"
_assert_eq "$LLMFIT_ODS_TIER" "T3"                      "nvidia-24gb maps to T3 tier"

# NVIDIA 8GB → T2 (8B model)
_parse_llmfit_output "$(cat "$FIXTURES_DIR/nvidia-8gb.json")"
_assert_eq "$LLMFIT_ODS_GGUF" "Qwen3-8B-Q4_K_M.gguf"  "nvidia-8gb maps to T2 GGUF"
_assert_eq "$LLMFIT_ODS_TIER" "T2"                      "nvidia-8gb maps to T2 tier"

# AMD Strix Halo → T3 or T4 depending on fixture
_parse_llmfit_output "$(cat "$FIXTURES_DIR/amd-strix-halo.json")"
_assert_eq "$LLMFIT_ODS_TIER" "T3" "strix-halo maps to T3 tier"

# Apple M3 16GB → T2
_parse_llmfit_output "$(cat "$FIXTURES_DIR/apple-m3-16gb.json")"
_assert_eq "$LLMFIT_ODS_TIER" "T2" "apple-m3-16gb maps to T2 tier"

# CPU only → T1
_parse_llmfit_output "$(cat "$FIXTURES_DIR/cpu-only.json")"
_assert_eq "$LLMFIT_ODS_GGUF" "Qwen3-1.7B-Q4_K_M.gguf" "cpu-only maps to T1 GGUF"
_assert_eq "$LLMFIT_ODS_TIER" "T1"                       "cpu-only maps to T1 tier"

# Malformed JSON → returns 1, no crash
_parse_llmfit_output '{"broken":' && {
    echo "[FAIL] malformed JSON should return 1"
    exit 1
} || echo "[PASS] malformed JSON returns 1 cleanly"

# Empty recommendations → returns 1, no crash
_parse_llmfit_output '{"recommendations":[]}' && {
    echo "[FAIL] empty recommendations should return 1"
    exit 1
} || echo "[PASS] empty recommendations returns 1 cleanly"

# Unmapped model → returns 1, falls back
_parse_llmfit_output '{"recommendations":[{"model_id":"unknown/model","quantization":"Q4_K_M","estimated_tokens_per_sec":10}]}' && {
    echo "[FAIL] unmapped model should return 1"
    exit 1
} || echo "[PASS] unmapped model returns 1 cleanly"

echo ""
echo "[PASS] All llmfit-advisor tests passed ($(ls "$FIXTURES_DIR"/*.json | wc -l) fixtures)"
