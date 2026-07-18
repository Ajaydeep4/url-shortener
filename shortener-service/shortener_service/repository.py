from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from shortener_service.db.models import Url


class UrlRepository:
    """Data access for the urls table.

    All uniqueness guarantees are enforced by PostgreSQL, not application code,
    so they hold across any number of service replicas.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(
        self, alias: str, long_url: str, is_custom: bool, expires_at=None
    ) -> Url | None:
        """Atomically insert a mapping.

        Uses INSERT ... ON CONFLICT (alias) DO NOTHING RETURNING so that
        concurrent requests for the same alias cannot race: exactly one insert
        wins and every loser observes ``None``. There is no check-then-insert
        window.
        """
        stmt = (
            pg_insert(Url)
            .values(alias=alias, long_url=long_url, is_custom=is_custom, expires_at=expires_at)
            .on_conflict_do_nothing(index_elements=["alias"])
            .returning(Url)
        )
        result = await self._session.execute(stmt)
        row = result.scalars().first()
        await self._session.commit()
        return row

    async def get_by_alias(self, alias: str) -> Url | None:
        result = await self._session.execute(select(Url).where(Url.alias == alias))
        return result.scalars().first()
