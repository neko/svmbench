"""
Worker init - runs OpenCode audit with Daydreams x402 router.
"""
try:
    from uvloop import run
except ImportError:
    from asyncio import run

import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

from httpx import AsyncClient
from loguru import logger


SECRET_SVC_HOST = os.getenv('SECRETSVC_HOST', 'secretsvc')
SECRET_SVC_PORT = os.getenv('SECRETSVC_PORT', '8081')
SECRET_REF = os.getenv('SECRETSVC_REF', '')
SECRET_SVC_TOKEN = os.getenv('SECRETSVC_TOKEN', '')
RESULTSVC_HOST = os.getenv('RESULTSVC_HOST', '')
RESULTSVC_PORT = os.getenv('RESULTSVC_PORT', '8083')
RESULTSVC_JOB_TOKEN = os.getenv('RESULTSVC_JOB_TOKEN', '')

AGENT_DIR = Path(os.getenv('AGENT_DIR') or str(Path.home()))
AUDIT_DIR = Path(os.getenv('AUDIT_DIR') or str(AGENT_DIR / 'audit'))
SUBMISSION_DIR = Path(os.getenv('SUBMISSION_DIR') or str(AGENT_DIR / 'submission'))
LOGS_DIR = Path(os.getenv('LOGS_DIR') or '/home/logs')

JOB_ID = os.getenv('JOB_ID', 'job').strip()

RUNNER_DIR = Path(os.getenv('SVM_BENCH_RUNNER_DIR') or '/opt/svmbench/worker_runner')
OPENCODE_RUNNER_SH = RUNNER_DIR / 'run_opencode.sh'
AGENTS_MD = RUNNER_DIR / 'AGENTS.md'

# Fixed timeout (always max effort)
AUDIT_TIMEOUT = 900


def _validate_report(payload: object) -> dict:
    """Validate audit report structure."""
    if not isinstance(payload, dict):
        raise TypeError('Report must be an object')

    vulns = payload.get('vulnerabilities')
    if not isinstance(vulns, list):
        raise TypeError('Report must contain vulnerabilities: [...]')

    for v in vulns:
        if not isinstance(v, dict):
            raise TypeError('Each vulnerability must be an object')
        title = v.get('title')
        severity = v.get('severity')
        if not isinstance(title, str) or not title.strip():
            raise ValueError('Each vulnerability must have a title')
        if not isinstance(severity, str) or not severity.strip():
            raise ValueError('Each vulnerability must have a severity')

    return payload


def _extract_json(text: str) -> dict:
    """Extract and parse JSON from audit output."""
    text = text.strip()
    if not text:
        raise ValueError('Empty audit output')

    # Try to find JSON in fenced block
    if '```json' in text:
        start = text.find('```json') + 7
        end = text.rfind('```')
        if end > start:
            text = text[start:end].strip()
    elif '```' in text:
        start = text.find('```') + 3
        end = text.rfind('```')
        if end > start:
            text = text[start:end].strip()

    # Find JSON object
    start = text.find('{')
    end = text.rfind('}') + 1
    if start >= 0 and end > start:
        text = text[start:end]

    payload = json.loads(text)
    return _validate_report(payload)


