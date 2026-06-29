"""Unit tests for AkShareProvider and supporting modules.

All tests mock the ``akshare`` library via ``sys.modules`` — no network
access is performed.
"""

from __future__ import annotations

import sys
from datetime import date
from unittest.mock import ANY, MagicMock, call, patch

import pandas as pd
import pytest

from app.data.errors import (
    DateParseError,
    InvalidPriceError,
    MissingColumnError,
    NaNValueError,
    OHLCContradictionError,
    ProviderUnavailableError,
)

# ---------------------------------------------------------------------------
# Ensure the mocked akshare is always in sys.modules before any import
# of app.data.akshare_provider (which does ``import akshare as ak`` inside
# method bodies).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_akshare_module() -> MagicMock:
    """Replace the real ``akshare`` with a MagicMock for every test."""
    mock_ak = MagicMock(name="akshare")
    original = sys.modules.get("akshare")
    sys.modules["akshare"] = mock_ak
    yield mock_ak
    if original is not None:
        sys.modules["akshare"] = original
    else:
        sys.modules.pop("akshare", None)


# Lazy import so the fixture has already patched sys.modules.
def _provider():
    from app.data.akshare_provider import AkShareProvider

    return AkShareProvider()


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

CHINESE_COLS = ["日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额"]
ENGLISH_COLS = ["date", "open", "close", "high", "low", "volume", "amount"]

STANDARD_COLS = [
    "symbol", "trade_date", "open", "high", "low", "close", "volume", "amount", "adj",
]

TEST_DATES = (date(2024, 1, 2), date(2024, 1, 5))


def _chinese_row(
    trade_date="2024-01-02", open_=10.0, high=10.5, low=9.8, close=10.2, volume=100000, amount=1_020_000
) -> dict:
    return {
        "日期": trade_date,
        "开盘": open_,
        "最高": high,
        "最低": low,
        "收盘": close,
        "成交量": volume,
        "成交额": amount,
    }


def _english_row(
    date_="2024-01-02", open_=10.0, high=10.5, low=9.8, close=10.2, volume=100000, amount=1_020_000
) -> dict:
    return {
        "date": date_,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": amount,
    }


# ===================================================================
# column_map tests
# ===================================================================


class TestBuildColumnMap:
    def test_chinese_columns(self):
        from app.data.column_map import build_column_map

        mapping = build_column_map(CHINESE_COLS)
        assert mapping == {
            "日期": "trade_date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "amount",
        }

    def test_english_columns(self):
        from app.data.column_map import build_column_map

        mapping = build_column_map(ENGLISH_COLS)
        assert mapping == {
            "date": "trade_date",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
            "amount": "amount",
        }

    def test_mixed_aliases(self):
        """First alias wins; unrecognised columns are ignored."""
        from app.data.column_map import build_column_map

        mapping = build_column_map(["日期", "date", "openPrice", "成交量"])
        assert mapping["日期"] == "trade_date"  # 日期 comes before date in aliases
        assert mapping["openPrice"] == "open"
        assert mapping["成交量"] == "volume"
        assert "date" not in mapping  # trade_date already mapped via 日期

    def test_unknown_columns_ignored(self):
        from app.data.column_map import build_column_map

        mapping = build_column_map(["foo", "bar", "日期", "open"])
        assert mapping == {"日期": "trade_date", "open": "open"}
        assert "foo" not in mapping
        assert "bar" not in mapping

    def test_apply_column_map_returns_only_standard_columns(self):
        from app.data.column_map import apply_column_map, build_column_map

        df = pd.DataFrame([_chinese_row()])
        mapping = build_column_map(list(df.columns))
        result = apply_column_map(df, mapping)
        assert set(result.columns) == {"trade_date", "open", "high", "low", "close", "volume", "amount"}


# ===================================================================
# validation tests
# ===================================================================


