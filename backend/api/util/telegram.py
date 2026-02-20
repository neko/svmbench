"""Telegram logging utility for verbose monitoring."""
import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx
from loguru import logger

from api.core.config import settings

TELEGRAM_TOKEN = settings.TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID = settings.TELEGRAM_CHAT_ID
TELEGRAM_ENABLED = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)

# Rate limiting
_last_send_time = 0.0
_min_interval = 0.5  # seconds between messages


async def _send_telegram(text: str, parse_mode: str = 'HTML') -> bool:
    """Send message to Telegram."""
    global _last_send_time

    if not TELEGRAM_ENABLED:
        return False

    # Rate limiting
    now = asyncio.get_event_loop().time()
    if now - _last_send_time < _min_interval:
        await asyncio.sleep(_min_interval - (now - _last_send_time))
    _last_send_time = asyncio.get_event_loop().time()

    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text[:4000],  # Telegram limit
        'parse_mode': parse_mode,
        'disable_web_page_preview': True,
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=10)
            if resp.status_code != 200:
                logger.warning(f'Telegram send failed: {resp.status_code} {resp.text}')
                return False
            return True
    except Exception as e:
        logger.warning(f'Telegram error: {e}')
        return False


def _truncate(text: str, max_len: int = 200) -> str:
    """Truncate text with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + '...'


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (
        str(text)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )


async def log_job_created(
    job_id: str,
    model: str,
    file_name: str,
    price: float,
    user_wallet: str,
) -> None:
    """Log new job creation."""
    text = f'''<b>📋 New Job Created</b>

<b>Job ID:</b> <code>{job_id[:8]}...</code>
<b>Model:</b> {_escape_html(model)}
<b>File:</b> {_escape_html(file_name)}
<b>Price:</b> ${price:.2f}
<b>Wallet:</b> <code>{user_wallet[:8]}...{user_wallet[-4:]}</code>
<b>Time:</b> {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}'''
    await _send_telegram(text)


async def log_payment_received(
    signature: str,
    amount: float,
    payer_wallet: str,
    model: str,
) -> None:
    """Log payment received."""
    text = f'''<b>💰 Payment Received</b>

<b>Amount:</b> ${amount:.2f} USDC
<b>Model:</b> {_escape_html(model)}
<b>Payer:</b> <code>{payer_wallet[:8]}...{payer_wallet[-4:]}</code>
<b>Signature:</b> <code>{signature[:16]}...</code>'''
    await _send_telegram(text)


async def log_bridge_started(
    operation_id: str,
    amount: float,
    source: str = 'Solana',
    dest: str = 'Base',
) -> None:
    """Log bridge operation started."""
    text = f'''<b>🌉 Bridge Started</b>

<b>Operation:</b> <code>{operation_id[:16]}...</code>
<b>Amount:</b> ${amount:.2f} USDC
<b>Route:</b> {source} → {dest}'''
    await _send_telegram(text)


async def log_bridge_completed(
    operation_id: str,
    tx_signature: str,
    amount_out: float,
    eta_seconds: int,
) -> None:
    """Log bridge completion."""
    text = f'''<b>✅ Bridge Completed</b>

<b>Operation:</b> <code>{operation_id[:16]}...</code>
<b>Output:</b> ${amount_out:.2f} USDC
<b>TX:</b> <code>{tx_signature[:16]}...</code>
<b>ETA:</b> ~{eta_seconds}s'''
    await _send_telegram(text)


async def log_x402_request(
    model: str,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
) -> None:
    """Log x402 API request."""
    text = f'''<b>🔗 x402 Request</b>

<b>Model:</b> {_escape_html(model)}'''
    if tokens_in:
        text += f'\n<b>Input:</b> {tokens_in:,} tokens'
    if tokens_out:
        text += f'\n<b>Output:</b> {tokens_out:,} tokens'
    await _send_telegram(text)


async def log_job_started(job_id: str, model: str) -> None:
    """Log job execution started."""
    text = f'''<b>🚀 Job Started</b>

<b>Job ID:</b> <code>{job_id[:8]}...</code>
<b>Model:</b> {_escape_html(model)}'''
    await _send_telegram(text)


async def log_job_completed(
    job_id: str,
    model: str,
    duration_seconds: float,
    vulnerabilities_count: int,
    status: str,
    error: str | None = None,
) -> None:
    """Log job completion."""
    emoji = '✅' if status == 'succeeded' else '❌'
    text = f'''<b>{emoji} Job Completed</b>

<b>Job ID:</b> <code>{job_id[:8]}...</code>
<b>Model:</b> {_escape_html(model)}
<b>Duration:</b> {duration_seconds:.1f}s
<b>Status:</b> {status}
<b>Vulnerabilities:</b> {vulnerabilities_count}'''

    if error:
        text += f'\n<b>Error:</b> {_escape_html(_truncate(error, 100))}'

    await _send_telegram(text)


async def log_audit_result(
    job_id: str,
    vulnerabilities: list[dict[str, Any]],
) -> None:
    """Log audit results summary."""
    if not vulnerabilities:
        text = f'''<b>📊 Audit Result</b>

<b>Job ID:</b> <code>{job_id[:8]}...</code>
<b>Result:</b> No vulnerabilities found'''
    else:
        vuln_summary = []
        for v in vulnerabilities[:5]:  # Max 5
            title = _escape_html(_truncate(v.get('title', 'Unknown'), 50))
            severity = v.get('severity', 'unknown')
            vuln_summary.append(f'• [{severity.upper()}] {title}')

        text = f'''<b>📊 Audit Result</b>

<b>Job ID:</b> <code>{job_id[:8]}...</code>
<b>Found:</b> {len(vulnerabilities)} vulnerabilities

{chr(10).join(vuln_summary)}'''
        if len(vulnerabilities) > 5:
            text += f'\n... and {len(vulnerabilities) - 5} more'

    await _send_telegram(text)


async def log_error(context: str, error: str) -> None:
    """Log error."""
    text = f'''<b>⚠️ Error</b>

<b>Context:</b> {_escape_html(context)}
<b>Error:</b> {_escape_html(_truncate(error, 300))}'''
    await _send_telegram(text)


async def log_info(message: str) -> None:
    """Log general info."""
    text = f'''<b>ℹ️ Info</b>

{_escape_html(message)}'''
    await _send_telegram(text)
