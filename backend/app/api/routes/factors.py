import hashlib
import json
from datetime import date, timedelta
from math import isfinite

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.data.repository import MarketDataRepository
from app.factors import (
    BUILTIN_FACTOR_NAMES,
    FACTOR_DIRECTIONS,
    FactorLab,
    FactorSpec,
    evaluate,
    forward_returns,
    preprocess,
)
from app.schemas.factors import (
    FactorExperimentRequest,
    FactorExperimentResponse,
    FactorMetadataOut,
    FactorSummaryOut,
)

router = APIRouter()

_LARGE_UNIVERSE_SYMBOLS = 1000
_LARGE_EXPERIMENT_CELLS = 2_000_000


def _finite(value) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    return numeric if isfinite(numeric) else None


def _factor_run_metadata(
    *,
    symbol_source: str,
    selected_symbols: list[str],
    requested_factors: list[str],
    start_date: date,
    end_date: date,
    warmup_start: date,
    label_end: date,
    horizon: int,
    n_groups: int,
    bar_rows: int,
) -> dict[str, object]:
    payload = {
        "symbol_source": symbol_source,
        "selected_symbols": selected_symbols,
        "factor_names": requested_factors,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "warmup_start": warmup_start.isoformat(),
        "label_end": label_end.isoformat(),
        "horizon": horizon,
        "n_groups": n_groups,
    }
    run_hash = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    symbol_hash = hashlib.sha256(
        json.dumps(
            selected_symbols,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    degraded_reasons = [
        "factor experiments currently use non-PIT universes",
        "qfq OHLCV history may be revised after future corporate actions",
    ]
    if symbol_source == "research_pool":
        degraded_reasons.insert(
            0,
            "research_pool is selected from today's active-stock coverage",
        )
    return {
        **payload,
        "run_hash": run_hash,
        "selected_symbol_count": len(selected_symbols),
        "selected_symbols_hash": symbol_hash,
        "bar_rows": bar_rows,
        "point_in_time": False,
        "degraded": True,
        "degraded_reasons": degraded_reasons,
    }


@router.get("", response_model=list[FactorMetadataOut])
def list_factors() -> list[FactorMetadataOut]:
    return [
        FactorMetadataOut(name=name, direction=FACTOR_DIRECTIONS[name])
        for name in BUILTIN_FACTOR_NAMES
    ]


@router.post("/experiments/run", response_model=FactorExperimentResponse)
def run_factor_experiment(
    payload: FactorExperimentRequest,
    session: Session = Depends(get_session),
) -> FactorExperimentResponse:
    if payload.start_date > payload.end_date:
        raise HTTPException(status_code=400, detail="start_date must be <= end_date")

    repository = MarketDataRepository(session)
    if payload.symbol_source == "research_pool":
        symbols = repository.covered_research_symbols(
            payload.start_date,
            payload.end_date,
            limit=payload.pool_max_symbols,
        )
    else:
        try:
            symbols = repository.resolve_symbols(payload.symbols)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not symbols:
        raise HTTPException(status_code=400, detail="No eligible symbols for the factor experiment.")

    requested = payload.factor_names or BUILTIN_FACTOR_NAMES
    unknown = sorted(set(requested) - set(BUILTIN_FACTOR_NAMES))
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown factors: {', '.join(unknown)}")

    warmup_start = payload.start_date - timedelta(days=200)
    label_end = payload.end_date + timedelta(days=payload.horizon * 3 + 10)
    bars = repository.daily_bars(symbols, warmup_start, label_end)
    if bars.empty:
        raise HTTPException(status_code=400, detail="No local daily bars for the requested experiment.")

    run_metadata = _factor_run_metadata(
        symbol_source=payload.symbol_source,
        selected_symbols=symbols,
        requested_factors=requested,
        start_date=payload.start_date,
        end_date=payload.end_date,
        warmup_start=warmup_start,
        label_end=label_end,
        horizon=payload.horizon,
        n_groups=payload.n_groups,
        bar_rows=len(bars),
    )
    factor_panel = FactorLab().compute(bars, [FactorSpec(name) for name in requested])
    labels = forward_returns(bars, horizons=(payload.horizon,))
    signal_dates = factor_panel.index.get_level_values("trade_date")
    in_range = (signal_dates >= payload.start_date) & (signal_dates <= payload.end_date)
    factor_panel = factor_panel[in_range]
    label_dates = labels.index.get_level_values("trade_date")
    labels = labels[(label_dates >= payload.start_date) & (label_dates <= payload.end_date)]
    label = labels[f"fwd_{payload.horizon}d"]

    warnings: list[str] = []
    if len(symbols) < 30:
        warnings.append(
            f"Only {len(symbols)} symbols are available; results are engineering diagnostics, not investment evidence."
        )
    estimated_cells = (
        len(symbols)
        * max(1, factor_panel.index.get_level_values("trade_date").nunique())
        * len(requested)
    )
    if len(symbols) >= _LARGE_UNIVERSE_SYMBOLS or estimated_cells >= _LARGE_EXPERIMENT_CELLS:
        warnings.append(
            "Large factor experiment: the current API computes dense in-memory panels. "
            "For reproducible reruns, keep run_metadata.run_hash with the result and prefer batching factors or dates."
        )
    warnings.extend(
        [
            "Factor experiments are not point-in-time; degraded results must not be treated as live-trading evidence.",
            "AkShare qfq history may be revised after future corporate actions.",
        ]
    )
    if payload.symbol_source == "research_pool":
        warnings.append(
            "The research_pool universe uses today's active-stock list and may contain survivorship bias."
        )

    summaries: list[FactorSummaryOut] = []
    for name in requested:
        direction = FACTOR_DIRECTIONS[name]
        adjusted = preprocess(factor_panel[[name]])["standardized"][name] * direction
        report = evaluate(adjusted, label, n_groups=payload.n_groups)
        summaries.append(
            FactorSummaryOut(
                name=name,
                direction=direction,
                ic_mean=_finite(report["ic_mean"]),
                ic_ir=_finite(report["ic_ir"]),
                rankic_mean=_finite(report["rankic_mean"]),
                rankic_ir=_finite(report["rankic_ir"]),
                long_short_return=_finite(report["long_short_return"]),
                long_short_turnover=_finite(report["long_short_turnover"]),
                n_dates=report["n_dates"],
                group_returns={
                    group: _finite(value)
                    for group, value in report["group_returns"].items()
                },
            )
        )
        if summaries[-1].n_dates < 20:
            warnings.append(
                f"{name} has only {summaries[-1].n_dates} valid evaluation dates; treat its statistics as unreliable."
            )
    summaries.sort(
        key=lambda item: item.rankic_mean if item.rankic_mean is not None else float("-inf"),
        reverse=True,
    )
    return FactorExperimentResponse(
        selected_symbols=symbols,
        factor_count=len(requested),
        horizon=payload.horizon,
        n_groups=payload.n_groups,
        warnings=warnings,
        run_metadata=run_metadata,
        summaries=summaries,
    )
