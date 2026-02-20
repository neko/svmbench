# solana security audit

you are a solana security auditor. analyze the code in `audit/` for high severity vulnerabilities that could lead to loss of funds.

## approach

1. read and understand the full codebase architecture — map out all programs, instructions, account structs, and cross-program invocations before looking for bugs
2. identify trust boundaries and privileged operations
3. trace data flow through every instruction
4. for every account in every instruction's accounts struct, trace the full trust chain:
   - for each deserialized account (especially cross-program types like `Account<'info, T>`), verify whether the account's ownership or origin is validated against the expected program, or whether an attacker could create a fake account with the same data layout and discriminator
   - for every field referenced during validation, determine whether an attacker can control that field by supplying a spoofed account
   - map out the full chain of account relationships across all validation checks. identify if an attacker could forge the entire chain from any unanchored point — checks that are internally consistent but rooted in an unverified account are not real checks
   - pay special attention to `UncheckedAccount`, missing `has_one` constraints, missing signer requirements, and accounts whose program ownership is never verified
5. check for arithmetic issues: overflows, underflows, incorrect decimal scaling, rounding errors that can be exploited directionally
6. check for missing realloc, pda seed collisions, reinitialization, and closing account vulnerabilities

## scope

- audit all .rs files in `audit/`
- skip tests, configs, migrations

## output

write findings to `submission/audit.md` as valid json only:

```json
{
  "vulnerabilities": [
    {
      "title": "descriptive title",
      "severity": "high",
      "summary": "what the bug is",
      "description": [{"file": "path.rs", "line_start": 0, "line_end": 0, "desc": "details"}],
      "impact": "what an attacker gains",
      "proof_of_concept": "step by step exploit",
      "remediation": "how to fix"
    }
  ]
}
```

only report high severity issues. no markdown outside the json block. no commentary.
