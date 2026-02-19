from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator, model_validator

from api.core.config import settings
from api.models.job import JobStatus
from api.util.zip_validate import validate_upload_zip


class StartJobForm(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    models: list[str]
    openai_key: Annotated[str | None, StringConstraints(strip_whitespace=True, min_length=1)]
    provider: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = 'openai'
    effort: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = 'medium'
    file: UploadFile

    @classmethod
    def as_form(
        cls,
        file: Annotated[UploadFile, File()],
        models: Annotated[list[str], Form()] = [],
        openai_key: Annotated[str | None, Form()] = None,
        provider: Annotated[str, Form()] = 'openai',
        effort: Annotated[str, Form()] = 'medium',
    ) -> 'StartJobForm':
        try:
            return cls(models=models, openai_key=openai_key, provider=provider, effort=effort, file=file)
        except ValidationError as exc:
            # TODO(es3n1n): this is **very** bad
            errors = exc.errors()
            messages = []
            for err in errors:
                if not isinstance(err, dict):
                    continue

                msg = err.get('msg', 'Invalid request')
                if isinstance(msg, str) and msg.startswith('Value error, '):
                    msg = msg.removeprefix('Value error, ')
                messages.append(msg)

            if not messages:
                messages = ['Invalid request']
            raise HTTPException(status_code=412, detail=messages[0]) from exc

    @field_validator('models', mode='before')
    @classmethod
    def parse_models(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [m.strip() for m in value.split(',') if m.strip()]
        return [m.strip() for m in value if m.strip()]

    @model_validator(mode='after')
    def require_openai_key(self) -> 'StartJobForm':
        # Skip validation if using proxy's static key or backend's static key
        if settings.BACKEND_USE_PROXY_STATIC_KEY:
            return self
        if settings.BACKEND_STATIC_OAI_KEY is None and not self.openai_key:
            msg = 'openai_key is required'
            raise ValueError(msg)
        return self

    @field_validator('file', mode='before')
    @classmethod
    def check_zip_file(cls, value: UploadFile) -> UploadFile:
        filename = value.filename or ''
        if not filename.lower().endswith('.zip'):
            msg = 'Only zip files are supported'
            raise ValueError(msg)

        size = getattr(value, 'size', None)
        if size is None or size > settings.BACKEND_MAX_ATTACHMENT_SIZE_BYTES:
            msg = f'Max file size is {settings.BACKEND_MAX_ATTACHMENT_SIZE_BYTES}'
            raise ValueError(msg)

        validate_upload_zip(
            value,
            max_uncompressed_bytes=settings.BACKEND_MAX_ATTACHMENT_UNCOMPRESSED_BYTES,
            max_files=settings.BACKEND_ZIP_MAX_FILES,
            max_ratio=settings.BACKEND_ZIP_MAX_COMPRESSION_RATIO,
            require_rust=True,
        )

        return value


class PatchJobForm(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public: bool


class StartJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    batch_id: UUID
    jobs: list['BatchJobItem']


class BatchJobItem(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    job_id: UUID = Field(validation_alias='id')
    model: str
    status: JobStatus


class JobStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    job_id: UUID = Field(validation_alias='id')
    batch_id: UUID | None = None
    status: JobStatus
    result: dict | None
    error: str | None = Field(validation_alias='result_error')
    model: str
    effort: str
    file_name: str
    public: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    queue_position: int | None = None


class JobHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    job_id: UUID = Field(validation_alias='id')
    batch_id: UUID | None = None
    model: str
    status: JobStatus
    created_at: datetime
    finished_at: datetime | None
    file_name: str
