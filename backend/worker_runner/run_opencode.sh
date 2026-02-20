#!/usr/bin/env bash
set -euo pipefail

# OpenCode audit runner - uses AGENTS.md for instructions

: "${AGENT_DIR:?missing AGENT_DIR}"
: "${SUBMISSION_DIR:?missing SUBMISSION_DIR}"
: "${LOGS_DIR:?missing LOGS_DIR}"
: "${X402_PRIVATE_KEY:?missing X402_PRIVATE_KEY}"

OPENCODE_MODEL="${OPENCODE_MODEL:-anthropic:claude-sonnet-4-6}"
AUDIT_TIMEOUT="${AUDIT_TIMEOUT:-900}"

# Convert model format from provider:model to provider/model
OPENCODE_MODEL_FORMATTED="${OPENCODE_MODEL/://}"

mkdir -p "${SUBMISSION_DIR}" "${LOGS_DIR}"

# Configure OpenCode with x402 router
CONFIG_DIR="${AGENT_DIR}/.opencode"
mkdir -p "${CONFIG_DIR}"

cat > "${CONFIG_DIR}/config.json" << EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "plugin": ["@lucid-agents/opencode-x402-plugin"],
  "provider": {
    "x402": {
      "npm": "@ai-sdk/anthropic",
      "name": "x402 Router",
      "options": {
        "baseURL": "https://ai.xgate.run/v1"
      },
      "models": {
        "${OPENCODE_MODEL_FORMATTED}": { "name": "${OPENCODE_MODEL_FORMATTED}" }
      }
    }
  }
}
EOF

# Set x402 environment
export X402_PRIVATE_KEY
export X402_ROUTER_URL="https://ai.xgate.run"
export X402_PERMIT_CAP="50"

rm -f "${SUBMISSION_DIR}/audit.md"
cd "${AGENT_DIR}"

# Run OpenCode with the run subcommand
# It reads AGENTS.md automatically for instructions
timeout --signal=TERM --kill-after=30s "${AUDIT_TIMEOUT}s" \
  opencode run -m "${OPENCODE_MODEL_FORMATTED}" "Follow the instructions in AGENTS.md to audit the code in audit/ and write results to submission/audit.md" \
  > "${LOGS_DIR}/opencode.log" 2>&1 || true

if [[ ! -s "${SUBMISSION_DIR}/audit.md" ]]; then
  echo '{"vulnerabilities":[]}' > "${SUBMISSION_DIR}/audit.md"
fi