class TestValidateDailyBars:
    def test_valid_data_passes(self):
        from app.data.validation import validate_daily_bars

        df = pd.DataFrame([{
            "trade_date": date(2024, 1, 2),
            "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2,
            "volume": 100000, "amount": 1_020_000,
        }])
        validate_daily_bars(df, source_label="test")  # no exception

    def test_zero_volume_allowed(self):
        from app.data.validation import validate_daily_bars

        df = pd.DataFrame([{
            "trade_date": date(2024, 1, 2),
            "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2,
            "volume": 0.0, "amount": 0.0,
        }])
        validate_daily_bars(df, source_label="test")  # no exception

    def test_missing_column_raises(self):
        from app.data.errors import MissingColumnError
        from app.data.validation import validate_daily_bars

        df = pd.DataFrame([{"trade_date": date(2024, 1, 2), "open": 10.0}])
        with pytest.raises(MissingColumnError) as exc_info:
            validate_daily_bars(df, source_label="s1")
        assert "high" in str(exc_info.value)
        assert exc_info.value.source == "s1"
        assert exc_info.value.missing
        assert "trade_date" in exc_info.value.present

    def test_nan_value_raises(self):
        from app.data.errors import NaNValueError
        from app.data.validation import validate_daily_bars

        df = pd.DataFrame([{
            "trade_date": date(2024, 1, 2),
            "open": float("nan"), "high": 10.5, "low": 9.8, "close": 10.2,
            "volume": 100000, "amount": 1_020_000,
        }])
        with pytest.raises(NaNValueError) as exc_info:
            validate_daily_bars(df, source_label="s2")
        assert exc_info.value.column == "open"
        assert exc_info.value.source == "s2"

    def test_non_positive_price_raises(self):
        from app.data.errors import InvalidPriceError
        from app.data.validation import validate_daily_bars

        df = pd.DataFrame([{
            "trade_date": date(2024, 1, 2),
            "open": 10.0, "high": 0.0, "low": 9.8, "close": 10.2,
            "volume": 100000, "amount": 1_020_000,
        }])
        with pytest.raises(InvalidPriceError) as exc_info:
            validate_daily_bars(df, source_label="s3")
        assert exc_info.value.column == "high"

    def test_negative_price_raises(self):
        from app.data.errors import InvalidPriceError
        from app.data.validation import validate_daily_bars

        df = pd.DataFrame([{
            "trade_date": date(2024, 1, 2),
            "open": -5.0, "high": 10.5, "low": 9.8, "close": 10.2,
            "volume": 100000, "amount": 1_020_000,
        }])
        with pytest.raises(InvalidPriceError) as exc_info:
            validate_daily_bars(df, source_label="test")
        assert exc_info.value.column == "open"

    def test_ohlc_contradiction_high_less_than_low(self):
        from app.data.errors import OHLCContradictionError
        from app.data.validation import validate_daily_bars

        df = pd.DataFrame([{
            "trade_date": date(2024, 1, 2),
            "open": 10.0, "high": 9.0, "low": 11.0, "close": 10.2,
            "volume": 100000, "amount": 1_020_000,
        }])
        with pytest.raises(OHLCContradictionError) as exc_info:
            validate_daily_bars(df, source_label="s4")
        assert "high < max" in str(exc_info.value)
        assert exc_info.value.source == "s4"

    def test_ohlc_contradiction_low_greater_than_high(self):
        from app.data.errors import OHLCContradictionError
        from app.data.validation import validate_daily_bars

        df = pd.DataFrame([{
            "trade_date": date(2024, 1, 2),
            "open": 10.0, "high": 10.5, "low": 10.6, "close": 10.2,
            "volume": 100000, "amount": 1_020_000,
        }])
        with pytest.raises(OHLCContradictionError):
            validate_daily_bars(df)

    def test_ohlc_contradiction_high_below_open(self):
        from app.data.errors import OHLCContradictionError
        from app.data.validation import validate_daily_bars

        df = pd.DataFrame([{
            "trade_date": date(2024, 1, 2),
            "open": 11.0, "high": 10.5, "low": 9.8, "close": 10.2,
            "volume": 100000, "amount": 1_020_000,
        }])
        with pytest.raises(OHLCContradictionError):
            validate_daily_bars(df)

    def test_ohlc_contradiction_low_above_close(self):
        from app.data.errors import OHLCContradictionError
        from app.data.validation import validate_daily_bars

        df = pd.DataFrame([{
            "trade_date": date(2024, 1, 2),
            "open": 10.0, "high": 10.5, "low": 10.3, "close": 10.1,
            "volume": 100000, "amount": 1_020_000,
        }])
        with pytest.raises(OHLCContradictionError):
            validate_daily_bars(df)

    def test_equal_ohlc_is_valid(self):
        """All prices equal is a valid scenario (e.g. limit-up/down day)."""
        from app.data.validation import validate_daily_bars

        df = pd.DataFrame([{
            "trade_date": date(2024, 1, 2),
            "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
            "volume": 100000, "amount": 1_000_000,
        }])
        validate_daily_bars(df)  # no exception


