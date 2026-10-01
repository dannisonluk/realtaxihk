"""Object storage (Cloudflare R2 via its S3-compatible API).

The API never proxies an upload. A client asks for a presigned PUT, sends the
bytes straight to R2, then tells the API the key it used. That keeps a 5 MB
licence photo off the API's worker and out of its request timeout, and it means
the bucket — not the application — absorbs the bandwidth.

Two rules the rest of the codebase depends on:

1. **The key is the only thing stored.** `driver_documents.object_key` and
   `users.avatar_key` hold a relative key, never a URL. A signed URL is minted
   on demand with a short TTL, so a leaked database row (a backup, a replica, a
   log of a bad query) cannot be turned into a download of someone's ID.
2. **Keys are server-generated.** The client never chooses the key it will
   later read back. A caller-supplied key is how one account ends up able to
   overwrite — or read — another's document.

Fail-closed: with `APP_ENV=prod` and no credentials, every method raises. A
presign that silently returned a dev URL would hand clients a link that 404s,
which reads to a driver as "the app is broken" rather than "we are unconfigured".
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings
from app.core.exceptions import BusinessRuleError

logger = logging.getLogger(__name__)

# Content types we are willing to sign. A presigned PUT with an arbitrary
# content type is a way to store an HTML page on a domain we control, which is
# then a phishing page served from our own hostname.
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
}

# Per-object ceilings. The avatar limit is a config knob because it is a product
# choice; the document limit is larger because a phone photo of a licence is
# routinely 3-6 MB and rejecting those would push drivers to send a screenshot.
MAX_AVATAR_BYTES = 5 * 1024 * 1024
MAX_DOCUMENT_BYTES = 12 * 1024 * 1024


@dataclass(frozen=True)
class PresignedUpload:
    """Everything the client needs to complete the upload itself.

    `object_key` is what the client must echo back to the API afterwards. The
    headers are the ones that were signed, so the client must send them
    verbatim — R2 rejects a PUT whose `Content-Type` differs from the signed
    one, which is what makes the type allowlist above enforceable.
    """

    upload_url: str
    object_key: str
    expires_in: int
    headers: dict[str, str]


def _extension_for(content_type: str) -> str:
    return ALLOWED_IMAGE_TYPES.get(content_type.lower(), "")


def _assert_content_type(content_type: str) -> str:
    ct = (content_type or "").strip().lower()
    if ct not in ALLOWED_IMAGE_TYPES:
        raise BusinessRuleError(
            f"unsupported image type: {content_type or '(empty)'}",
            {"allowed": sorted(ALLOWED_IMAGE_TYPES)},
        )
    return ct


def _assert_size(size_bytes: int, ceiling: int) -> int:
    if not isinstance(size_bytes, int) or size_bytes <= 0:
        raise BusinessRuleError("file size must be a positive integer")
    if size_bytes > ceiling:
        raise BusinessRuleError(
            f"file is too large (limit {ceiling // (1024 * 1024)} MB)",
            {"size_bytes": size_bytes, "limit_bytes": ceiling},
        )
    return size_bytes


def build_object_key(*, namespace: str, owner_id: str, content_type: str) -> str:
    """A server-generated, unguessable key.

    The UUID is not decoration: it is what stops one driver from reading
    another's document by guessing. `users/<id>/avatar` would be enumerable;
    the random component makes the key itself a capability.

    The namespace and owner are kept as readable prefixes anyway, because an
    operator looking at a bucket needs to know whose data a stray object is
    before acting on a deletion request.
    """
    ext = _extension_for(_assert_content_type(content_type))
    safe_owner = "".join(ch for ch in owner_id if ch.isalnum() or ch in "-_")[:64] or "anon"
    return f"{namespace}/{safe_owner}/{uuid.uuid4().hex}{ext}"


class StorageService:
    """Presigns uploads and (for the console) downloads.

    Wraps `boto3` lazily so importing this module never requires credentials —
    prod-only paths and the test suite both import it, and a module-level client
    would make `import app.services.storage_service` fail without a bucket.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._client: Any = None

    # -- configuration ----------------------------------------------------- #

    @property
    def is_configured(self) -> bool:
        s = self._settings
        return bool(
            s.r2_endpoint_url and s.r2_access_key_id and s.r2_secret_access_key and s.r2_bucket
        )

    def _require_configured(self) -> None:
        if not self.is_configured:
            raise BusinessRuleError(
                "object storage is not configured",
                {
                    "missing": [
                        "R2_ENDPOINT_URL",
                        "R2_ACCESS_KEY_ID",
                        "R2_SECRET_ACCESS_KEY",
                        "R2_BUCKET",
                    ]
                },
            )

    def client(self) -> Any:
        """A lazily-built S3 client.

        Built on first use, not in `__init__`: `get_settings()` is cached but
        `boto3.client` performs credential resolution, and doing that at import
        time turns a missing env var into an import error rather than a handled
        400 from the endpoint that needed storage.
        """
        if self._client is not None:
            return self._client
        self._require_configured()
        import boto3

        s = self._settings
        self._client = boto3.client(
            "s3",
            endpoint_url=s.r2_endpoint_url,
            aws_access_key_id=s.r2_access_key_id,
            aws_secret_access_key=s.r2_secret_access_key,
            region_name="auto",  # R2 ignores it, but SigV4 needs it set
            config=_boto_config(),
        )
        return self._client

    # -- uploads ------------------------------------------------------------ #

    def presign_upload(
        self,
        *,
        namespace: str,
        owner_id: str,
        content_type: str,
        size_bytes: int,
        max_bytes: int,
    ) -> PresignedUpload:
        """A presigned PUT for a server-chosen key.

        The `Content-Length` is NOT part of the signature (R2/S3 cannot sign it
        for a PUT), so the size check here is advisory — it stops an honest
        client from wasting an upload. The real enforcement is at read time:
        `head_object` reports the stored size, and a document larger than the
        ceiling is not signed for download.
        """
        ct = _assert_content_type(content_type)
        _assert_size(size_bytes, max_bytes)
        key = build_object_key(namespace=namespace, owner_id=owner_id, content_type=ct)

        ttl = int(self._settings.upload_url_ttl_s)
        url = self.client().generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self._settings.r2_bucket,
                "Key": key,
                "ContentType": ct,
            },
            ExpiresIn=ttl,
            HttpMethod="PUT",
        )
        logger.info("presigned upload key=%s type=%s ttl=%ss", key, ct, ttl)
        return PresignedUpload(
            upload_url=url,
            object_key=key,
            expires_in=ttl,
            headers={"Content-Type": ct},
        )

    def presign_avatar(
        self, *, user_id: str, content_type: str, size_bytes: int
    ) -> PresignedUpload:
        return self.presign_upload(
            namespace="avatars",
            owner_id=user_id,
            content_type=content_type,
            size_bytes=size_bytes,
            max_bytes=min(MAX_AVATAR_BYTES, int(self._settings.max_avatar_bytes)),
        )

    def presign_document(
        self, *, driver_profile_id: str, content_type: str, size_bytes: int
    ) -> PresignedUpload:
        return self.presign_upload(
            namespace="documents",
            owner_id=driver_profile_id,
            content_type=content_type,
            size_bytes=size_bytes,
            max_bytes=MAX_DOCUMENT_BYTES,
        )

    # -- reads -------------------------------------------------------------- #

    def presign_download(self, *, object_key: str, ttl_s: int = 300) -> str:
        """A short-lived signed GET.

        Shorter than the upload TTL on purpose: an upload link is used
        immediately, whereas a download link for an identity document may sit in
        a browser tab or a chat message. Five minutes is enough for an operator
        to look at the image in front of them.
        """
        if not object_key:
            raise BusinessRuleError("object key is required")
        return self.client().generate_presigned_url(
            "get_object",
            Params={"Bucket": self._settings.r2_bucket, "Key": object_key},
            ExpiresIn=ttl_s,
            HttpMethod="GET",
        )

    def head(self, *, object_key: str) -> dict[str, Any] | None:
        """Size and type of a stored object, or None if it is not there.

        Used before approving a submission: a driver who requested a presigned
        URL and then never uploaded leaves a row pointing at nothing, and an
        operator must not be able to approve an empty document.
        """
        from botocore.exceptions import ClientError

        try:
            resp = self.client().head_object(Bucket=self._settings.r2_bucket, Key=object_key)
        except ClientError as exc:
            code = (exc.response.get("Error") or {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return {
            "size_bytes": int(resp.get("ContentLength") or 0),
            "content_type": resp.get("ContentType") or "",
            "etag": (resp.get("ETag") or "").strip('"'),
        }

    def public_url(self, *, object_key: str) -> str | None:
        """A direct URL, when the bucket has a public host configured.

        Returns None otherwise, so the caller can fall back to a signed
        redirect rather than emitting a link that will 404. Avatars can be
        public; licence documents must not be, and callers are expected to
        honour that by only using this for `avatars/`.
        """
        base = (self._settings.r2_public_base_url or "").rstrip("/")
        if not base or not object_key:
            return None
        return f"{base}/{object_key.lstrip('/')}"


def _boto_config() -> Any:
    """Retries and timeouts for a client that runs inside request handlers.

    Default boto3 behaviour retries for up to ~60 s with no read timeout, which
    in a FastAPI worker means a slow R2 turns one presign into a wedged request.
    """
    from botocore.config import Config

    return Config(
        retries={"max_attempts": 3, "mode": "standard"},
        connect_timeout=5,
        read_timeout=10,
        signature_version="s3v4",
    )


@dataclass(frozen=True)
class DevStorageService:
    """A stand-in used when storage is unconfigured in a non-prod env.

    It mints keys exactly as the real service does, so the rest of the flow
    (store the key, attach it to a submission, validate it on approve) is
    exercised end to end without a bucket. The URLs it returns are explicitly
    unroutable, so a client that tries to use one fails loudly instead of
    appearing to work.
    """

    _base: StorageService

    @property
    def is_configured(self) -> bool:
        return False

    def presign_upload(self, **kwargs: Any) -> PresignedUpload:
        ct = _assert_content_type(kwargs.get("content_type", ""))
        _assert_size(kwargs.get("size_bytes", 0), kwargs.get("max_bytes", MAX_AVATAR_BYTES))
        key = build_object_key(
            namespace=kwargs["namespace"],
            owner_id=kwargs["owner_id"],
            content_type=ct,
        )
        logger.info("[STORAGE:dev] presigned upload key=%s (unroutable URL)", key)
        return PresignedUpload(
            upload_url=f"http://storage.invalid/{key}",
            object_key=key,
            expires_in=900,
            headers={"Content-Type": ct},
        )

    def presign_avatar(
        self, *, user_id: str, content_type: str, size_bytes: int
    ) -> PresignedUpload:
        # The parameter is named `user_id` here to match `StorageService` exactly
        # — a dev stand-in whose signature differs is worse than no stand-in,
        # because the call site type-checks against the real one and then fails
        # only in the env where storage is unconfigured. It maps onto `owner_id`
        # internally.
        return self.presign_upload(
            namespace="avatars",
            owner_id=user_id,
            content_type=content_type,
            size_bytes=size_bytes,
            max_bytes=min(MAX_AVATAR_BYTES, int(self._base._settings.max_avatar_bytes)),
        )

    def presign_document(
        self, *, driver_profile_id: str, content_type: str, size_bytes: int
    ) -> PresignedUpload:
        return self.presign_upload(
            namespace="documents",
            owner_id=driver_profile_id,
            content_type=content_type,
            size_bytes=size_bytes,
            max_bytes=MAX_DOCUMENT_BYTES,
        )

    def presign_download(self, *, object_key: str, ttl_s: int = 300) -> str:
        return f"http://storage.invalid/{object_key}"

    def head(self, *, object_key: str) -> dict[str, Any] | None:
        # Unknown by construction. Reporting a plausible size here would let an
        # operator "approve" a document that was never uploaded.
        return None

    def public_url(self, *, object_key: str) -> str | None:
        return None


def get_storage_service() -> StorageService | DevStorageService:
    """The seam every caller goes through.

    Mirrors `get_email_provider` in `notify.py`: a dev stand-in for dev/test,
    and a real implementation that raises when unconfigured elsewhere. Tests
    that need to assert on a presign must patch **the consuming module's**
    binding, not this one — `from x import f` binds the function object.
    """
    svc = StorageService()
    if get_settings().app_env in ("dev", "test") and not svc.is_configured:
        return DevStorageService(svc)
    return svc


__all__ = [
    "ALLOWED_IMAGE_TYPES",
    "MAX_AVATAR_BYTES",
    "MAX_DOCUMENT_BYTES",
    "DevStorageService",
    "PresignedUpload",
    "StorageService",
    "build_object_key",
    "get_storage_service",
]
