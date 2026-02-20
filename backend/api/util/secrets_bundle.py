import io
import os
import tarfile

import orjson
from fastapi import UploadFile


def build_secret_bundle(*, upload: UploadFile, openai_token: str, key_mode: str, provider: str = 'openai', effort: str = 'medium') -> bytes:
    upload_file = upload.file

    # NOTE(es3n1n): upload.size is optional
    upload_file.seek(0, os.SEEK_END)
    upload_size = upload_file.tell()
    upload_file.seek(0)

    key_payload = orjson.dumps({'openai_token': openai_token, 'key_mode': key_mode, 'provider': provider, 'effort': effort})

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as tar:
        upload_info = tarfile.TarInfo(name='upload.zip')
        upload_info.size = upload_size
        tar.addfile(upload_info, fileobj=upload_file)

        key_info = tarfile.TarInfo(name='key.json')
        key_info.size = len(key_payload)
        tar.addfile(key_info, fileobj=io.BytesIO(key_payload))

    return buffer.getvalue()


def build_secret_bundle_from_bytes(*, upload_data: bytes, openai_token: str, key_mode: str, provider: str = 'openai', effort: str = 'medium') -> bytes:
    """Build secret bundle from raw bytes instead of UploadFile."""
    key_payload = orjson.dumps({'openai_token': openai_token, 'key_mode': key_mode, 'provider': provider, 'effort': effort})

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as tar:
        upload_info = tarfile.TarInfo(name='upload.zip')
        upload_info.size = len(upload_data)
        tar.addfile(upload_info, fileobj=io.BytesIO(upload_data))

        key_info = tarfile.TarInfo(name='key.json')
        key_info.size = len(key_payload)
        tar.addfile(key_info, fileobj=io.BytesIO(key_payload))

    return buffer.getvalue()