# ===================================================================
# AkShareProvider tests
# ===================================================================


class TestDailyBarsSuccess:
    def test_main_source_chinese_columns(self, mock_akshare_module):
        """Primary source returns Chinese columns → standardised output."""
        mock_akshare_module.stock_zh_a_hist.return_value = pd.DataFrame([
            _chinese_row("2024-01-02", 10.0, 10.5, 9.8, 10.2),
            _chinese_row("2024-01-03", 10.2, 10.8, 10.1, 10.6),
        ])

        result = _provider().daily_bars("000001", *TEST_DATES)

        assert list(result.columns) == STANDARD_COLS
        assert len(result) == 2
        assert result.iloc[0]["symbol"] == "000001"
        assert result.iloc[0]["trade_date"] == date(2024, 1, 2)
        assert result.iloc[0]["open"] == 10.0
        assert result.iloc[0]["high"] == 10.5
        assert result.iloc[0]["low"] == 9.8
        assert result.iloc[0]["close"] == 10.2
        assert result.iloc[0]["volume"] == 100000
        assert result.iloc[0]["amount"] == 1_020_000
        assert result.iloc[0]["adj"] == "qfq"

    def test_main_source_preserves_adjust_param(self, mock_akshare_module):
        mock_akshare_module.stock_zh_a_hist.return_value = pd.DataFrame([
            _chinese_row("2024-01-02", 10.0, 10.5, 9.8, 10.2),
        ])

        result = _provider().daily_bars("000001", *TEST_DATES, adjust="hfq")
        assert result.iloc[0]["adj"] == "hfq"

    @patch('app.data.akshare_provider.sleep')
    def test_fallback_english_columns(self, mock_sleep, mock_akshare_module):
        """Main fails 3×, fallback returns English columns."""
        mock_akshare_module.stock_zh_a_hist.side_effect = ConnectionError("timeout")
        mock_akshare_module.stock_zh_a_daily.return_value = pd.DataFrame([
            _english_row("2024-01-02", 10.0, 10.5, 9.8, 10.2),
        ])

        result = _provider().daily_bars("000001", *TEST_DATES)

        assert list(result.columns) == STANDARD_COLS
        assert len(result) == 1
        assert result.iloc[0]["symbol"] == "000001"
        assert result.iloc[0]["trade_date"] == date(2024, 1, 2)
        assert mock_akshare_module.stock_zh_a_hist.call_count == 3
        mock_akshare_module.stock_zh_a_daily.assert_called_once()

    @patch('app.data.akshare_provider.sleep')
    def test_fallback_called_with_prefixed_symbol(self, mock_sleep, mock_akshare_module):
        """Fallback API receives exchange-prefixed symbol."""
        mock_akshare_module.stock_zh_a_hist.side_effect = ConnectionError("fail")
        mock_akshare_module.stock_zh_a_daily.return_value = pd.DataFrame([
            _english_row("2024-01-02", 10.0, 10.5, 9.8, 10.2),
        ])

        _provider().daily_bars("000001", *TEST_DATES)
        call_kwargs = mock_akshare_module.stock_zh_a_daily.call_args.kwargs
        assert call_kwargs["symbol"] == "sz000001"

    def test_symbol_normalised_for_main_source(self, mock_akshare_module):
        mock_akshare_module.stock_zh_a_hist.return_value = pd.DataFrame([
            _chinese_row("2024-01-02", 10.0, 10.5, 9.8, 10.2),
        ])

        _provider().daily_bars("SZ000001", *TEST_DATES)
        call_kwargs = mock_akshare_module.stock_zh_a_hist.call_args.kwargs
        assert call_kwargs["symbol"] == "000001"

    def test_zero_volume_allowed(self, mock_akshare_module):
        mock_akshare_module.stock_zh_a_hist.return_value = pd.DataFrame([
            _chinese_row("2024-01-02", 10.0, 10.5, 9.8, 10.2, volume=0, amount=0),
        ])

        result = _provider().daily_bars("000001", *TEST_DATES)
        assert len(result) == 1
        assert result.iloc[0]["volume"] == 0.0


