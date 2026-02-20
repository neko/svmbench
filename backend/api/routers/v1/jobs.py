import os
import uuid
from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from httpx import AsyncClient
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import settings
from api.util.fs import ROOT_DIR
from api.core.deps import OptionalTokenDep, TokenDep, get_db
from api.core.impl import auth_backend
from api.core.rabbitmq import RabbitMQPublisher, get_rabbitmq_publisher
from api.models.job import Job, JobStatus
from api.schemas.job import (
    BatchJobItem,
    JobHistoryItem,
    JobStatusResponse,
    PatchJobForm,
    StartJobForm,
    StartJobFromUrlRequest,
    StartJobResponse,
)
from api.secrets.impl import secret_storage
from api.util.secrets_bundle import build_secret_bundle, build_secret_bundle_from_bytes
from api.util.zip_validate import ZipValidationError, validate_zip_bytes

PUBLIC_SOURCES_DIR = ROOT_DIR / 'public_sources'

router = APIRouter(prefix='/jobs', tags=['jobs'])


async def _validate_and_consume_payment_token(
    session: AsyncSession,
    payment_token: str | None,
    effort: str,
    model: str,
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

    if payment.models != model:
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


ALLOWED_EFFORTS = {'low', 'medium', 'high'}


def _require_allowed_effort(effort: str) -> None:
    if effort not in ALLOWED_EFFORTS:
        raise HTTPException(status_code=412, detail='Effort must be low, medium, or high')


def _require_service_key() -> None:
    """Ensure X402 service key is configured."""
    if not settings.X402_SERVICE_KEY:
        raise HTTPException(status_code=500, detail='X402 service key not configured')


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
        archive_url = f'https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip'
        async with AsyncClient(follow_redirects=True, timeout=60.0) as client:
            response = await client.get(archive_url)
            if response.status_code == 404:
                archive_url = f'https://github.com/{owner}/{repo}/archive/refs/heads/master.zip'
                response = await client.get(archive_url)
            if response.status_code != 200:
                raise HTTPException(status_code=400, detail=f'Failed to fetch from GitHub: {response.status_code}')
            return response.content, f'{repo}.zip'

    # Generic URL
    async with AsyncClient(follow_redirects=True, timeout=60.0) as client:
        response = await client.get(url)
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail=f'Failed to fetch URL: {response.status_code}')

        content_type = response.headers.get('content-type', '')
        if 'zip' not in content_type.lower() and not url.lower().endswith('.zip'):
            raise HTTPException(status_code=400, detail='URL must point to a zip file or GitHub repository')

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
    _require_allowed_effort(form.effort)
    _require_service_key()

    if not form.model:
        raise HTTPException(status_code=412, detail='Model is required')

    # Validate payment
    if not form.payment_token:
        raise HTTPException(status_code=402, detail='Payment required')

    paid = await _validate_and_consume_payment_token(
        session, form.payment_token, form.effort, form.model
    )
    if not paid:
        raise HTTPException(status_code=402, detail='Invalid or expired payment token')

    batch_id = uuid.uuid4()

    try:
        bundle = build_secret_bundle(upload=form.file, model=form.model, effort=form.effort)

        job_id = uuid.uuid4()
        secret_ref = os.urandom(32).hex()
        result_token = os.urandom(32).hex()

        await secret_storage.save_secret(secret_ref, bundle)

        job = Job(
            id=job_id,
            batch_id=batch_id,
            status=JobStatus.queued,
            user_id=token.user_id,
            secret_ref=secret_ref,
            result_token=result_token,
            model=form.model,
            effort=form.effort,
            file_name=(form.file.filename or 'files.zip')[:128],
        )
        session.add(job)
        await session.commit()

        try:
            await publisher.publish_job_start(
                job_id=str(job.id),
                secret_ref=job.secret_ref or '',
                model=job.model,
                result_token=job.result_token or '',
            )
        except Exception as err:
            with suppress(Exception):
                await secret_storage.delete_secret(secret_ref)
            await session.delete(job)
            await session.commit()
            raise HTTPException(status_code=502, detail='Failed to enqueue job') from err

        return StartJobResponse(
            batch_id=batch_id,
            jobs=[BatchJobItem.model_validate(job)],
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
    _require_allowed_effort(request.effort)
    _require_service_key()

    if not request.model:
        raise HTTPException(status_code=412, detail='Model is required')

    # Validate payment
    if not request.payment_token:
        raise HTTPException(status_code=402, detail='Payment required')

    paid = await _validate_and_consume_payment_token(
        session, request.payment_token, request.effort, request.model
    )
    if not paid:
        raise HTTPException(status_code=402, detail='Invalid or expired payment token')

    # Fetch source from URL
    zip_data, filename = await _fetch_source_from_url(request.source_url)

    if len(zip_data) > settings.BACKEND_MAX_ATTACHMENT_SIZE_BYTES:
        raise HTTPException(status_code=413, detail=f'Downloaded file too large')

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
    bundle = build_secret_bundle_from_bytes(
        upload_data=zip_data,
        model=request.model,
        effort=request.effort,
    )

    job_id = uuid.uuid4()
    secret_ref = os.urandom(32).hex()
    result_token = os.urandom(32).hex()

    await secret_storage.save_secret(secret_ref, bundle)

    job = Job(
        id=job_id,
        batch_id=batch_id,
        status=JobStatus.queued,
        user_id=token.user_id,
        secret_ref=secret_ref,
        result_token=result_token,
        model=request.model,
        effort=request.effort,
        file_name=filename[:128],
    )
    session.add(job)
    await session.commit()

    try:
        await publisher.publish_job_start(
            job_id=str(job.id),
            secret_ref=job.secret_ref or '',
            model=job.model,
            result_token=job.result_token or '',
        )
    except Exception as err:
        with suppress(Exception):
            await secret_storage.delete_secret(secret_ref)
        await session.delete(job)
        await session.commit()
        raise HTTPException(status_code=502, detail='Failed to enqueue job') from err

    return StartJobResponse(
        batch_id=batch_id,
        jobs=[BatchJobItem.model_validate(job)],
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

    source_path = PUBLIC_SOURCES_DIR / f'{job_id}.zip'
    if not source_path.exists():
        raise HTTPException(status_code=404, detail='Source not available')

    return FileResponse(
        source_path,
        media_type='application/zip',
        filename=job.file_name or 'source.zip',
    )
