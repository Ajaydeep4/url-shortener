from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl


class CreateUrlRequest(BaseModel):
    long_url: HttpUrl = Field(..., description="Destination URL (http or https)")
    custom_alias: str | None = Field(
        default=None,
        max_length=64,
        description="Optional custom alias; 3-32 chars of [a-zA-Z0-9_-]",
    )
    ttl_seconds: int | None = Field(
        default=None,
        ge=1,
        le=315_360_000,  # 10 years
        description="Optional lifetime in seconds; omit for a link that never expires",
    )


class UrlResponse(BaseModel):
    alias: str
    short_url: str
    long_url: str
    created_at: datetime
    expires_at: datetime | None = None


class UrlMetadataResponse(UrlResponse):
    access_count: int
    is_custom: bool


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