class TestDailyBarsRetryAndFallback:
    @patch('app.data.akshare_provider.sleep')
    def test_retry_succeeds_on_third_attempt(self, mock_sleep, mock_akshare_module):
        """First two calls fail, third succeeds — no fallback needed."""
        mock_akshare_module.stock_zh_a_hist.side_effect = [
            ConnectionError("attempt 1"),
            ConnectionError("attempt 2"),
            pd.DataFrame([_chinese_row("2024-01-02", 10.0, 10.5, 9.8, 10.2)]),
        ]

        result = _provider().daily_bars("000001", *TEST_DATES)

        assert len(result) == 1
        assert mock_akshare_module.stock_zh_a_hist.call_count == 3
        mock_akshare_module.stock_zh_a_daily.assert_not_called()

    @patch('app.data.akshare_provider.sleep')
    def test_fallback_after_all_retries_exhausted(self, mock_sleep, mock_akshare_module):
        mock_akshare_module.stock_zh_a_hist.side_effect = ConnectionError("fail")
        mock_akshare_module.stock_zh_a_daily.return_value = pd.DataFrame([
            _english_row("2024-01-02", 10.0, 10.5, 9.8, 10.2),
        ])

        result = _provider().daily_bars("000001", *TEST_DATES)
        assert len(result) == 1
        assert mock_akshare_module.stock_zh_a_hist.call_count == 3
        mock_akshare_module.stock_zh_a_daily.assert_called_once()

    @patch('app.data.akshare_provider.sleep')
    def test_both_sources_fail_preserves_error_context(self, mock_sleep, mock_akshare_module):
        mock_akshare_module.stock_zh_a_hist.side_effect = TimeoutError("main timeout")
        mock_akshare_module.stock_zh_a_daily.side_effect = ValueError("fallback bad")

        with pytest.raises(ProviderUnavailableError) as exc_info:
            _provider().daily_bars("000001", *TEST_DATES)

        msg = str(exc_info.value)
        assert "main(stock_zh_a_hist)" in msg
        assert "main timeout" in msg
        assert "fallback(stock_zh_a_daily)" in msg
        assert "fallback bad" in msg
        assert "000001" in msg

    @patch('app.data.akshare_provider.sleep')
    def test_both_sources_fail_fallback_exception_only(self, mock_sleep, mock_akshare_module):
        """Main succeeds (returns None after all retries raise), fallback
        also raises — the error must mention both."""
        mock_akshare_module.stock_zh_a_hist.side_effect = RuntimeError("a")
        mock_akshare_module.stock_zh_a_daily.side_effect = RuntimeError("b")

        with pytest.raises(ProviderUnavailableError) as exc_info:
            _provider().daily_bars("600000", *TEST_DATES)
        msg = str(exc_info.value)
        assert "main" in msg
        assert "a" in msg
        assert "fallback" in msg
        assert "b" in msg

    @patch('app.data.akshare_provider.sleep')
    def test_main_fails_fallback_validation_fails(self, mock_sleep, mock_akshare_module):
        """Main source fails → fallback returns bad data → validation error
        with fallback source label."""
        from app.data.errors import InvalidPriceError

        mock_akshare_module.stock_zh_a_hist.side_effect = ConnectionError("main down")
        mock_akshare_module.stock_zh_a_daily.return_value = pd.DataFrame([
            _english_row("2024-01-02", open_=-1.0),  # invalid price
        ])

        with pytest.raises(InvalidPriceError) as exc_info:
            _provider().daily_bars("000001", *TEST_DATES)
        assert exc_info.value.source == "akshare.stock_zh_a_daily"


