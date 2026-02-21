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

rm -f "${SUBMISSION_DIR}/audit.md"
cd "${AGENT_DIR}"

# Run OpenCode with openrouter provider - uses OPENROUTER_API_KEY directly
timeout --signal=TERM --kill-after=30s "${AUDIT_TIMEOUT}s" \
  opencode run -m "openrouter/${OPENCODE_MODEL}" "Follow the instructions in AGENTS.md to audit the code in audit/ and write results to submission/audit.md" \
  > "${LOGS_DIR}/opencode.log" 2>&1 || true

if [[ ! -s "${SUBMISSION_DIR}/audit.md" ]]; then
  echo '{"vulnerabilities":[]}' > "${SUBMISSION_DIR}/audit.md"
fi
