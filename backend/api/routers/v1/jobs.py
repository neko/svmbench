import os
import uuid
from contextlib import suppress
from http import HTTPStatus
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from httpx import AsyncClient
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import settings
from api.util.fs import ROOT_DIR

# Directory for storing public audit source files
PUBLIC_SOURCES_DIR = ROOT_DIR / 'public_sources'
from api.core.const import ALLOWED_MODELS, ALLOWED_PROVIDERS, OPENROUTER_ALLOWED_MODELS
from api.core.deps import OptionalTokenDep, TokenDep, get_db
from api.core.impl import auth_backend
from api.core.rabbitmq import RabbitMQPublisher, get_rabbitmq_publisher
from api.models.job import Job, JobStatus
from api.schemas.job import BatchJobItem, JobHistoryItem, JobStatusResponse, PatchJobForm, StartJobForm, StartJobFromUrlRequest, StartJobResponse
from api.secrets.impl import secret_storage
from api.util.aes_gcm import derive_key, encrypt_token
from api.util.secrets_bundle import build_secret_bundle, build_secret_bundle_from_bytes
from api.util.zip_validate import ZipValidationError, validate_zip_bytes


router = APIRouter(prefix='/jobs', tags=['jobs'])


async def _validate_and_consume_payment_token(
    session: AsyncSession,
    payment_token: str | None,
    effort: str,
    models: list[str],
) -> bool:
    """Validate payment token and return True if payment is valid."""
    if not payment_token:
        return False

    from api.models.payment import Payment
    from datetime import datetime, timezone

    result = await session.execute(
        select(Payment).where(
            Payment.payment_token == payment_token,
            Payment.used == False,  # noqa: E712
            Payment.effort == effort,
        )
    )
    payment = result.scalar_one_or_none()

    if not payment:
        return False

    # Verify models match (payment must cover requested models)
    paid_models = set(payment.models.split(',')) if payment.models else set()
    requested_models = set(models)

    if not requested_models.issubset(paid_models):
        return False

    payment.used = True
    payment.used_at = datetime.now(timezone.utc)
    await session.commit()
    return True


JobFormDep = Annotated[StartJobForm, Depends(StartJobForm.as_form)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db)]
PublisherDep = Annotated[RabbitMQPublisher, Depends(get_rabbitmq_publisher)]


async def _queue_position(session: AsyncSession, job: Job) -> int | None:
    if job.status != JobStatus.queued or job.created_at is None:
        return None
    stmt = (
        select(func.count())
        .select_from(Job)
        .where(
            Job.status == JobStatus.queued,
            or_(
                Job.created_at < job.created_at,
                and_(Job.created_at == job.created_at, Job.id < job.id),
            ),
        )
    )
    count = await session.scalar(stmt)
    return int(count or 0) + 1


async def _require_no_active_job(*, session: AsyncSession, user_id: str) -> None:
    if not auth_backend:
        return

    existing_job_id = await session.scalar(
        select(Job.id)
        .where(
            Job.user_id == user_id,
            Job.status.in_([JobStatus.queued, JobStatus.running]),
        )
        .limit(1)
    )
    if existing_job_id is not None:
        raise HTTPException(
            status_code=409,
            detail='You already have a queued or running job',
        )


def _require_allowed_model(model: str, provider: str) -> None:
    if provider == 'openrouter':
        if model not in OPENROUTER_ALLOWED_MODELS:
            raise HTTPException(status_code=401, detail='Model is not allowed for OpenRouter')
    elif provider == 'x402':
        # x402 models are fetched dynamically - accept any llm-* model
        # Invalid models will fail at x402 gateway level
        if not model.startswith('llm-'):
            raise HTTPException(status_code=401, detail='x402 models must start with llm-')
    elif model not in ALLOWED_MODELS:
        raise HTTPException(status_code=401, detail='Model is not allowed')


ALLOWED_EFFORTS = {'low', 'medium', 'high'}


def _require_allowed_provider(provider: str) -> None:
    if provider not in ALLOWED_PROVIDERS:
        raise HTTPException(status_code=401, detail='Provider is not allowed')


def _require_allowed_effort(effort: str) -> None:
    if effort not in ALLOWED_EFFORTS:
        raise HTTPException(status_code=412, detail='Effort must be low, medium, or high')


def _resolve_openai_key(form: StartJobForm) -> str | None:
    # Determine which OpenAI key mode to use:
    # 1. BACKEND_USE_PROXY_STATIC_KEY: Use "STATIC" marker, real key stays in oai_proxy only
    # 2. BACKEND_STATIC_OAI_KEY: Backend knows the key (legacy mode)
    # 3. User-provided key
    static_key = settings.BACKEND_STATIC_OAI_KEY.get_secret_value() if settings.BACKEND_STATIC_OAI_KEY else None
    return static_key or form.openai_key


