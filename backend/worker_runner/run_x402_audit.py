#!/usr/bin/env python3
"""
Multi-phase audit using x402 or direct API calls.

Effort levels:
- low: 2 phases (file selection + basic scan)
- medium: 3 phases (+ deep dive on critical areas)
- high: 5+ phases (multiple passes, verification, cross-referencing)

This allows scaling analysis depth based on user preferences.
"""

import json
import os
import sys
from pathlib import Path

import httpx

AGENT_DIR = os.environ.get('AGENT_DIR')
SUBMISSION_DIR = os.environ.get('SUBMISSION_DIR')
LOGS_DIR = os.environ.get('LOGS_DIR')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
OPENAI_BASE_URL = os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1')
CODEX_MODEL = os.environ.get('CODEX_MODEL', 'gpt-4o')
AUDIT_EFFORT = os.environ.get('AUDIT_EFFORT', 'medium').lower()

AUDIT_DIR = Path(AGENT_DIR) / 'audit' if AGENT_DIR else None
MAX_FILE_SIZE = 100_000  # 100KB max per file
MAX_TOTAL_SIZE = 500_000  # 500KB total

# Effort-based configuration
EFFORT_CONFIG = {
    'low': {
        'max_tokens': 4096,
        'phases': ['file_select', 'basic_scan'],
    },
    'medium': {
        'max_tokens': 8192,
        'phases': ['file_select', 'thorough_scan', 'deep_dive'],
    },
    'high': {
        'max_tokens': 16384,
        'phases': ['file_select', 'thorough_scan', 'access_control', 'state_handling', 'arithmetic', 'deep_dive', 'verify'],
    },
}

VULNERABILITY_CLASSES = {
    'access_control': [
        'missing signer checks',
        'missing owner validation',
        'unauthorized cpi calls',
        'privilege escalation',
    ],
    'state_handling': [
        'account data matching issues',
        'type cosplay / missing discriminator',
        'reinitialization attacks',
        'improper account closing',
        'missing rent-exemption checks',
    ],
    'arithmetic': [
        'integer overflow/underflow',
        'precision loss in calculations',
        'fee calculation errors',
    ],
}


def get_file_tree(directory: Path, prefix: str = '') -> list[str]:
    """Get tree structure of directory."""
    files = []
    try:
        items = sorted(directory.iterdir())
    except PermissionError:
        return files

    for item in items:
        if item.name.startswith('.'):
            continue
        if item.is_dir():
            files.append(f'{prefix}{item.name}/')
            files.extend(get_file_tree(item, prefix + '  '))
        else:
            size = item.stat().st_size
            files.append(f'{prefix}{item.name} ({size} bytes)')
    return files


def get_rust_files(directory: Path) -> list[Path]:
    """Get all .rs files in directory."""
    return list(directory.rglob('*.rs'))


def read_file_safe(path: Path) -> str | None:
    """Read file safely with size limit."""
    try:
        if path.stat().st_size > MAX_FILE_SIZE:
            return f'[FILE TOO LARGE: {path.stat().st_size} bytes]'
        return path.read_text(encoding='utf-8', errors='replace')
    except Exception as e:
        return f'[ERROR READING FILE: {e}]'


def call_llm(messages: list[dict], model: str, max_tokens: int = 8192) -> str:
    """Make LLM API call."""
    with httpx.Client(timeout=180.0) as client:
        response = client.post(
            f'{OPENAI_BASE_URL}/chat/completions',
            headers={
                'Authorization': f'Bearer {OPENAI_API_KEY}',
                'Content-Type': 'application/json',
            },
            json={
                'model': model,
                'messages': messages,
                'max_tokens': max_tokens,
            },
        )

        if response.status_code == 402:
            raise Exception('Payment required - x402 proxy should handle this')

        response.raise_for_status()
        data = response.json()
        return data['choices'][0]['message']['content']


def phase_file_select(file_tree: str, rust_files: list[str], effort: str) -> list[str]:
    """Phase 1: Ask AI which files to analyze."""

    if effort == 'high':
        prompt = f"""you are a senior solana security auditor preparing for a comprehensive vulnerability assessment.

codebase structure:
```
{file_tree}
```

rust files available:
{chr(10).join(f'- {f}' for f in rust_files)}

select ALL files that could contain security-relevant logic. be thorough - it's better to include too many than miss a critical file. include:
- all instruction handlers and entry points
- state/account definitions
- any utility functions that handle accounts or tokens
- cpi-related code
- any custom validation logic

skip only: tests, build configs, migrations, documentation.

respond with ONLY a json array of file paths:
["path/to/file.rs", ...]
"""
    else:
        prompt = f"""you are auditing a solana program for vulnerabilities.

codebase structure:
```
{file_tree}
```

rust files available:
{chr(10).join(f'- {f}' for f in rust_files)}

reply with ONLY a json array of filenames you want to read for the audit.
focus on: program logic, instruction handlers, state management.
skip: tests, configs, build artifacts.

example: ["programs/vault/src/lib.rs", "programs/vault/src/state.rs"]
"""

    config = EFFORT_CONFIG.get(effort, EFFORT_CONFIG['medium'])
    messages = [{'role': 'user', 'content': prompt}]
    response = call_llm(messages, CODEX_MODEL, max_tokens=2048)

    try:
        if '```' in response:
            start = response.find('[')
            end = response.rfind(']') + 1
            response = response[start:end]
        return json.loads(response)
    except json.JSONDecodeError:
        return rust_files


