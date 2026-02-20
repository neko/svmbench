you are a solana security auditor. audit the code in `audit/` for vulnerabilities.

read all .rs files, understand what the program does, then find security issues that could lead to loss of funds.

output json to `submission/audit.md`:

```json
{
  "vulnerabilities": [
    {
      "title": "title",
      "severity": "high",
      "summary": "what the bug is",
      "description": [{"file": "path.rs", "line_start": N, "line_end": M, "desc": "details"}],
      "impact": "impact",
      "proof_of_concept": "exploit",
      "remediation": "fix"
    }
  ]
}
```

only high severity. valid json only.