async def _maybe_validate_user_key(*, form: StartJobForm, openai_key: str | None) -> None:
    # Validate user-provided keys (skip if using static keys)
    if settings.BACKEND_USE_PROXY_STATIC_KEY:
        return
    if settings.BACKEND_STATIC_OAI_KEY is not None:
        return
    if not form.openai_key:
        return
    if not openai_key:
        return

    # Choose validation endpoint based on provider
    if form.provider == 'openrouter':
        validate_url = 'https://openrouter.ai/api/v1/models'
    else:
        validate_url = 'https://api.openai.com/v1/models'

    async with AsyncClient() as client:
        response = await client.get(
            validate_url,
            headers={
                'Authorization': f'Bearer {openai_key}',
            },
        )
        if response.status_code != HTTPStatus.OK:
            raise HTTPException(status_code=401, detail='Invalid API key')


def _encode_openai_token(
    *,
    openai_key: str,
    use_proxy_static: bool,
    use_proxy_tokens: bool,
    use_x402: bool = False,
) -> tuple[str, str]:
    """Return (openai_token, key_mode) for the worker bundle."""
    if use_x402:
        # x402 paid mode - marker token, proxy handles payment
        return 'X402', 'x402'

    if use_proxy_static:
        # Marker token (not a real credential). The proxy substitutes its static key.
        return 'STATIC', 'proxy_static'

    if use_proxy_tokens:
        if settings.OAI_PROXY_AES_KEY is None:
            raise HTTPException(status_code=500, detail='OAI_PROXY_AES_KEY must be set for proxy mode')
        return (
            encrypt_token(
                openai_key,
                key=derive_key(settings.OAI_PROXY_AES_KEY.get_secret_value()),
            ),
            'proxy',
        )

    return openai_key, 'direct'


async def _fetch_source_from_url(url: str) -> tuple[bytes, str]:
    """Fetch source code from URL. Returns (zip_bytes, filename)."""
    import re
    from urllib.parse import urlparse

    parsed = urlparse(url)

    # Handle GitHub URLs
    github_match = re.match(r'^/([^/]+)/([^/]+)(?:/tree/([^/]+))?', parsed.path)
    if parsed.netloc == 'github.com' and github_match:
        owner, repo, branch = github_match.groups()
        repo = repo.rstrip('.git')
        branch = branch or 'main'
        # Use GitHub's archive API
        archive_url = f'https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip'
        async with AsyncClient(follow_redirects=True, timeout=60.0) as client:
            response = await client.get(archive_url)
            if response.status_code == 404:
                # Try master branch
                archive_url = f'https://github.com/{owner}/{repo}/archive/refs/heads/master.zip'
                response = await client.get(archive_url)
            if response.status_code != 200:
                raise HTTPException(status_code=400, detail=f'Failed to fetch from GitHub: {response.status_code}')
            return response.content, f'{repo}.zip'

    # Generic URL - try to download directly
    async with AsyncClient(follow_redirects=True, timeout=60.0) as client:
        response = await client.get(url)
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail=f'Failed to fetch URL: {response.status_code}')

        content_type = response.headers.get('content-type', '')
        if 'zip' not in content_type.lower() and not url.lower().endswith('.zip'):
            raise HTTPException(status_code=400, detail='URL must point to a zip file or GitHub repository')

        # Extract filename from URL or use default
        filename = parsed.path.split('/')[-1] or 'source.zip'
        if not filename.endswith('.zip'):
            filename = 'source.zip'

        return response.content, filename


