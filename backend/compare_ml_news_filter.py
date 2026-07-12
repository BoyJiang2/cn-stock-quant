"""Compare ML Score Rank with and without database news risk filtering."""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from app.backtest.engine import BacktestConfig, DailyBacktestEngine
from app.core.database import SessionLocal, init_db
from app.data.repository import MarketDataRepository
from app.strategy.registry import get_strategy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--scores-path", type=Path, required=True)
    parser.add_argument("--symbols", default="")
    parser.add_argument("--symbol-source", choices=["manual", "research_pool"], default="manual")
    parser.add_argument("--pool-max-symbols", type=int, default=300)
    parser.add_argument("--benchmark-symbol", default="000300")
    parser.add_argument("--initial-cash", type=float, default=1_000_000)
    parser.add_argument("--rebalance-interval", type=int, default=1)
    parser.add_argument("--top-n", type=int, default=30)
    parser.add_argument("--max-position-weight", type=float, default=0.05)
    parser.add_argument("--max-total-weight", type=float, default=0.8)
    parser.add_argument("--min-score", type=float, default=-1.0)
    parser.add_argument("--min-avg-amount-20d", type=float, default=50_000_000)
    parser.add_argument("--min-price", type=float, default=5.0)
    parser.add_argument("--negative-news-lookback-days", type=int, default=3)
    parser.add_argument("--negative-news-min-relevance", type=float, default=0.0)
    parser.add_argument("--negative-news-max-sentiment", type=float, default=-0.2)
    parser.add_argument(
        "--news-availability",
        choices=["observed", "published_at"],
        default="observed",
        help="observed uses max(published_at, fetched_at); published_at is a retrospective research mode.",
    )
    parser.add_argument("--include-curves", action="store_true")
    parser.add_argument("--json-output", type=Path, default=Path("ml-news-filter-comparison.json"))
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args()


def run_comparison(args: argparse.Namespace) -> dict[str, Any]:
    if args.start_date > args.end_date:
        raise ValueError("start_date must be <= end_date")
    if not args.scores_path.exists():
        raise ValueError(f"scores_path does not exist: {args.scores_path}")

    started_at = time.time()
    init_db()
    with SessionLocal() as session:
        repository = MarketDataRepository(session)
        symbols = _select_symbols(repository, args)
        bars = repository.daily_bars(symbols, args.start_date, args.end_date)
        if bars.empty:
            raise ValueError("No local daily bars for selected symbols and date range.")
        benchmark_bars = (
            repository.index_daily_bars(args.benchmark_symbol, args.start_date, args.end_date)
            if args.benchmark_symbol
            else None
        )
        if benchmark_bars is not None and benchmark_bars.empty:
            benchmark_bars = None
        news_history = _load_news_history(repository, symbols, args)

    strategy = get_strategy("ml_score_rank")
    base_params = _strategy_params(args)
    runs = {
        "baseline": _run_variant(
            strategy=strategy,
            bars=bars,
            benchmark_bars=benchmark_bars,
            args=args,
            params={**base_params, "use_db_negative_news": False},
            news_history=None,
        ),
        "db_news_filter": _run_variant(
            strategy=strategy,
            bars=bars,
            benchmark_bars=benchmark_bars,
            args=args,
            params={**base_params, "use_db_negative_news": True},
            news_history=news_history,
        ),
    }
    report = {
        "metadata": {
            "strategy": "ml_score_rank",
            "start_date": args.start_date.isoformat(),
            "end_date": args.end_date.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scores_path": str(args.scores_path),
            "score_column": "score",
            "symbol_source": args.symbol_source,
            "selected_symbol_count": len(symbols),
            "selected_symbols_sample": symbols[:30],
            "bar_rows": int(len(bars)),
            "benchmark_symbol": args.benchmark_symbol if benchmark_bars is not None else None,
            "news_lookback_days": args.negative_news_lookback_days,
            "news_availability": args.news_availability,
            "news_rows": int(len(news_history)) if news_history is not None else 0,
            "timing_seconds": round(time.time() - started_at, 3),
        },
        "news_risk": _news_risk_summary(news_history),
        "filtered_symbols": _filtered_symbols_summary(news_history),
        "runs": runs,
        "comparison": _comparison(runs["baseline"], runs["db_news_filter"]),
    }
    return report