def phase_basic_scan(file_contents: dict[str, str], effort: str) -> dict:
    """Basic vulnerability scan (low effort)."""
    files_text = '\n\n'.join(
        f'=== {path} ===\n```rust\n{content}\n```'
        for path, content in file_contents.items()
    )

    prompt = f"""analyze these solana program files for HIGH severity vulnerabilities that could lead to loss of funds.

{files_text}

check for:
- missing signer/owner checks
- account validation issues
- integer overflow/underflow
- improper account closing
- reinitialization vulnerabilities

respond with ONLY valid json:
{{
  "vulnerabilities": [
    {{
      "title": "vulnerability title",
      "severity": "high",
      "summary": "precise summary",
      "description": [{{"file": "path.rs", "line_start": 10, "line_end": 20, "desc": "issue"}}],
      "impact": "how funds are lost",
      "proof_of_concept": "attack steps",
      "remediation": "fix"
    }}
  ]
}}

if no vulnerabilities found: {{"vulnerabilities": []}}
"""

    messages = [{'role': 'user', 'content': prompt}]
    response = call_llm(messages, CODEX_MODEL, max_tokens=4096)
    return _parse_vuln_response(response)


def phase_thorough_scan(file_contents: dict[str, str], effort: str) -> dict:
    """Thorough vulnerability scan (medium/high effort)."""
    files_text = '\n\n'.join(
        f'=== {path} ===\n```rust\n{content}\n```'
        for path, content in file_contents.items()
    )

    prompt = f"""you are a senior solana security auditor. perform a THOROUGH analysis of these files for HIGH severity vulnerabilities.

{files_text}

IMPORTANT: take your time and examine every function carefully. look for subtle bugs that could be exploited.

vulnerability classes to check thoroughly:
1. ACCESS CONTROL
   - missing signer checks on privileged operations
   - incorrect owner validation
   - pda authority confusion

2. ACCOUNT VALIDATION
   - type cosplay (accounts misidentified due to missing discriminators)
   - pda seed collisions
   - account substitution attacks

3. STATE CORRUPTION
   - reinitialization of existing accounts
   - improper account closing (data left accessible)
   - missing rent-exemption checks

4. ARITHMETIC
   - integer overflow/underflow in token amounts
   - precision loss in fee/reward calculations
   - division by zero

5. CPI SECURITY
   - unauthorized cross-program invocations
   - missing privilege checks on cpi calls
   - arbitrary program invocation

for each vulnerability:
- trace the exact code path
- explain how an attacker exploits it
- quantify the potential loss

respond with ONLY valid json (no markdown fences, no extra text):
{{
  "vulnerabilities": [
    {{
      "title": "descriptive title",
      "severity": "high",
      "summary": "concise technical summary",
      "description": [{{"file": "path.rs", "line_start": N, "line_end": M, "desc": "detailed analysis"}}],
      "impact": "specific impact on funds",
      "proof_of_concept": "step by step exploit",
      "remediation": "concrete fix"
    }}
  ]
}}

if no HIGH severity issues found: {{"vulnerabilities": []}}
remember: only report issues that lead to LOSS OF FUNDS. be thorough but avoid false positives.
"""

    config = EFFORT_CONFIG.get(effort, EFFORT_CONFIG['medium'])
    messages = [{'role': 'user', 'content': prompt}]
    response = call_llm(messages, CODEX_MODEL, max_tokens=config['max_tokens'])
    return _parse_vuln_response(response)


