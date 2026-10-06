"""Request dependencies."""

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session

from app.config import Settings


def get_db(request: Request) -> Generator[Session, None, None]:
    yield from request.app.state.database.session()


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings
