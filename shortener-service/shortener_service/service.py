import logging
import re
import secrets
import string
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from shortener_service.db.models import Url
from shortener_service.exceptions import (
    AliasAlreadyExistsError,
    AliasGenerationExhaustedError,
    AliasNotFoundError,
    InvalidAliasError,
    InvalidUrlError,
)
from shortener_service.repository import UrlRepository

logger = logging.getLogger(__name__)

ALIAS_ALPHABET = string.ascii_letters + string.digits  # base62
CUSTOM_ALIAS_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{3,32}$")
# Path prefixes owned by the platform; letting users claim them would shadow
# real routes on the gateway or the services.
RESERVED_ALIASES = frozenset(
    {"api", "docs", "redoc", "openapi.json", "health", "healthz", "metrics", "static", "favicon.ico"}
)


def generate_alias(length: int) -> str:
    """Cryptographically random base62 alias (no modulo bias via secrets.choice)."""
    return "".join(secrets.choice(ALIAS_ALPHABET) for _ in range(length))


def validate_custom_alias(alias: str) -> None:
    if not CUSTOM_ALIAS_PATTERN.match(alias):
        raise InvalidAliasError(
            "custom_alias must be 3-32 characters of letters, digits, hyphen or underscore"
        )
    if alias.lower() in RESERVED_ALIASES:
        raise InvalidAliasError(f"custom_alias '{alias}' is reserved")


class ShortenerService:
    def __init__(
        self,
        repository: UrlRepository,
        *,
        base_url: str,
        alias_length: int = 7,
        max_retries: int = 5,
    ) -> None:
        self._repository = repository
        self._base_url = base_url.rstrip("/")
        self._alias_length = alias_length
        self._max_retries = max_retries

    def short_url_for(self, alias: str) -> str:
        return f"{self._base_url}/{alias}"

    def _validate_long_url(self, long_url: str) -> None:
        parsed = urlparse(long_url)
        base = urlparse(self._base_url)
        if parsed.netloc and parsed.netloc == base.netloc:
            raise InvalidUrlError("refusing to shorten a URL that points at this service")

    async def create_short_url(
        self,
        long_url: str,
        custom_alias: str | None = None,
        ttl_seconds: int | None = None,
    ) -> Url:
        self._validate_long_url(long_url)
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=ttl_seconds) if ttl_seconds is not None else None
        )

        if custom_alias is not None:
            validate_custom_alias(custom_alias)
            row = await self._repository.insert(
                custom_alias, long_url, is_custom=True, expires_at=expires_at
            )
            if row is None:
                # A concurrent or earlier request already claimed it; the DB
                # unique index guarantees exactly one winner.
                raise AliasAlreadyExistsError(f"alias '{custom_alias}' is already taken")
            return row

        for attempt in range(1, self._max_retries + 1):
            alias = generate_alias(self._alias_length)
            if alias.lower() in RESERVED_ALIASES:
                continue
            row = await self._repository.insert(
                alias, long_url, is_custom=False, expires_at=expires_at
            )
            if row is not None:
                return row
            logger.warning(
                "generated alias collided, retrying",
                extra={"extra_fields": {"alias": alias, "attempt": attempt}},
            )
        # With 62^7 possible aliases this indicates either extreme keyspace
        # saturation or a systemic fault; surface as a retryable 503.
        raise AliasGenerationExhaustedError(
            f"could not generate a unique alias after {self._max_retries} attempts"
        )

    async def get_metadata(self, alias: str) -> Url:
        row = await self._repository.get_by_alias(alias)
        if row is None:
            raise AliasNotFoundError(f"alias '{alias}' does not exist")
        return row