def phase_focused_scan(file_contents: dict[str, str], focus_area: str, vuln_classes: list[str]) -> dict:
    """Focused scan on specific vulnerability class (high effort)."""
    files_text = '\n\n'.join(
        f'=== {path} ===\n```rust\n{content}\n```'
        for path, content in file_contents.items()
    )

    prompt = f"""you are a specialist in {focus_area} vulnerabilities in solana programs.

{files_text}

YOUR SOLE FOCUS: {focus_area.upper()} vulnerabilities

specifically look for:
{chr(10).join(f'- {vc}' for vc in vuln_classes)}

examine EVERY function that could have these issues. trace data flow. check all code paths.

respond with ONLY valid json:
{{
  "vulnerabilities": [
    {{
      "title": "title",
      "severity": "high",
      "summary": "summary",
      "description": [{{"file": "path.rs", "line_start": N, "line_end": M, "desc": "analysis"}}],
      "impact": "funds impact",
      "proof_of_concept": "exploit",
      "remediation": "fix"
    }}
  ]
}}

if no {focus_area} vulnerabilities: {{"vulnerabilities": []}}
only HIGH severity (loss of funds).
"""

    messages = [{'role': 'user', 'content': prompt}]
    response = call_llm(messages, CODEX_MODEL, max_tokens=8192)
    return _parse_vuln_response(response)


def phase_deep_dive(file_contents: dict[str, str], existing_vulns: list[dict], effort: str) -> dict:
    """Deep dive on suspicious areas identified earlier."""
    files_text = '\n\n'.join(
        f'=== {path} ===\n```rust\n{content}\n```'
        for path, content in file_contents.items()
    )

    if existing_vulns:
        vuln_summary = '\n'.join(f'- {v.get("title", "unknown")}: {v.get("summary", "")}' for v in existing_vulns[:5])
        context = f"""
previously identified potential issues:
{vuln_summary}

now look DEEPER. are there related vulnerabilities? did we miss anything in the same functions?
"""
    else:
        context = """
initial scan found no obvious issues. now dig deeper:
- look at edge cases
- trace complex data flows
- check for subtle logic errors
- examine function interactions
"""

    prompt = f"""you are performing a DEEP DIVE security analysis.

{files_text}

{context}

specifically:
1. re-examine all instruction handlers
2. check every account constraint
3. trace token/sol transfers end-to-end
4. look for logic bugs that aren't obvious at first glance
5. check for race conditions or reentrancy

find what others might miss. be thorough.

respond with ONLY valid json:
{{
  "vulnerabilities": [
    {{
      "title": "title",
      "severity": "high",
      "summary": "summary",
      "description": [{{"file": "path.rs", "line_start": N, "line_end": M, "desc": "analysis"}}],
      "impact": "impact",
      "proof_of_concept": "exploit",
      "remediation": "fix"
    }}
  ]
}}

if no additional issues: {{"vulnerabilities": []}}
"""

    config = EFFORT_CONFIG.get(effort, EFFORT_CONFIG['medium'])
    messages = [{'role': 'user', 'content': prompt}]
    response = call_llm(messages, CODEX_MODEL, max_tokens=config['max_tokens'])
    return _parse_vuln_response(response)


def phase_verify(file_contents: dict[str, str], all_vulns: list[dict]) -> dict:
    """Verify and deduplicate findings (high effort)."""
    if not all_vulns:
        return {'vulnerabilities': []}

    files_text = '\n\n'.join(
        f'=== {path} ===\n```rust\n{content}\n```'
        for path, content in file_contents.items()
    )

    vulns_text = json.dumps(all_vulns, indent=2)

    prompt = f"""you are performing FINAL VERIFICATION of security findings.

{files_text}

candidate vulnerabilities found:
{vulns_text}

your task:
1. VERIFY each finding - is it actually exploitable? check the code again.
2. REMOVE false positives - if protected elsewhere or not actually vulnerable
3. DEDUPLICATE - merge findings that describe the same issue
4. REFINE - improve descriptions, add missing details

return the VERIFIED list:
{{
  "vulnerabilities": [
    {{
      "title": "title",
      "severity": "high",
      "summary": "summary",
      "description": [{{"file": "path.rs", "line_start": N, "line_end": M, "desc": "analysis"}}],
      "impact": "impact",
      "proof_of_concept": "exploit",
      "remediation": "fix"
    }}
  ]
}}

remove any finding that isn't definitively a HIGH severity loss-of-funds issue.
"""

    messages = [{'role': 'user', 'content': prompt}]
    response = call_llm(messages, CODEX_MODEL, max_tokens=16384)
    return _parse_vuln_response(response)


def _parse_vuln_response(response: str) -> dict:
    """Parse vulnerability JSON from response."""
    try:
        # Try direct parse
        return json.loads(response)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown
    if '```' in response:
        try:
            start = response.find('{')
            end = response.rfind('}') + 1
            if start >= 0 and end > start:
                return json.loads(response[start:end])
        except json.JSONDecodeError:
            pass

    # Fallback
    return {'vulnerabilities': []}


