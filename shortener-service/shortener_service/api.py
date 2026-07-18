from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from shortener_service.config import Settings, get_settings
from shortener_service.db.session import get_session
from shortener_service.repository import UrlRepository
from shortener_service.schemas import (
    CreateUrlRequest,
    ErrorResponse,
    UrlMetadataResponse,
    UrlResponse,
)
from shortener_service.service import ShortenerService

router = APIRouter(prefix="/api/v1/urls", tags=["urls"])


def get_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ShortenerService:
    return ShortenerService(
        UrlRepository(session),
        base_url=settings.base_url,
        alias_length=settings.alias_length,
        max_retries=settings.alias_max_retries,
    )


ServiceDep = Annotated[ShortenerService, Depends(get_service)]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=UrlResponse,
    responses={
        409: {"model": ErrorResponse, "description": "Alias already taken"},
        422: {"model": ErrorResponse, "description": "Validation error"},
        503: {"model": ErrorResponse, "description": "Alias generation exhausted"},
    },
)
async def create_short_url(body: CreateUrlRequest, service: ServiceDep) -> UrlResponse:
    row = await service.create_short_url(str(body.long_url), body.custom_alias, body.ttl_seconds)
    return UrlResponse(
        alias=row.alias,
        short_url=service.short_url_for(row.alias),
        long_url=row.long_url,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )


@router.get(
    "/{alias}",
    response_model=UrlMetadataResponse,
    responses={404: {"model": ErrorResponse, "description": "Unknown alias"}},
)
async def get_url_metadata(alias: str, service: ServiceDep) -> UrlMetadataResponse:
    row = await service.get_metadata(alias)
    return UrlMetadataResponse(
        alias=row.alias,
        short_url=service.short_url_for(row.alias),
        long_url=row.long_url,
        created_at=row.created_at,
        expires_at=row.expires_at,
        access_count=row.access_count,
        is_custom=row.is_custom,
    )
