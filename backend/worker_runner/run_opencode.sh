#!/usr/bin/env bash
set -euo pipefail

# OpenCode audit runner - uses AGENTS.md for instructions

: "${AGENT_DIR:?missing AGENT_DIR}"
: "${SUBMISSION_DIR:?missing SUBMISSION_DIR}"
: "${LOGS_DIR:?missing LOGS_DIR}"
: "${OPENROUTER_API_KEY:?missing OPENROUTER_API_KEY}"

OPENCODE_MODEL="${OPENCODE_MODEL:-anthropic/claude-sonnet-4}"
AUDIT_TIMEOUT="${AUDIT_TIMEOUT:-900}"

mkdir -p "${SUBMISSION_DIR}" "${LOGS_DIR}"

# Use OpenRouter via OpenAI-compatible interface
# OpenRouter is OpenAI-compatible, so we use openai provider with custom base URL
export OPENAI_API_KEY="${OPENROUTER_API_KEY}"
export OPENAI_BASE_URL="https://openrouter.ai/api/v1"

rm -f "${SUBMISSION_DIR}/audit.md"
cd "${AGENT_DIR}"

# Run OpenCode with the run subcommand
# Use openai provider since OpenRouter is OpenAI-compatible
timeout --signal=TERM --kill-after=30s "${AUDIT_TIMEOUT}s" \
  opencode run -m "openai/${OPENCODE_MODEL}" "Follow the instructions in AGENTS.md to audit the code in audit/ and write results to submission/audit.md" \
  > "${LOGS_DIR}/opencode.log" 2>&1 || true

if [[ ! -s "${SUBMISSION_DIR}/audit.md" ]]; then
  echo '{"vulnerabilities":[]}' > "${SUBMISSION_DIR}/audit.md"
fi
