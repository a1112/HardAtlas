from typing import Any, Protocol

from botocore.exceptions import ClientError


class SnapshotStore(Protocol):
    def put(
        self,
        *,
        key: str,
        content: bytes,
        media_type: str,
        metadata: dict[str, str],
    ) -> None: ...

    def get(self, *, key: str) -> bytes: ...


def _validate_snapshot_key(key: str) -> None:
    if not key.startswith("sources/") or ".." in key.split("/"):
        raise ValueError("snapshot storage key is outside the sources prefix")


class S3SnapshotStore:
    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str = "us-east-1",
        client: Any | None = None,
    ) -> None:
        self.bucket = bucket
        if client is None:
            import boto3

            client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
            )
        self.client = client

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code not in {"404", "NoSuchBucket", "NotFound"}:
                raise
            self.client.create_bucket(Bucket=self.bucket)

    def put(
        self,
        *,
        key: str,
        content: bytes,
        media_type: str,
        metadata: dict[str, str],
    ) -> None:
        _validate_snapshot_key(key)
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType=media_type,
            Metadata=metadata,
        )

    def get(self, *, key: str) -> bytes:
        _validate_snapshot_key(key)
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        body = response["Body"]
        content = body.read()
        if not isinstance(content, bytes):
            raise TypeError("snapshot object body must return bytes")
        return content
