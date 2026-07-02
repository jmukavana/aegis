from collections.abc import AsyncGenerator

from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from src.infrastructure.config import Settings


def create_db_engine(settings: Settings) -> AsyncEngine:
    engine = create_async_engine(settings.database_url)
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
    return engine


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def get_session_dependency(session_factory: async_sessionmaker[AsyncSession]):
    async def _get_session() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    return _get_session
