import io
from pathlib import Path
from typing import BinaryIO
from botocore.exceptions import ClientError
import boto3
from botocore.config import Config
from .config import settings

def r2_enabled() -> bool:
    return bool(settings.r2_endpoint_url and settings.r2_access_key_id and
                settings.r2_secret_access_key and settings.r2_bucket_name)

def _client():
    if not r2_enabled():
        raise RuntimeError("R2 storage is not fully configured.")
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
        config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
    )

def put_object(key: str, content: bytes, content_type: str = "application/pdf") -> None:
    if r2_enabled():
        _client().put_object(Bucket=settings.r2_bucket_name, Key=key, Body=content,
                             ContentType=content_type)
        return
    if settings.require_r2:
        raise RuntimeError("R2 is required but its credentials are not configured.")
    path = Path(settings.storage_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)

def get_object(key: str) -> bytes:
    if r2_enabled():
        response = _client().get_object(Bucket=settings.r2_bucket_name, Key=key)
        try:
            return response["Body"].read()
        finally:
            response["Body"].close()
    if settings.require_r2:
        raise FileNotFoundError("R2 is required but its credentials are not configured.")
    return Path(settings.storage_dir, key).read_bytes()

def check_storage() -> str:
    if r2_enabled():
        _client().head_bucket(Bucket=settings.r2_bucket_name)
        return "r2"
    if settings.require_r2:
        raise RuntimeError("R2 is required but its credentials are not configured.")
    Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
    return "local"
