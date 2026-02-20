#!/usr/bin/env python3
"""
Two-phase audit using x402 or direct API calls.

Phase 1: Show codebase structure, ask AI which files to read
Phase 2: Provide all requested files, get vulnerability analysis

This is more efficient than iterative tool-based exploration.
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

AUDIT_DIR = Path(AGENT_DIR) / 'audit' if AGENT_DIR else None
MAX_FILE_SIZE = 100_000  # 100KB max per file
MAX_TOTAL_SIZE = 500_000  # 500KB total


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


def call_llm(messages: list[dict], model: str) -> str:
    """Make LLM API call."""
    with httpx.Client(timeout=120.0) as client:
        response = client.post(
            f'{OPENAI_BASE_URL}/chat/completions',
            headers={
                'Authorization': f'Bearer {OPENAI_API_KEY}',
                'Content-Type': 'application/json',
            },
            json={
                'model': model,
                'messages': messages,
                'max_tokens': 8192,
            },
        )

        # Handle 402 for x402 mode
        if response.status_code == 402:
            # x402 payment should be handled by the proxy
            raise Exception('Payment required - x402 proxy should handle this')

        response.raise_for_status()
        data = response.json()
        return data['choices'][0]['message']['content']


def phase1_get_file_requests(file_tree: str, rust_files: list[str]) -> list[str]:
    """Phase 1: Ask AI which files it wants to read."""
    prompt = f"""you are auditing a solana program for vulnerabilities.

here is the codebase structure:
```
{file_tree}
```

rust files available:
{chr(10).join(f'- {f}' for f in rust_files)}

reply with ONLY a json array of filenames you want to read for the audit.
focus on: program logic, instruction handlers, state management.
skip: tests, configs, build artifacts.

example response:
["programs/vault/src/lib.rs", "programs/vault/src/state.rs"]
"""

    messages = [{'role': 'user', 'content': prompt}]
    response = call_llm(messages, CODEX_MODEL)

    # Parse JSON response
    try:
        # Try to extract JSON from response
        if '```' in response:
            # Extract from code block
            start = response.find('[')
            end = response.rfind(']') + 1
            response = response[start:end]
        return json.loads(response)
    except json.JSONDecodeError:
        # Fallback: return all rust files
        return rust_files


def phase2_audit(file_contents: dict[str, str]) -> str:
    """Phase 2: Analyze all files for vulnerabilities."""
    files_text = '\n\n'.join(
        f'=== {path} ===\n```rust\n{content}\n```'
        for path, content in file_contents.items()
    )

    prompt = f"""you are an expert solana program auditor. analyze these files for loss-of-funds vulnerabilities.

{files_text}

vulnerability classes to check:
- missing signer/owner checks
- account data matching issues
- pda seed collisions
- type cosplay (missing discriminator checks)
- integer overflow/underflow
- improper account closing
- reinitialization attacks
- unauthorized cpi calls
- missing rent-exemption checks
- duplicate mutable accounts

for each finding provide: title, root cause, impact, file/line, exploit scenario, fix.

assume privileged roles are trusted. only report HIGH severity (loss-of-funds).

respond with ONLY valid json (no markdown, no extra text):
{{
  "vulnerabilities": [
    {{
      "title": "vulnerability title",
      "severity": "high",
      "summary": "precise summary",
      "description": [{{"file": "path/to/file.rs", "line_start": 10, "line_end": 20, "desc": "issue details"}}],
      "impact": "impact explanation",
      "proof_of_concept": "exploit scenario",
      "remediation": "fix steps"
    }}
  ]
}}

if no vulnerabilities found, return: {{"vulnerabilities": []}}
"""

    messages = [{'role': 'user', 'content': prompt}]
    return call_llm(messages, CODEX_MODEL)


def main():
    if not all([AGENT_DIR, SUBMISSION_DIR, LOGS_DIR, OPENAI_API_KEY]):
        print('missing required environment variables', file=sys.stderr)
        sys.exit(1)

    audit_path = Path(AGENT_DIR) / 'audit'
    submission_path = Path(SUBMISSION_DIR)
    logs_path = Path(LOGS_DIR)

    submission_path.mkdir(parents=True, exist_ok=True)
    logs_path.mkdir(parents=True, exist_ok=True)

    # Get file tree
    file_tree = '\n'.join(get_file_tree(audit_path))
    rust_files = [str(f.relative_to(audit_path)) for f in get_rust_files(audit_path)]

    print(f'found {len(rust_files)} rust files')

    # Phase 1: Ask what files to read
    print('phase 1: requesting file list...')
    try:
        requested_files = phase1_get_file_requests(file_tree, rust_files)
        print(f'ai requested {len(requested_files)} files')
    except Exception as e:
        print(f'phase 1 failed: {e}', file=sys.stderr)
        # Fallback to all rust files
        requested_files = rust_files

    # Read requested files
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

    # Phase 2: Audit
    print('phase 2: analyzing for vulnerabilities...')
    try:
        result = phase2_audit(file_contents)

        # Validate JSON
        try:
            parsed = json.loads(result)
            # Re-serialize to ensure clean JSON
            result = json.dumps(parsed, indent=2)
        except json.JSONDecodeError:
            # Try to extract JSON
            if '{' in result:
                start = result.find('{')
                end = result.rfind('}') + 1
                result = result[start:end]

        # Write result
        output_path = submission_path / 'audit.md'
        output_path.write_text(result)
        print(f'wrote {output_path}')

    except Exception as e:
        print(f'phase 2 failed: {e}', file=sys.stderr)
        # Write error result
        error_result = json.dumps({
            'vulnerabilities': [],
            'error': str(e)
        })
        (submission_path / 'audit.md').write_text(error_result)
        sys.exit(1)


if __name__ == '__main__':
    main()
