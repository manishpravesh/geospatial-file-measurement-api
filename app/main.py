"""Application entry point."""

from fastapi import FastAPI

from app import __version__
from app.api.routes import router
from app.config import Settings, get_settings
from app.database import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="Geospatial File Measurement API",
        version=__version__,
        summary="Upload a shapefile or KML file and measure its features.",
        description=(
            "Accepts a zipped shapefile, a KML file, or a KMZ archive. "
            "Polygon area and line length are calculated in metres after transforming "
            "geographic coordinates into a projected coordinate system."
        ),
    )
    app.state.settings = settings
    app.state.database = Database(settings)
    app.include_router(router, prefix="/api")

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {
            "service": "Geospatial File Measurement API",
            "health": "/health",
            "docs": "/docs",
            "upload": "/api/files/",
        }

    return app


app = create_app()