def merge_vulnerabilities(results: list[dict]) -> list[dict]:
    """Merge vulnerability lists, deduplicating by title."""
    seen_titles = set()
    merged = []

    for result in results:
        for vuln in result.get('vulnerabilities', []):
            title = vuln.get('title', '').lower().strip()
            if title and title not in seen_titles:
                seen_titles.add(title)
                merged.append(vuln)

    return merged


def main():
    if not all([AGENT_DIR, SUBMISSION_DIR, LOGS_DIR, OPENAI_API_KEY]):
        print('missing required environment variables', file=sys.stderr)
        sys.exit(1)

    audit_path = Path(AGENT_DIR) / 'audit'
    submission_path = Path(SUBMISSION_DIR)
    logs_path = Path(LOGS_DIR)

    submission_path.mkdir(parents=True, exist_ok=True)
    logs_path.mkdir(parents=True, exist_ok=True)

    effort = AUDIT_EFFORT
    config = EFFORT_CONFIG.get(effort, EFFORT_CONFIG['medium'])
    phases = config['phases']

    print(f'audit effort: {effort} ({len(phases)} phases)')

    # Get file tree
    file_tree = '\n'.join(get_file_tree(audit_path))
    rust_files = [str(f.relative_to(audit_path)) for f in get_rust_files(audit_path)]

    print(f'found {len(rust_files)} rust files')

    # Phase 1: File selection
    print('phase 1: selecting files...')
    try:
        requested_files = phase_file_select(file_tree, rust_files, effort)
        print(f'selected {len(requested_files)} files')
    except Exception as e:
        print(f'phase 1 failed: {e}', file=sys.stderr)
        requested_files = rust_files

    # Read files
    file_contents = {}
    total_size = 0
    for rel_path in requested_files:
        full_path = audit_path / rel_path
        if full_path.exists() and full_path.is_file():
            content = read_file_safe(full_path)
            if content and total_size + len(content) < MAX_TOTAL_SIZE:
                file_contents[rel_path] = content
                total_size += len(content)

    print(f'loaded {len(file_contents)} files ({total_size} bytes)')

    all_results = []
    all_vulns = []

    # Run phases based on effort level
    try:
        if 'basic_scan' in phases:
            print('phase 2: basic vulnerability scan...')
            result = phase_basic_scan(file_contents, effort)
            all_results.append(result)
            all_vulns.extend(result.get('vulnerabilities', []))

        if 'thorough_scan' in phases:
            print('phase 2: thorough vulnerability scan...')
            result = phase_thorough_scan(file_contents, effort)
            all_results.append(result)
            all_vulns.extend(result.get('vulnerabilities', []))

        # High effort: focused scans on each vulnerability class
        if 'access_control' in phases:
            print('phase 3a: access control focused scan...')
            result = phase_focused_scan(file_contents, 'access_control', VULNERABILITY_CLASSES['access_control'])
            all_results.append(result)
            all_vulns.extend(result.get('vulnerabilities', []))

        if 'state_handling' in phases:
            print('phase 3b: state handling focused scan...')
            result = phase_focused_scan(file_contents, 'state_handling', VULNERABILITY_CLASSES['state_handling'])
            all_results.append(result)
            all_vulns.extend(result.get('vulnerabilities', []))

        if 'arithmetic' in phases:
            print('phase 3c: arithmetic focused scan...')
            result = phase_focused_scan(file_contents, 'arithmetic', VULNERABILITY_CLASSES['arithmetic'])
            all_results.append(result)
            all_vulns.extend(result.get('vulnerabilities', []))

        if 'deep_dive' in phases:
            print('phase 4: deep dive analysis...')
            result = phase_deep_dive(file_contents, all_vulns, effort)
            all_results.append(result)
            all_vulns.extend(result.get('vulnerabilities', []))

        if 'verify' in phases:
            print('phase 5: verification and deduplication...')
            # Merge before verify
            merged_vulns = merge_vulnerabilities(all_results)
            final_result = phase_verify(file_contents, merged_vulns)
            all_vulns = final_result.get('vulnerabilities', [])
        else:
            # Just merge without verification
            all_vulns = merge_vulnerabilities(all_results)

        # Final output
        final_output = {'vulnerabilities': all_vulns}
        result_json = json.dumps(final_output, indent=2)

        output_path = submission_path / 'audit.md'
        output_path.write_text(result_json)
        print(f'wrote {output_path} ({len(all_vulns)} vulnerabilities)')

    except Exception as e:
        print(f'audit failed: {e}', file=sys.stderr)
        error_result = json.dumps({
            'vulnerabilities': [],
            'error': str(e)
        })
        (submission_path / 'audit.md').write_text(error_result)
        sys.exit(1)


if __name__ == '__main__':
    main()