class TestDailyBarsValidationErrors:
    def test_missing_column_from_main_source(self, mock_akshare_module):
        from app.data.errors import MissingColumnError

        # stock_zh_a_hist returns frame without 最高 (high)
        df = pd.DataFrame([_chinese_row("2024-01-02", 10.0, 10.5, 9.8, 10.2)])
        df = df.drop(columns=["最高"])
        mock_akshare_module.stock_zh_a_hist.return_value = df

        with pytest.raises(MissingColumnError) as exc_info:
            _provider().daily_bars("000001", *TEST_DATES)
        assert "high" in exc_info.value.missing
        assert exc_info.value.source == "akshare.stock_zh_a_hist"

    @patch('app.data.akshare_provider.sleep')
    def test_nan_value_from_fallback(self, mock_sleep, mock_akshare_module):
        from app.data.errors import NaNValueError

        mock_akshare_module.stock_zh_a_hist.side_effect = ConnectionError("fail")
        mock_akshare_module.stock_zh_a_daily.return_value = pd.DataFrame([
            _english_row("2024-01-02", open_=float("nan")),
        ])

        with pytest.raises(NaNValueError) as exc_info:
            _provider().daily_bars("000001", *TEST_DATES)
        assert exc_info.value.column == "open"
        assert exc_info.value.source == "akshare.stock_zh_a_daily"

    def test_non_positive_price_from_main_source(self, mock_akshare_module):
        from app.data.errors import InvalidPriceError

        mock_akshare_module.stock_zh_a_hist.return_value = pd.DataFrame([
            _chinese_row("2024-01-02", close=0.0),
        ])

        with pytest.raises(InvalidPriceError) as exc_info:
            _provider().daily_bars("000001", *TEST_DATES)
        assert exc_info.value.column == "close"
        assert exc_info.value.source == "akshare.stock_zh_a_hist"

    def test_ohlc_contradiction_from_main_source(self, mock_akshare_module):
        from app.data.errors import OHLCContradictionError

        mock_akshare_module.stock_zh_a_hist.return_value = pd.DataFrame([
            _chinese_row("2024-01-02", 10.0, high=5.0, low=12.0, close=10.2),
        ])

        with pytest.raises(OHLCContradictionError) as exc_info:
            _provider().daily_bars("000001", *TEST_DATES)
        assert exc_info.value.source == "akshare.stock_zh_a_hist"


class TestDailyBarsEdgeCases:
    def test_empty_result_from_main(self, mock_akshare_module):
        """Empty DataFrame from source → empty standardized DataFrame."""
        mock_akshare_module.stock_zh_a_hist.return_value = pd.DataFrame(
            columns=CHINESE_COLS
        )

        result = _provider().daily_bars("000001", *TEST_DATES)
        assert list(result.columns) == STANDARD_COLS
        assert len(result) == 0

    @patch('app.data.akshare_provider.sleep')
    def test_empty_result_from_fallback(self, mock_sleep, mock_akshare_module):
        mock_akshare_module.stock_zh_a_hist.side_effect = ConnectionError("fail")
        mock_akshare_module.stock_zh_a_daily.return_value = pd.DataFrame(
            columns=ENGLISH_COLS
        )

        result = _provider().daily_bars("000001", *TEST_DATES)
        assert list(result.columns) == STANDARD_COLS
        assert len(result) == 0

    def test_column_map_handles_extra_source_columns(self, mock_akshare_module):
        """Extra columns from source are ignored."""
        df = pd.DataFrame([_chinese_row("2024-01-02", 10.0, 10.5, 9.8, 10.2)])
        df["流通市值"] = 1e9  # extra column not in our aliases
        mock_akshare_module.stock_zh_a_hist.return_value = df

        result = _provider().daily_bars("000001", *TEST_DATES)
        assert list(result.columns) == STANDARD_COLS

    def test_partial_alias_coverage(self, mock_akshare_module):
        """Source uses non-standard column names that aren't in any alias list."""
        from app.data.errors import MissingColumnError

        mock_akshare_module.stock_zh_a_hist.return_value = pd.DataFrame([{
            "day": "2024-01-02",
            "o": 10.0,
            "h": 10.5,
            "l": 9.8,
            "c": 10.2,
            "vol": 100000,
        }])
        # "o", "h", "l", "c", "vol" aren't aliased → missing after mapping

        with pytest.raises(MissingColumnError):
            _provider().daily_bars("000001", *TEST_DATES)

    def test_date_column_is_parsed_to_date_type(self, mock_akshare_module):
        mock_akshare_module.stock_zh_a_hist.return_value = pd.DataFrame([
            _chinese_row("2024-01-02", 10.0, 10.5, 9.8, 10.2),
        ])

        result = _provider().daily_bars("000001", *TEST_DATES)
        assert isinstance(result.iloc[0]["trade_date"], date)

    def test_date_parse_error(self, mock_akshare_module):
        """Unparseable trade_date values raise DateParseError."""
        df = pd.DataFrame([_chinese_row("2024-01-02", 10.0, 10.5, 9.8, 10.2)])
        # Replace the date column with values that pd.to_datetime cannot parse.
        df["日期"] = ["not_a_date"]
        mock_akshare_module.stock_zh_a_hist.return_value = df

        with pytest.raises(DateParseError) as exc_info:
            _provider().daily_bars("000001", *TEST_DATES)
        assert exc_info.value.source == "akshare.stock_zh_a_hist"
        assert "trade_date" in str(exc_info.value)


