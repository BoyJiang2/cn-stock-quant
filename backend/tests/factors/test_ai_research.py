from datetime import date

import math
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai_research import build_composite_factor, to_qlib_frame
from app.core.database import get_session
from app.main import create_app
from app.models.entities import Base, DailyBar, IndexDailyBar, Stock


def _panel() -> pd.DataFrame:
    index = pd.MultiIndex.from_product(
        [[date(2024, 1, 1), date(2024, 1, 2)], ["000001", "000002", "000003"]],
        names=["trade_date", "symbol"],
    )
    return pd.DataFrame(
        {
            "momentum_20d": [1.0, 2.0, 3.0, 2.0, 3.0, 4.0],
            "volatility_20d": [3.0, 2.0, 1.0, 4.0, 3.0, 2.0],
        },
        index=index,
    )


def test_composite_orients_lower_volatility_as_better():
    composite = build_composite_factor(
        _panel(),
        {"momentum_20d": 1.0, "volatility_20d": 1.0},
    )

    assert composite.loc[(date(2024, 1, 1), "000003")] > composite.loc[
        (date(2024, 1, 1), "000001")
    ]


def test_composite_rejects_unknown_or_non_finite_weights():
    with pytest.raises(ValueError, match="unknown factor"):
        build_composite_factor(_panel(), {"future_magic": 1.0})
    with pytest.raises(ValueError, match="finite"):
        build_composite_factor(_panel(), {"momentum_20d": math.inf})


def test_qlib_adapter_uses_datetime_and_instrument_columns():
    frame = to_qlib_frame(_panel())

    assert list(frame.columns[:2]) == ["datetime", "instrument"]
    assert pd.api.types.is_datetime64_any_dtype(frame["datetime"])
    assert frame["instrument"].iloc[0] == "000001"


def test_ai_research_capabilities_disallow_arbitrary_code():
    response = TestClient(create_app()).get("/api/ai-research/capabilities")

    assert response.status_code == 200
    assert response.json()["arbitrary_code_execution"] is False


def test_market_regime_endpoint_returns_non_trading_context():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True)
    session = factory()
    start = date(2024, 1, 1)
    try:
        session.add(Stock(symbol="000001", name="Test", exchange="SZ", status="active"))
        for day in range(160):
            trade_date = start + pd.Timedelta(days=day)
            normalized_date = (
                trade_date.date() if hasattr(trade_date, "date") else trade_date
            )
            close = 100.0 + day * 0.2
            session.add(
                IndexDailyBar(
                    symbol="000300",
                    trade_date=normalized_date,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=1000,
                    amount=10000,
                )
            )
            session.add(
                DailyBar(
                    symbol="000001",
                    trade_date=normalized_date,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=1000,
                    amount=10000,
                    adj="qfq",
                )
            )
        session.commit()
    finally:
        session.close()

    app = create_app()

    def override_session():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_session
    response = TestClient(app).post(
        "/api/ai-research/market-regime",
        json={
            "benchmark_symbol": "000300",
            "as_of_date": "2024-06-08",
            "lookback_calendar_days": 200,
            "include_market_breadth": True,
            "breadth_max_symbols": 1000,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["regime"] in {"BULL", "BEAR", "SIDEWAYS", "PANIC", "EUPHORIA"}
    assert body["breadth_symbol_count"] == 1
    assert body["can_trade_directly"] is False
    assert "不要生成买卖订单" in body["llm_context"]["content"]
