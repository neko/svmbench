#!/usr/bin/env bash
set -euo pipefail

# OpenCode audit runner
# Uses Daydreams router with x402 payments (ERC-2612 permits on Base)
#
# Environment:
# - AGENT_DIR: directory containing audit/, submission/
# - SUBMISSION_DIR: output directory
# - LOGS_DIR: log directory
# - X402_PRIVATE_KEY: Base wallet private key for permit signing
# - OPENCODE_MODEL: model to use (default: anthropic:claude-sonnet-4-6)
# - AUDIT_TIMEOUT: max runtime in seconds (default: 600)

: "${AGENT_DIR:?missing AGENT_DIR}"
: "${SUBMISSION_DIR:?missing SUBMISSION_DIR}"
: "${LOGS_DIR:?missing LOGS_DIR}"
: "${X402_PRIVATE_KEY:?missing X402_PRIVATE_KEY}"

OPENCODE_MODEL="${OPENCODE_MODEL:-anthropic:claude-sonnet-4-6}"
AUDIT_TIMEOUT="${AUDIT_TIMEOUT:-600}"

mkdir -p "${SUBMISSION_DIR}" "${LOGS_DIR}"

# Configure OpenCode with Daydreams x402 router
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
        "${OPENCODE_MODEL}": { "name": "${OPENCODE_MODEL}" }
      }
    }
  }
}
EOF

# Copy audit instructions
cp "${AGENT_DIR}/AGENTS.md" "${AGENT_DIR}/AGENTS.md" 2>/dev/null || true

# Ensure clean output
rm -f "${SUBMISSION_DIR}/audit.md"

# Set x402 environment for plugin
export X402_PRIVATE_KEY
export X402_ROUTER_URL="https://ai.xgate.run"
export X402_PERMIT_CAP="5"  # $5 max spend per session

# Audit prompt
AUDIT_PROMPT='You are a Solana security auditor. Analyze the code in audit/ for HIGH severity vulnerabilities that could lead to loss of funds.

Read and understand the codebase first. Then identify any exploitable security issues.

Write your findings to submission/audit.md as JSON:
{
  "vulnerabilities": [
    {
      "title": "title",
      "severity": "high",
      "summary": "what the bug is",
      "description": [{"file": "path.rs", "line_start": N, "line_end": M, "desc": "details"}],
      "impact": "impact",
      "proof_of_concept": "exploit steps",
      "remediation": "fix"
    }
  ]
}

Only report HIGH severity issues. Valid JSON only.'

# Run OpenCode
cd "${AGENT_DIR}"
timeout --signal=TERM --kill-after=30s "${AUDIT_TIMEOUT}s" \
  opencode run \
    --model "${OPENCODE_MODEL}" \
    --provider x402 \
    --non-interactive \
    "${AUDIT_PROMPT}" \
  > "${LOGS_DIR}/opencode.log" 2>&1 || true

# Check output
if [[ ! -s "${SUBMISSION_DIR}/audit.md" ]]; then
  echo "No audit output generated" >&2
  # Create empty result
  echo '{"vulnerabilities":[]}' > "${SUBMISSION_DIR}/audit.md"
fi
