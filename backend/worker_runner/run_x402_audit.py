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
        'phases': ['file_select', 'thorough_scan', 'permissions', 'state', 'value_flow', 'deep_dive', 'verify'],
    },
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


def call_llm(messages: list[dict], model: str, max_tokens: int = 8192, max_retries: int = 3) -> str:
    """Make LLM API call with retry logic."""
    last_error = None

    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=300.0) as client:  # 5 min timeout per request
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

                # Retry on transient errors
                if response.status_code in (402, 408, 429, 500, 502, 503, 504):
                    wait_time = min(10 * (2 ** attempt), 60)  # 10s, 20s, 40s max 60s
                    print(f'LLM call got {response.status_code}, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})')
                    import time
                    time.sleep(wait_time)
                    last_error = Exception(f'HTTP {response.status_code}: {response.text[:200]}')
                    continue

                response.raise_for_status()
                data = response.json()
                return data['choices'][0]['message']['content']

        except httpx.TimeoutException as e:
            wait_time = min(10 * (2 ** attempt), 60)
            print(f'LLM call timed out, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})')
            import time
            time.sleep(wait_time)
            last_error = e
            continue

    raise last_error or Exception('LLM call failed after retries')


def phase_file_select(file_tree: str, rust_files: list[str], effort: str) -> list[str]:
    """Phase 1: Ask AI which files to analyze."""

    prompt = f"""you are a solana security auditor. select which files to review.

directory structure:
```
{file_tree}
```

rust files:
{chr(10).join(f'- {f}' for f in rust_files)}

return a json array of files to audit. include program logic, skip tests/configs.
["path/file.rs", ...]
"""

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

    prompt = f"""you are auditing this solana program for security vulnerabilities.

{files_text}

first, understand what this program does. then identify any HIGH severity vulnerabilities that could result in unauthorized fund transfers, theft of assets, or permanent loss of user funds.

only report issues that are actually exploitable. if you find nothing critical, that's okay.

respond with json only:
{{
  "vulnerabilities": [
    {{
      "title": "clear title",
      "severity": "high",
      "summary": "what the bug is",
      "description": [{{"file": "path.rs", "line_start": N, "line_end": M, "desc": "technical details"}}],
      "impact": "concrete impact",
      "proof_of_concept": "how to exploit",
      "remediation": "how to fix"
    }}
  ]
}}
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

    prompt = f"""you are a senior solana security researcher conducting an audit. your goal is to find real, exploitable vulnerabilities.

{files_text}

APPROACH:
1. understand the program's purpose and architecture
2. identify trust boundaries and privileged operations
3. trace data flow and state transitions
4. find bugs that break security assumptions

report only HIGH severity issues - those where an attacker could steal funds, drain accounts, or cause permanent financial damage. quality over quantity. if the code is secure, report nothing.

json format:
{{
  "vulnerabilities": [
    {{
      "title": "descriptive title",
      "severity": "high",
      "summary": "the bug in one sentence",
      "description": [{{"file": "path.rs", "line_start": N, "line_end": M, "desc": "technical explanation"}}],
      "impact": "what an attacker gains",
      "proof_of_concept": "attack steps",
      "remediation": "fix"
    }}
  ]
}}
"""

    config = EFFORT_CONFIG.get(effort, EFFORT_CONFIG['medium'])
    messages = [{'role': 'user', 'content': prompt}]
    response = call_llm(messages, CODEX_MODEL, max_tokens=config['max_tokens'])
    return _parse_vuln_response(response)


def phase_focused_scan(file_contents: dict[str, str], focus_area: str) -> dict:
    """Focused exploration of a specific aspect (high effort)."""
    files_text = '\n\n'.join(
        f'=== {path} ===\n```rust\n{content}\n```'
        for path, content in file_contents.items()
    )

    focus_prompts = {
        'permissions': 'examine all permission checks and authorization logic. who can call what? are there paths where untrusted callers gain elevated access?',
        'state': 'examine state transitions and account lifecycle. can accounts be corrupted, reused inappropriately, or left in invalid states?',
        'value_flow': 'trace all token transfers and value movements. can funds be redirected, duplicated, or stolen?',
    }

    focus_guidance = focus_prompts.get(focus_area, 'look for any security issues you may have missed.')

    prompt = f"""continuing audit. {focus_guidance}

{files_text}

report any HIGH severity vulnerabilities you find. json format:
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
"""

    messages = [{'role': 'user', 'content': prompt}]
    response = call_llm(messages, CODEX_MODEL, max_tokens=8192)
    return _parse_vuln_response(response)


def phase_deep_dive(file_contents: dict[str, str], existing_vulns: list[dict], effort: str) -> dict:
    """Final pass to catch anything missed."""
    files_text = '\n\n'.join(
        f'=== {path} ===\n```rust\n{content}\n```'
        for path, content in file_contents.items()
    )

    if existing_vulns:
        vuln_summary = '\n'.join(f'- {v.get("title", "unknown")}' for v in existing_vulns[:5])
        context = f"already found:\n{vuln_summary}\n\nlook for anything we missed."
    else:
        context = "no issues found yet. look harder - examine edge cases, complex interactions, subtle logic errors."

    prompt = f"""final audit pass.

{files_text}

{context}

report any additional HIGH severity vulnerabilities. json format:
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

    prompt = f"""verify these findings against the code. remove false positives, deduplicate, and improve descriptions.

{files_text}

candidates:
{vulns_text}

return only confirmed HIGH severity issues:
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

        # High effort: focused exploration passes
        if 'permissions' in phases:
            print('phase 3a: permissions exploration...')
            result = phase_focused_scan(file_contents, 'permissions')
            all_results.append(result)
            all_vulns.extend(result.get('vulnerabilities', []))

        if 'state' in phases:
            print('phase 3b: state exploration...')
            result = phase_focused_scan(file_contents, 'state')
            all_results.append(result)
            all_vulns.extend(result.get('vulnerabilities', []))

        if 'value_flow' in phases:
            print('phase 3c: value flow exploration...')
            result = phase_focused_scan(file_contents, 'value_flow')
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
