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

# Configure OpenCode with OpenRouter
CONFIG_DIR="${AGENT_DIR}/.opencode"
mkdir -p "${CONFIG_DIR}"

cat > "${CONFIG_DIR}/config.json" << EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "openrouter": {
      "npm": "@openrouter/ai-sdk-provider",
      "name": "OpenRouter",
      "options": {
        "apiKey": "${OPENROUTER_API_KEY}"
      },
      "models": {
        "${OPENCODE_MODEL}": { "name": "${OPENCODE_MODEL}" }
      }
    }
  }
}
EOF

rm -f "${SUBMISSION_DIR}/audit.md"
cd "${AGENT_DIR}"

# Run OpenCode with the run subcommand
# It reads AGENTS.md automatically for instructions
timeout --signal=TERM --kill-after=30s "${AUDIT_TIMEOUT}s" \
  opencode run -m "openrouter:${OPENCODE_MODEL}" "Follow the instructions in AGENTS.md to audit the code in audit/ and write results to submission/audit.md" \
  > "${LOGS_DIR}/opencode.log" 2>&1 || true

if [[ ! -s "${SUBMISSION_DIR}/audit.md" ]]; then
  echo '{"vulnerabilities":[]}' > "${SUBMISSION_DIR}/audit.md"
fi