def _select_symbols(repository: MarketDataRepository, args: argparse.Namespace) -> list[str]:
    if args.symbol_source == "manual":
        symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]
        return repository.resolve_symbols(symbols)
    symbols = repository.select_research_symbols(
        args.start_date,
        args.end_date,
        limit=args.pool_max_symbols,
    )
    if not symbols:
        symbols = repository.covered_research_symbols(
            args.start_date,
            args.end_date,
            limit=args.pool_max_symbols,
        )
    if not symbols:
        raise ValueError("No symbols selected.")
    return symbols


def _strategy_params(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "scores_path": str(args.scores_path),
        "score_column": "score",
        "top_n": args.top_n,
        "max_position_weight": args.max_position_weight,
        "max_total_weight": args.max_total_weight,
        "min_score": args.min_score,
        "min_avg_amount_20d": args.min_avg_amount_20d,
        "min_price": args.min_price,
        "negative_news_lookback_days": args.negative_news_lookback_days,
        "negative_news_min_relevance": args.negative_news_min_relevance,
        "negative_news_max_sentiment": args.negative_news_max_sentiment,
    }


def _load_news_history(
    repository: MarketDataRepository,
    symbols: list[str],
    args: argparse.Namespace,
) -> pd.DataFrame | None:
    start_at = datetime.combine(
        args.start_date - timedelta(days=max(0, args.negative_news_lookback_days)),
        datetime_time.min,
    )
    end_at = datetime.combine(args.end_date, datetime_time.max)
    frames = [
        repository.news_items(symbol=symbol, start_at=start_at, end_at=end_at, limit=5000)
        for symbol in symbols
    ]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return None
    frames = [frame.dropna(axis=1, how="all") for frame in frames]
    return _apply_news_availability(pd.concat(frames, ignore_index=True), args.news_availability)


def _apply_news_availability(news_history: pd.DataFrame, mode: str) -> pd.DataFrame:
    frame = news_history.copy()
    if mode == "published_at":
        frame["known_at"] = pd.to_datetime(frame["published_at"], errors="coerce")
        return frame
    frame["published_at"] = pd.to_datetime(frame["published_at"], errors="coerce")
    frame["fetched_at"] = pd.to_datetime(frame["fetched_at"], errors="coerce")
    frame["known_at"] = frame[["published_at", "fetched_at"]].max(axis=1)
    return frame


def _run_variant(
    *,
    strategy,
    bars: pd.DataFrame,
    benchmark_bars: pd.DataFrame | None,
    args: argparse.Namespace,
    params: dict[str, Any],
    news_history: pd.DataFrame | None,
) -> dict[str, Any]:
    result = DailyBacktestEngine().run(
        strategy=strategy,
        bars=bars,
        benchmark_bars=benchmark_bars,
        config=BacktestConfig(
            start_date=args.start_date,
            end_date=args.end_date,
            initial_cash=args.initial_cash,
            rebalance_interval=args.rebalance_interval,
            params=params,
            news_history=news_history,
        ),
    )
    run = {
        "parameters": params,
        "metrics": result.metrics,
        "trade_stats": _trade_stats(result.trades, args.initial_cash),
        "traded_symbols": sorted({trade["symbol"] for trade in result.trades}),
    }
    if args.include_curves:
        run["equity_curve"] = _json_safe_points(result.equity_curve)
        run["benchmark_curve"] = _json_safe_points(result.benchmark_curve)
    return run


def _trade_stats(trades: list[dict], initial_cash: float) -> dict[str, Any]:
    buy_amount = sum(float(trade["amount"]) for trade in trades if trade["side"] == "buy")
    sell_amount = sum(float(trade["amount"]) for trade in trades if trade["side"] == "sell")
    return {
        "trade_count": len(trades),
        "buy_count": sum(1 for trade in trades if trade["side"] == "buy"),
        "sell_count": sum(1 for trade in trades if trade["side"] == "sell"),
        "buy_amount": round(buy_amount, 2),
        "sell_amount": round(sell_amount, 2),
        "turnover_on_initial_cash": round((buy_amount + sell_amount) / initial_cash, 6)
        if initial_cash > 0
        else 0.0,
    }


