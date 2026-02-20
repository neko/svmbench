import io
import os
import tarfile

import orjson
from fastapi import UploadFile

from api.core.config import settings


def build_secret_bundle(*, upload: UploadFile, model: str, effort: str = 'medium') -> bytes:
    """Build secret bundle with x402 service key."""
    upload_file = upload.file

    upload_file.seek(0, os.SEEK_END)
    upload_size = upload_file.tell()
    upload_file.seek(0)

    key_payload = orjson.dumps({
        'x402_key': settings.X402_SERVICE_KEY,
        'model': model,
        'effort': effort,
    })

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as tar:
        upload_info = tarfile.TarInfo(name='upload.zip')
        upload_info.size = upload_size
        tar.addfile(upload_info, fileobj=upload_file)

        key_info = tarfile.TarInfo(name='key.json')
        key_info.size = len(key_payload)
        tar.addfile(key_info, fileobj=io.BytesIO(key_payload))

    return buffer.getvalue()


def build_secret_bundle_from_bytes(*, upload_data: bytes, model: str, effort: str = 'medium') -> bytes:
    """Build secret bundle from raw bytes."""
    key_payload = orjson.dumps({
        'x402_key': settings.X402_SERVICE_KEY,
        'model': model,
        'effort': effort,
    })

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as tar:
        upload_info = tarfile.TarInfo(name='upload.zip')
        upload_info.size = len(upload_data)
        tar.addfile(upload_info, fileobj=io.BytesIO(upload_data))

        key_info = tarfile.TarInfo(name='key.json')
        key_info.size = len(key_payload)
        tar.addfile(key_info, fileobj=io.BytesIO(key_payload))

    return buffer.getvalue()