# ===================================================================
# stock_list tests
# ===================================================================


class TestStockList:
    def test_stock_list_renames_and_adds_exchange(self, mock_akshare_module):
        mock_akshare_module.stock_info_a_code_name.return_value = pd.DataFrame([
            {"code": "000001", "name": "平安银行"},
            {"code": "600000", "name": "浦发银行"},
            {"code": "430047", "name": "诺思兰德"},
        ])

        result = _provider().stock_list()

        assert list(result.columns) == ["symbol", "name", "exchange", "status"]
        assert len(result) == 3
        assert result.iloc[0]["symbol"] == "000001"
        assert result.iloc[0]["exchange"] == "SZ"
        assert result.iloc[1]["symbol"] == "600000"
        assert result.iloc[1]["exchange"] == "SH"
        assert result.iloc[2]["symbol"] == "430047"
        assert result.iloc[2]["exchange"] == "BJ"
        assert (result["status"] == "active").all()


# ===================================================================
# Provider satisfies protocol
# ===================================================================


class TestProtocolConformance:
    def test_aks_provider_satisfies_protocol(self):
        from app.data.akshare_provider import AkShareProvider
        from app.data.provider import MarketDataProvider

        assert isinstance(AkShareProvider(), MarketDataProvider)


def test_beijing_920_prefix_is_classified_as_bj(mock_akshare_module):
    mock_akshare_module.stock_info_a_code_name.return_value = pd.DataFrame(
        [{"code": "920001", "name": "BJ New Code"}]
    )

    result = _provider().stock_list()

    assert result.iloc[0]["exchange"] == "BJ"


class TestTradingCalendar:
    def test_calendar_is_normalized(self, mock_akshare_module):
        mock_akshare_module.tool_trade_date_hist_sina.return_value = pd.DataFrame(
            {"trade_date": ["2024-01-02", "2024-01-03", "2024-01-03"]}
        )

        result = _provider().trading_calendar()

        assert result["trade_date"].tolist() == [date(2024, 1, 2), date(2024, 1, 3)]
        assert result["is_open"].tolist() == [True, True]

    def test_calendar_failure_is_provider_error(self, mock_akshare_module):
        mock_akshare_module.tool_trade_date_hist_sina.side_effect = ConnectionError("down")

        with pytest.raises(ProviderUnavailableError):
            _provider().trading_calendar()


# ===================================================================
# index_daily_bars tests
# ===================================================================