def _news_risk_summary(news_history: pd.DataFrame | None) -> dict[str, Any]:
    if news_history is None or news_history.empty:
        return {"rows": 0, "risk_rows": 0, "risk_symbol_count": 0, "risk_symbols": []}
    frame = news_history.copy()
    event = frame.get("event_type", pd.Series("", index=frame.index)).astype(str).str.lower()
    label = frame.get("sentiment_label", pd.Series("", index=frame.index)).astype(str).str.lower()
    score = pd.to_numeric(frame.get("sentiment_score", pd.Series(pd.NA, index=frame.index)), errors="coerce")
    risk = (
        event.isin({"negative_news", "risk_news"})
        | label.isin({"negative", "risk", "bearish", "bad"})
        | (score <= -0.2)
    )
    risk_frame = frame[risk].copy()
    symbols = sorted(risk_frame["symbol"].dropna().astype(str).unique().tolist())
    return {
        "rows": int(len(frame)),
        "risk_rows": int(len(risk_frame)),
        "risk_symbol_count": len(symbols),
        "risk_symbols": symbols[:50],
    }


def _filtered_symbols_summary(news_history: pd.DataFrame | None) -> list[dict[str, Any]]:
    if news_history is None or news_history.empty:
        return []
    risk_frame = _risk_news_frame(news_history)
    if risk_frame.empty:
        return []
    rows: list[dict[str, Any]] = []
    for symbol, group in risk_frame.groupby("symbol", sort=True):
        group = group.sort_values("known_at")
        latest = group.iloc[-1]
        rows.append(
            {
                "symbol": str(symbol),
                "first_blocked_date": group["known_at"].dt.date.min().isoformat(),
                "blocked_days": int(group["known_at"].dt.date.nunique()),
                "news_count": int(len(group)),
                "latest_title": str(latest.get("title") or ""),
                "event_type": str(latest.get("event_type") or ""),
                "sentiment_label": str(latest.get("sentiment_label") or ""),
                "sentiment_score": (
                    None
                    if pd.isna(latest.get("sentiment_score"))
                    else float(latest.get("sentiment_score"))
                ),
            }
        )
    return rows


def _risk_news_frame(news_history: pd.DataFrame) -> pd.DataFrame:
    frame = news_history.copy()
    if "known_at" not in frame.columns:
        frame["published_at"] = pd.to_datetime(frame.get("published_at"), errors="coerce")
        frame["fetched_at"] = pd.to_datetime(frame.get("fetched_at"), errors="coerce")
        frame["known_at"] = frame[["published_at", "fetched_at"]].max(axis=1)
    else:
        frame["known_at"] = pd.to_datetime(frame["known_at"], errors="coerce")
    event = frame.get("event_type", pd.Series("", index=frame.index)).astype(str).str.lower()
    label = frame.get("sentiment_label", pd.Series("", index=frame.index)).astype(str).str.lower()
    score = pd.to_numeric(frame.get("sentiment_score", pd.Series(pd.NA, index=frame.index)), errors="coerce")
    risk = (
        event.isin({"negative_news", "risk_news"})
        | label.isin({"negative", "risk", "bearish", "bad"})
        | (score <= -0.2)
    )
    return frame[risk].dropna(subset=["symbol", "known_at"]).copy()