def _run_audit(*, x402_key: str, model: str, effort: str) -> Path:
    """Run OpenCode audit."""
    env = os.environ.copy()
    env['X402_PRIVATE_KEY'] = x402_key
    env['OPENCODE_MODEL'] = model
    env['HOME'] = str(AGENT_DIR)
    env['AGENT_DIR'] = str(AGENT_DIR)
    env['SUBMISSION_DIR'] = str(SUBMISSION_DIR)
    env['LOGS_DIR'] = str(LOGS_DIR)

    env['AUDIT_TIMEOUT'] = str(AUDIT_TIMEOUT)

    # Copy AGENTS.md instructions
    if AGENTS_MD.exists():
        shutil.copy(AGENTS_MD, AGENT_DIR / 'AGENTS.md')

    if not OPENCODE_RUNNER_SH.exists():
        raise RuntimeError(f'Missing runner: {OPENCODE_RUNNER_SH}')

    logger.info(f'Running audit: model={model}, timeout={AUDIT_TIMEOUT}s')

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    SUBMISSION_DIR.mkdir(parents=True, exist_ok=True)

    proc = subprocess.run(
        [str(OPENCODE_RUNNER_SH)],
        cwd=str(AGENT_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout + 60,
        check=False,
    )
    (LOGS_DIR / 'runner.log').write_text(proc.stdout or '', encoding='utf-8')

    if proc.returncode != 0:
        logger.warning(f'Runner exit code: {proc.returncode}')

    audit_path = SUBMISSION_DIR / 'audit.md'
    if not audit_path.exists():
        raise RuntimeError('No audit output generated')

    return audit_path


def _unpack_bundle(bundle: bytes, work_dir: Path) -> tuple[Path, str, str, str]:
    """Unpack job bundle and return (upload_path, x402_key, model, effort)."""
    upload_zip_path = work_dir / 'upload.zip'
    key_payload = None

    with tarfile.open(fileobj=io.BytesIO(bundle), mode='r:*') as tar:
        for member in tar.getmembers():
            fileobj = tar.extractfile(member)
            if fileobj is None:
                raise RuntimeError(f'Failed to extract: {member.name}')

            data = fileobj.read()
            if member.name == 'upload.zip':
                upload_zip_path.write_bytes(data)
            else:
                key_payload = json.loads(data.decode('utf-8'))

    if key_payload is None:
        raise RuntimeError('Missing key.json in bundle')

    x402_key = key_payload.get('x402_key') or key_payload.get('openai_token') or ''
    if not x402_key:
        raise RuntimeError('Missing x402_key in bundle')

    model = key_payload.get('model') or 'anthropic:claude-sonnet-4-6'
    effort = key_payload.get('effort') or 'medium'
    if effort not in {'low', 'medium', 'high'}:
        effort = 'medium'

    if not upload_zip_path.exists():
        raise RuntimeError('Missing upload.zip in bundle')

    return upload_zip_path, x402_key, model, effort


async def main() -> None:
    logger.info('Requesting bundle...')
    async with AsyncClient() as client:
        response = await client.get(
            f'http://{SECRET_SVC_HOST}:{SECRET_SVC_PORT}/v1/bundles/{SECRET_REF}',
            headers={'X-Secrets-Token': SECRET_SVC_TOKEN},
        )
        response.raise_for_status()
        bundle = response.content

    logger.info(f'Got {len(bundle)} bytes, extracting...')
    report_payload = {
        'job_id': JOB_ID,
        'status': 'failed',
        'error': 'No audit generated',
    }

    with tempfile.TemporaryDirectory(prefix='svmbench-worker-') as tmpdir:
        work_dir = Path(tmpdir)
        upload_zip_path, x402_key, model, effort = _unpack_bundle(bundle, work_dir)

        if AUDIT_DIR.exists():
            shutil.rmtree(AUDIT_DIR)
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(upload_zip_path, 'r') as zf:
            zf.extractall(AUDIT_DIR)

        logger.info(f'Running audit with model={model}, effort={effort}')
        try:
            audit_path = _run_audit(x402_key=x402_key, model=model, effort=effort)
            audit_text = audit_path.read_text()
            _extract_json(audit_text)

            report_payload = {
                'job_id': JOB_ID,
                'status': 'succeeded',
                'report': audit_text,
            }
            logger.info('Audit completed successfully')
        except Exception as err:
            report_payload = {
                'job_id': JOB_ID,
                'status': 'failed',
                'error': str(err),
            }
            logger.opt(exception=err).error('Audit failed')

    logger.info(f'{report_payload=}')
    try:
        async with AsyncClient() as client:
            response = await client.post(
                f'http://{RESULTSVC_HOST}:{RESULTSVC_PORT}/v1/results',
                json=report_payload,
                headers={'X-Results-Token': RESULTSVC_JOB_TOKEN},
                timeout=30,
            )
            response.raise_for_status()
    except Exception as err:
        logger.opt(exception=err).exception('Failed to upload result')


if __name__ == '__main__':
    run(main())
