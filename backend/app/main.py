from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import backtest, data, health, strategies
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

    app.include_router(health.router, tags=["health"])
    app.include_router(data.router, prefix="/api/data", tags=["data"])
    app.include_router(strategies.router, prefix="/api/strategies", tags=["strategies"])
    app.include_router(backtest.router, prefix="/api/backtests", tags=["backtests"])

    @app.on_event("startup")
    def on_startup() -> None:
        init_db()

    return app


app = create_app()