class TestIndexDailyBarsSuccess:
    def test_returns_standard_columns(self, mock_akshare_module):
        """index_zh_a_hist returns Chinese columns → standardised output with adj='none'."""
        mock_akshare_module.index_zh_a_hist.return_value = pd.DataFrame([
            _chinese_row("2024-01-02", 3200.0, 3210.0, 3190.0, 3205.0),
            _chinese_row("2024-01-03", 3205.0, 3220.0, 3200.0, 3215.0),
        ])

        result = _provider().index_daily_bars("000300", *TEST_DATES)

        assert list(result.columns) == STANDARD_COLS
        assert len(result) == 2
        assert result.iloc[0]["symbol"] == "000300"
        assert result.iloc[0]["trade_date"] == date(2024, 1, 2)
        assert result.iloc[0]["open"] == 3200.0
        assert result.iloc[0]["high"] == 3210.0
        assert result.iloc[0]["low"] == 3190.0
        assert result.iloc[0]["close"] == 3205.0
        assert result.iloc[0]["adj"] == "none"

    def test_english_columns_normalised(self, mock_akshare_module):
        """index_zh_a_hist returns English columns → still standardised."""
        mock_akshare_module.index_zh_a_hist.return_value = pd.DataFrame([
            _english_row("2024-01-02", 3200.0, 3210.0, 3190.0, 3205.0),
        ])

        result = _provider().index_daily_bars("000016", *TEST_DATES)

        assert list(result.columns) == STANDARD_COLS
        assert len(result) == 1
        assert result.iloc[0]["adj"] == "none"

    def test_empty_result(self, mock_akshare_module):
        """Empty DataFrame from source → empty standardized DataFrame."""
        mock_akshare_module.index_zh_a_hist.return_value = pd.DataFrame(
            columns=CHINESE_COLS
        )

        result = _provider().index_daily_bars("000300", *TEST_DATES)
        assert list(result.columns) == STANDARD_COLS
        assert len(result) == 0

    def test_none_result(self, mock_akshare_module):
        """None return from source → empty standardized DataFrame."""
        mock_akshare_module.index_zh_a_hist.return_value = None

        result = _provider().index_daily_bars("000300", *TEST_DATES)
        assert list(result.columns) == STANDARD_COLS
        assert len(result) == 0

    def test_date_column_parsed_to_date_type(self, mock_akshare_module):
        mock_akshare_module.index_zh_a_hist.return_value = pd.DataFrame([
            _chinese_row("2024-01-02", 3200.0, 3210.0, 3190.0, 3205.0),
        ])

        result = _provider().index_daily_bars("000300", *TEST_DATES)
        assert isinstance(result.iloc[0]["trade_date"], date)

    def test_retries_transient_source_failure(self, mock_akshare_module):
        mock_akshare_module.index_zh_a_hist.side_effect = [
            ConnectionError("first failure"),
            ConnectionError("second failure"),
            pd.DataFrame([_chinese_row("2024-01-02", 3200.0, 3210.0, 3190.0, 3205.0)]),
        ]

        with patch("app.data.akshare_provider.sleep") as mocked_sleep:
            result = _provider().index_daily_bars("000300", *TEST_DATES)

        assert len(result) == 1
        assert mock_akshare_module.index_zh_a_hist.call_count == 3
        assert mocked_sleep.call_args_list == [call(0.8), call(1.6)]

    def test_falls_back_to_sina_and_fills_missing_amount(self, mock_akshare_module):
        mock_akshare_module.index_zh_a_hist.side_effect = ConnectionError("eastmoney down")
        mock_akshare_module.stock_zh_index_daily.return_value = pd.DataFrame(
            [{
                "date": "2024-01-02",
                "open": 3200.0,
                "high": 3210.0,
                "low": 3190.0,
                "close": 3205.0,
                "volume": 100000.0,
            }]
        )

        with patch("app.data.akshare_provider.sleep"):
            result = _provider().index_daily_bars("000300", *TEST_DATES)

        assert len(result) == 1
        assert result.iloc[0]["amount"] == 0.0
        mock_akshare_module.stock_zh_index_daily.assert_called_once_with(symbol="sh000300")
        mock_akshare_module.stock_zh_index_daily_tx.assert_not_called()

    def test_falls_back_to_tencent_and_fills_missing_volume(self, mock_akshare_module):
        mock_akshare_module.index_zh_a_hist.side_effect = ConnectionError("eastmoney down")
        mock_akshare_module.stock_zh_index_daily.side_effect = ConnectionError("sina down")
        mock_akshare_module.stock_zh_index_daily_tx.return_value = pd.DataFrame(
            [{
                "date": "2024-01-02",
                "open": 3200.0,
                "high": 3210.0,
                "low": 3190.0,
                "close": 3205.0,
                "amount": 123456.0,
            }]
        )

        with patch("app.data.akshare_provider.sleep"):
            result = _provider().index_daily_bars("000300", *TEST_DATES)

        assert len(result) == 1
        assert result.iloc[0]["volume"] == 0.0
        mock_akshare_module.stock_zh_index_daily_tx.assert_called_once_with(symbol="sh000300")


