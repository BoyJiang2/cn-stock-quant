from app.portfolio.trade_plan import build_trade_plan
from app.risk.rules import RiskConfig, RiskEngine
from app.data.symbols import normalize_a_share_symbol


def test_risk_engine_caps_weights():
    decision = RiskEngine().evaluate(
        {"000001": 0.8, "600000": 0.8, "300001": 0.2},
        RiskConfig(max_symbol_weight=0.3, max_total_weight=0.5),
    )
    assert round(sum(decision.accepted.values()), 6) <= 0.5
    assert decision.accepted["000001"] < 0.3


def test_build_trade_plan_uses_lot_size():
    plan = build_trade_plan(
        positions={"000001": 100},
        target_weights={"000001": 0.5},
        latest_prices={"000001": 10.0},
        total_equity=10000,
    )
    assert plan[0].side == "buy"
    assert plan[0].quantity == 400


def test_normalize_a_share_symbol():
    assert normalize_a_share_symbol("000001.SZ") == "000001"
    assert normalize_a_share_symbol("sz000001") == "000001"
    assert normalize_a_share_symbol("SH600000") == "600000"