@router.post('/start')
async def start_job(
    form: JobFormDep,
    session: DbSessionDep,
    publisher: PublisherDep,
    token: TokenDep,
) -> StartJobResponse:
    await _require_no_active_job(session=session, user_id=token.user_id)
    _require_allowed_provider(form.provider)
    _require_allowed_effort(form.effort)

    if not form.models:
        raise HTTPException(status_code=412, detail='At least one model is required')

    for model in form.models:
        _require_allowed_model(model, form.provider)

    # Check for payment token - if valid, use x402 paid mode
    paid_with_token = False
    if form.payment_token:
        paid_with_token = await _validate_and_consume_payment_token(
            session, form.payment_token, form.effort, form.models
        )
        if not paid_with_token:
            raise HTTPException(status_code=402, detail='Invalid or expired payment token')

    use_x402 = paid_with_token
    use_proxy_static = settings.BACKEND_USE_PROXY_STATIC_KEY and not use_x402
    use_proxy_tokens = (settings.BACKEND_OAI_KEY_MODE == 'proxy' or form.provider == 'openrouter') and not use_x402
    openai_key = _resolve_openai_key(form) if not paid_with_token else None

    if not use_x402 and not use_proxy_static and not openai_key:
        raise HTTPException(status_code=412, detail='API key is required')

    if not paid_with_token:
        await _maybe_validate_user_key(form=form, openai_key=openai_key)

    batch_id = uuid.uuid4()
    openai_token, key_mode = _encode_openai_token(
        openai_key=openai_key or '',
        use_proxy_static=use_proxy_static,
        use_proxy_tokens=use_proxy_tokens,
        use_x402=use_x402,
    )

    try:
        bundle = build_secret_bundle(upload=form.file, openai_token=openai_token, key_mode=key_mode, provider=form.provider, effort=form.effort)

        jobs: list[Job] = []
        secret_refs: list[str] = []

        for model in form.models:
            job_id = uuid.uuid4()
            secret_ref = os.urandom(32).hex()
            result_token = os.urandom(32).hex()

            await secret_storage.save_secret(secret_ref, bundle)
            secret_refs.append(secret_ref)

            job = Job(
                id=job_id,
                batch_id=batch_id,
                status=JobStatus.queued,
                user_id=token.user_id,
                secret_ref=secret_ref,
                result_token=result_token,
                model=model,
                effort=form.effort,
                file_name=(form.file.filename or 'files.zip')[:128],
            )
            session.add(job)
            jobs.append(job)

        await session.commit()

        try:
            for job in jobs:
                await publisher.publish_job_start(
                    job_id=str(job.id),
                    secret_ref=job.secret_ref or '',
                    model=job.model,
                    result_token=job.result_token or '',
                )
        except Exception as err:
            for secret_ref in secret_refs:
                with suppress(Exception):
                    await secret_storage.delete_secret(secret_ref)
            for job in jobs:
                await session.delete(job)
            await session.commit()
            raise HTTPException(
                status_code=502,
                detail='Failed to enqueue jobs',
            ) from err

        return StartJobResponse(
            batch_id=batch_id,
            jobs=[BatchJobItem.model_validate(job) for job in jobs],
        )
    finally:
        await form.file.close()


@router.post('/start-from-url')
async def start_job_from_url(
    request: StartJobFromUrlRequest,
    session: DbSessionDep,
    publisher: PublisherDep,
    token: TokenDep,
) -> StartJobResponse:
    await _require_no_active_job(session=session, user_id=token.user_id)
    _require_allowed_provider(request.provider)
    _require_allowed_effort(request.effort)

    if not request.models:
        raise HTTPException(status_code=412, detail='At least one model is required')

    for model in request.models:
        _require_allowed_model(model, request.provider)

    # Check for payment token - if valid, use x402 paid mode
    paid_with_token = False
    if request.payment_token:
        paid_with_token = await _validate_and_consume_payment_token(
            session, request.payment_token, request.effort, request.models
        )
        if not paid_with_token:
            raise HTTPException(status_code=402, detail='Invalid or expired payment token')

    use_x402 = paid_with_token
    use_proxy_static = settings.BACKEND_USE_PROXY_STATIC_KEY and not use_x402
    use_proxy_tokens = (settings.BACKEND_OAI_KEY_MODE == 'proxy' or request.provider == 'openrouter') and not use_x402
    static_key = settings.BACKEND_STATIC_OAI_KEY.get_secret_value() if settings.BACKEND_STATIC_OAI_KEY else None
    openai_key = (static_key or request.openai_key) if not paid_with_token else None

    if not use_x402 and not use_proxy_static and not openai_key:
        raise HTTPException(status_code=412, detail='API key is required')

    # Fetch source from URL
    zip_data, filename = await _fetch_source_from_url(request.source_url)

    # Validate size
    if len(zip_data) > settings.BACKEND_MAX_ATTACHMENT_SIZE_BYTES:
        raise HTTPException(status_code=413, detail=f'Downloaded file too large (max {settings.BACKEND_MAX_ATTACHMENT_SIZE_BYTES} bytes)')

    # Validate zip contents
    try:
        validate_zip_bytes(
            zip_data,
            max_uncompressed_bytes=settings.BACKEND_MAX_ATTACHMENT_UNCOMPRESSED_BYTES,
            max_files=settings.BACKEND_ZIP_MAX_FILES,
            max_ratio=settings.BACKEND_ZIP_MAX_COMPRESSION_RATIO,
            require_rust=True,
        )
    except ZipValidationError as exc:
        raise HTTPException(status_code=412, detail=str(exc)) from exc

    batch_id = uuid.uuid4()
    openai_token, key_mode = _encode_openai_token(
        openai_key=openai_key or '',
        use_proxy_static=use_proxy_static,
        use_proxy_tokens=use_proxy_tokens,
        use_x402=use_x402,
    )

    bundle = build_secret_bundle_from_bytes(
        upload_data=zip_data,
        openai_token=openai_token,
        key_mode=key_mode,
        provider=request.provider,
        effort=request.effort,
    )

    jobs: list[Job] = []
    secret_refs: list[str] = []

    for model in request.models:
        job_id = uuid.uuid4()
        secret_ref = os.urandom(32).hex()
        result_token = os.urandom(32).hex()

        await secret_storage.save_secret(secret_ref, bundle)
        secret_refs.append(secret_ref)

        job = Job(
            id=job_id,
            batch_id=batch_id,
            status=JobStatus.queued,
            user_id=token.user_id,
            secret_ref=secret_ref,
            result_token=result_token,
            model=model,
            effort=request.effort,
            file_name=filename[:128],
        )
        session.add(job)
        jobs.append(job)

    await session.commit()

    try:
        for job in jobs:
            await publisher.publish_job_start(
                job_id=str(job.id),
                secret_ref=job.secret_ref or '',
                model=job.model,
                result_token=job.result_token or '',
            )
    except Exception as err:
        for secret_ref in secret_refs:
            with suppress(Exception):
                await secret_storage.delete_secret(secret_ref)
        for job in jobs:
            await session.delete(job)
        await session.commit()
        raise HTTPException(
            status_code=502,
            detail='Failed to enqueue jobs',
        ) from err

    return StartJobResponse(
        batch_id=batch_id,
        jobs=[BatchJobItem.model_validate(job) for job in jobs],
    )