def _comparison(baseline: dict[str, Any], news_filter: dict[str, Any]) -> dict[str, Any]:
    base_metrics = baseline["metrics"]
    news_metrics = news_filter["metrics"]
    comparison = {
        "total_return_delta": _delta(news_metrics, base_metrics, "total_return"),
        "annual_return_delta": _delta(news_metrics, base_metrics, "annual_return"),
        "max_drawdown_delta": _delta(news_metrics, base_metrics, "max_drawdown"),
        "sharpe_delta": _delta(news_metrics, base_metrics, "sharpe"),
        "excess_return_delta": _delta(news_metrics, base_metrics, "excess_return"),
        "trade_count_delta": news_filter["trade_stats"]["trade_count"]
        - baseline["trade_stats"]["trade_count"],
        "turnover_delta": float(news_filter["trade_stats"].get("turnover_on_initial_cash", 0.0))
        - float(baseline["trade_stats"].get("turnover_on_initial_cash", 0.0)),
        "baseline_only_traded_symbols": sorted(
            set(baseline["traded_symbols"]) - set(news_filter["traded_symbols"])
        )[:50],
        "news_filter_only_traded_symbols": sorted(
            set(news_filter["traded_symbols"]) - set(baseline["traded_symbols"])
        )[:50],
    }
    comparison["winner"] = _winner(comparison)
    return comparison


def _winner(comparison: dict[str, Any]) -> str:
    for key in ("max_drawdown_delta", "excess_return_delta", "sharpe_delta"):
        value = float(comparison.get(key, 0.0) or 0.0)
        if value > 0:
            return "db_news_filter"
        if value < 0:
            return "baseline"
    return "tie"


def _delta(left: dict[str, Any], right: dict[str, Any], key: str) -> float:
    return float(left.get(key, 0.0) or 0.0) - float(right.get(key, 0.0) or 0.0)


def _json_safe_points(points: list[dict]) -> list[dict]:
    safe: list[dict] = []
    for point in points:
        item = dict(point)
        trade_date = item.get("trade_date")
        if hasattr(trade_date, "isoformat"):
            item["trade_date"] = trade_date.isoformat()
        safe.append(item)
    return safe


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# ML News Filter Comparison",
        "",
        f"- strategy: `{report['metadata']['strategy']}`",
        f"- start: `{report['metadata']['start_date']}`",
        f"- end: `{report['metadata']['end_date']}`",
        f"- selected_symbol_count: `{report['metadata']['selected_symbol_count']}`",
        f"- news_availability: `{report['metadata'].get('news_availability', 'observed')}`",
        f"- news_risk_symbols: `{report['news_risk']['risk_symbol_count']}`",
        "",
        "## Runs",
        "",
        "| Variant | Total Return | Max DD | Sharpe | Trades | Turnover |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, run in report["runs"].items():
        metrics = run["metrics"]
        stats = run["trade_stats"]
        lines.append(
            "| {name} | {total_return} | {max_drawdown} | {sharpe} | {trades} | {turnover} |".format(
                name=name,
                total_return=_fmt(metrics.get("total_return")),
                max_drawdown=_fmt(metrics.get("max_drawdown")),
                sharpe=_fmt(metrics.get("sharpe")),
                trades=stats["trade_count"],
                turnover=_fmt(stats["turnover_on_initial_cash"]),
            )
        )
    lines.extend(["", "## Comparison", ""])
    for key, value in report["comparison"].items():
        lines.append(f"- {key}: {_fmt(value)}")
    lines.extend(
        [
            "",
            "## Filtered Symbols",
            "",
            "| Symbol | First Blocked | Days | News | Latest Title | Type | Sentiment |",
            "| --- | --- | ---: | ---: | --- | --- | ---: |",
        ]
    )
    for row in report["filtered_symbols"]:
        lines.append(
            "| {symbol} | {first_blocked_date} | {blocked_days} | {news_count} | {latest_title} | {event_type} | {sentiment_score} |".format(
                symbol=row["symbol"],
                first_blocked_date=row["first_blocked_date"],
                blocked_days=row["blocked_days"],
                news_count=row["news_count"],
                latest_title=str(row["latest_title"]).replace("|", "/")[:80],
                event_type=row["event_type"],
                sentiment_score=_fmt(row["sentiment_score"]),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(map(str, value))
    if value is None:
        return ""
    try:
        return f"{float(value):.6f}"
    except (TypeError, ValueError):
        return str(value)


def main() -> int:
    args = parse_args()
    report = run_comparison(args)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.markdown_output is not None:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(to_markdown(report), encoding="utf-8")
    print(json.dumps(report["comparison"], ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
