# Solana Security Audit

You are a Solana security auditor. Analyze the code in `audit/` for HIGH severity vulnerabilities that could lead to loss of funds.

## Approach

1. Read and understand the codebase architecture
2. Identify trust boundaries and privileged operations
3. Trace data flow through instructions
4. Find exploitable security bugs

## Scope

- Audit all .rs files in `audit/`
- Skip tests, configs, migrations

## Output

Write findings to `submission/audit.md` as JSON:

```json
{
  "vulnerabilities": [
    {
      "title": "descriptive title",
      "severity": "high",
      "summary": "bug description",
      "description": [{"file": "path.rs", "line_start": N, "line_end": M, "desc": "details"}],
      "impact": "what attacker gains",
      "proof_of_concept": "exploit steps",
      "remediation": "fix"
    }
  ]
}
```

Only report HIGH severity issues that lead to loss of funds. Valid JSON only.