@router.get('/batch/{batch_id}')
async def get_batch(
    batch_id: uuid.UUID,
    session: DbSessionDep,
    token: OptionalTokenDep,
) -> list[JobStatusResponse]:
    stmt = select(Job).where(Job.batch_id == batch_id).order_by(Job.model)
    jobs_result = await session.scalars(stmt)
    jobs = jobs_result.all()
    if not jobs:
        raise HTTPException(status_code=404, detail='Batch not found')

    first_job = jobs[0]
    if not first_job.public and ((not token) or (auth_backend and first_job.user_id != token.user_id)):
        raise HTTPException(status_code=404, detail='Batch not found')

    responses = []
    for job in jobs:
        response = JobStatusResponse.model_validate(job)
        response.queue_position = await _queue_position(session, job)
        responses.append(response)
    return responses


@router.get('/history')
async def get_job_history(
    session: DbSessionDep,
    token: TokenDep,
) -> list[JobHistoryItem]:
    if not auth_backend:
        raise HTTPException(status_code=404, detail='Not found')

    stmt = select(Job).where(Job.user_id == token.user_id).order_by(Job.created_at.desc(), Job.id.desc())
    jobs = await session.scalars(stmt)
    return [JobHistoryItem.model_validate(job) for job in jobs.all()]


@router.get('/{job_id}')
async def get_job(
    job_id: uuid.UUID,
    session: DbSessionDep,
    token: OptionalTokenDep,
) -> JobStatusResponse:
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail='Job not found')
    if not job.public and ((not token) or (auth_backend and job.user_id != token.user_id)):
        raise HTTPException(status_code=404, detail='Job not found')
    response = JobStatusResponse.model_validate(job)
    response.queue_position = await _queue_position(session, job)
    return response


@router.patch('/{job_id}')
async def patch_job(
    job_id: uuid.UUID,
    session: DbSessionDep,
    token: TokenDep,
    form: PatchJobForm,
) -> JobStatusResponse:
    # Does not really matter for instances with no authorization
    if not auth_backend:
        raise HTTPException(status_code=404, detail='Not found')

    job = await session.get(Job, job_id)
    if not job or job.user_id != token.user_id:
        raise HTTPException(status_code=404, detail='Job not found')

    job.public = form.public
    await session.commit()
    await session.refresh(job)
    response = JobStatusResponse.model_validate(job)
    response.queue_position = await _queue_position(session, job)
    return response


@router.get('/{job_id}/source')
async def get_job_source(
    job_id: uuid.UUID,
    session: DbSessionDep,
) -> FileResponse:
    """Get source files for a public audit."""
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail='Job not found')
    if not job.public:
        raise HTTPException(status_code=404, detail='Source not available')

    # Look for source file by job_id
    source_path = PUBLIC_SOURCES_DIR / f'{job_id}.zip'
    if not source_path.exists():
        raise HTTPException(status_code=404, detail='Source not available')

    return FileResponse(
        source_path,
        media_type='application/zip',
        filename=job.file_name or 'source.zip',
    )
