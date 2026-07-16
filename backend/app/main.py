from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    advisory,
    ai_research,
    backtest,
    data,
    factors,
    health,
    pit,
    portfolio,
    strategies,
)
from app.core.config import PROJECT_ROOT
from app.core.database import init_db


def create_app() -> FastAPI:
    app = FastAPI(title="CN Stock Quant API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(data.router, prefix="/api/data", tags=["data"])
    app.include_router(strategies.router, prefix="/api/strategies", tags=["strategies"])
    app.include_router(backtest.router, prefix="/api/backtests", tags=["backtests"])
    app.include_router(factors.router, prefix="/api/factors", tags=["factors"])
    app.include_router(ai_research.router, prefix="/api/ai-research", tags=["ai-research"])
    app.include_router(advisory.router, prefix="/api/advisory", tags=["advisory"])
    app.include_router(portfolio.router, prefix="/api/portfolio", tags=["portfolio"])
    app.include_router(pit.router, prefix="/api/data/pit", tags=["pit"])

    frontend_dist = PROJECT_ROOT / "frontend" / "dist"
    assets_dir = frontend_dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    if (frontend_dist / "index.html").is_file():
        @app.get("/", include_in_schema=False)
        def frontend_index() -> FileResponse:
            return FileResponse(frontend_dist / "index.html")

    app.include_router(health.router, tags=["health"])

    @app.on_event("startup")
    def on_startup() -> None:
        init_db()

    return app


app = create_app()