class TestIndexDailyBarsErrors:
    def test_provider_unavailable(self, mock_akshare_module):
        """When ak.index_zh_a_hist raises, a ProviderUnavailableError is raised."""
        mock_akshare_module.index_zh_a_hist.side_effect = ConnectionError("network down")
        mock_akshare_module.stock_zh_index_daily.side_effect = ConnectionError("sina down")
        mock_akshare_module.stock_zh_index_daily_tx.side_effect = ConnectionError("tencent down")

        with patch("app.data.akshare_provider.sleep") as mocked_sleep, pytest.raises(ProviderUnavailableError) as exc_info:
            _provider().index_daily_bars("000300", *TEST_DATES)
        assert "network down" in str(exc_info.value)
        assert "000300" in str(exc_info.value)
        assert mock_akshare_module.index_zh_a_hist.call_count == 3
        assert mocked_sleep.call_args_list == [call(0.8), call(1.6), call(2.4000000000000004)]

    def test_date_parse_error(self, mock_akshare_module):
        """Unparseable trade_date values raise DateParseError."""
        df = pd.DataFrame([_chinese_row("2024-01-02", 3200.0, 3210.0, 3190.0, 3205.0)])
        df["日期"] = ["not_a_date"]
        mock_akshare_module.index_zh_a_hist.return_value = df

        with pytest.raises(DateParseError) as exc_info:
            _provider().index_daily_bars("000300", *TEST_DATES)
        assert exc_info.value.source == "akshare.index_zh_a_hist"

    def test_missing_column(self, mock_akshare_module):
        """Missing required column raises MissingColumnError."""
        df = pd.DataFrame([_chinese_row("2024-01-02", 3200.0, 3210.0, 3190.0, 3205.0)])
        df = df.drop(columns=["最高"])
        mock_akshare_module.index_zh_a_hist.return_value = df

        with pytest.raises(MissingColumnError) as exc_info:
            _provider().index_daily_bars("000300", *TEST_DATES)
        assert "high" in exc_info.value.missing
        assert exc_info.value.source == "akshare.index_zh_a_hist"

    def test_nan_value(self, mock_akshare_module):
        """NaN in required column raises NaNValueError."""
        from app.data.errors import NaNValueError

        mock_akshare_module.index_zh_a_hist.return_value = pd.DataFrame([
            _chinese_row("2024-01-02", open_=float("nan"), high=3210.0, low=3190.0, close=3205.0),
        ])

        with pytest.raises(NaNValueError) as exc_info:
            _provider().index_daily_bars("000300", *TEST_DATES)
        assert exc_info.value.column == "open"
        assert exc_info.value.source == "akshare.index_zh_a_hist"

    def test_invalid_price(self, mock_akshare_module):
        """Non-positive price raises InvalidPriceError."""
        from app.data.errors import InvalidPriceError

        mock_akshare_module.index_zh_a_hist.return_value = pd.DataFrame([
            _chinese_row("2024-01-02", 3200.0, 3210.0, 3190.0, close=0.0),
        ])

        with pytest.raises(InvalidPriceError) as exc_info:
            _provider().index_daily_bars("000300", *TEST_DATES)
        assert exc_info.value.column == "close"
        assert exc_info.value.source == "akshare.index_zh_a_hist"

    def test_ohlc_contradiction(self, mock_akshare_module):
        """OHLC contradiction raises OHLCContradictionError."""
        from app.data.errors import OHLCContradictionError

        mock_akshare_module.index_zh_a_hist.return_value = pd.DataFrame([
            _chinese_row("2024-01-02", 3200.0, high=3100.0, low=3250.0, close=3205.0),
        ])

        with pytest.raises(OHLCContradictionError) as exc_info:
            _provider().index_daily_bars("000300", *TEST_DATES)
        assert exc_info.value.source == "akshare.index_zh_a_hist"

    def test_zero_volume_allowed(self, mock_akshare_module):
        """Zero volume/amount on an index day is valid (indices can have zero volume)."""
        mock_akshare_module.index_zh_a_hist.return_value = pd.DataFrame([
            _chinese_row("2024-01-02", 3200.0, 3210.0, 3190.0, 3205.0, volume=0, amount=0),
        ])

        result = _provider().index_daily_bars("000300", *TEST_DATES)
        assert len(result) == 1
        assert result.iloc[0]["volume"] == 0.0
