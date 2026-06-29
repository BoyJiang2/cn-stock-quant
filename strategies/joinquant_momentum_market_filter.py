# -*- coding: utf-8 -*-
"""
JoinQuant/聚宽策略：市场过滤 + 趋势动量 + 盘中量能确认

这版参考了桌面 1.txt 的聚宽写法：
- 使用 run_daily 定时运行，不依赖 before_trading_start / handle_data。
- 盘中多次风控检查，14:30 附近再开新仓，减少 T+1 噪音换手。
- 股票池用市值过滤 + 基础过滤，而不是只用指数成分股。
- 选股综合趋势、突破、RSI、盘中量比和当日涨幅。

使用方式：
1. 在 JoinQuant/聚宽中新建股票策略。
2. 将本文件完整复制到策略编辑器。
3. 优先用日频回测，确认能跑后再调参数。
"""

from jqdata import *  # type: ignore
import numpy as np
import pandas as pd
import talib


def initialize(context):
    """聚宽初始化函数。"""
    set_benchmark("000852.XSHG")
    set_option("use_real_price", True)
    set_order_cost(
        OrderCost(
            close_tax=0.001,
            open_commission=0.0003,
            close_commission=0.0003,
            min_commission=5,
        ),
        type="stock",
    )

    # 市场与股票池参数。
    g.market_index = "000852.XSHG"
    g.min_market_cap = 30
    g.max_market_cap = 200
    g.exclude_prefixes = ("688", "8", "4")

    # 日线信号参数。
    g.daily_count = 80
    g.min_listing_days = 120
    g.max_buy_return = 0.085
    g.min_volume_ratio = 1.5

    # 持仓与风控参数。
    g.max_hold_count = 5
    g.position_pct = 0.18
    g.stop_loss = 0.95
    g.trailing_start = 1.08
    g.trailing_stop = 0.94
    g.max_hold_days = 10
    g.min_hold_days = 2

    # 运行状态。
    g.volume_lookback = 10
    g.buy_dates = {}
    g.highest_price = {}

    # 盘中风控；尾盘开仓。
    run_daily(risk_monitor, time="10:30")
    run_daily(risk_monitor, time="11:30")
    run_daily(risk_monitor, time="14:00")
    run_daily(rebalance, time="14:30")

    log.info("strategy initialized: market filter + trend momentum + intraday volume")


def risk_monitor(context):
    """盘中风控，只卖不买。"""
    log.info("risk monitor: %s" % context.current_dt)
    execute_risk_sell(context, force_market_risk=is_market_risk(context))


def rebalance(context):
    """尾盘调仓：先处理风险卖出，再根据候选池买入。"""
    log.info("rebalance: %s" % context.current_dt)

    market_ok = is_market_ok(context)
    execute_risk_sell(context, force_market_risk=not market_ok)

    if not market_ok:
        log.info("market filter failed, skip new buys")
        return

    stock_pool = get_refined_pool(context)
    candidates = select_candidates(stock_pool, context)
    log.info("candidate count: %d" % len(candidates))

    buy_new_positions(context, candidates)


def is_market_ok(context):
    """市场状态过滤：指数在中短期趋势上方才允许开仓。"""
    df = get_price(
        g.market_index,
        count=80,
        end_date=context.previous_date,
        frequency="daily",
        fields=["close"],
        panel=False,
    )
    if df is None or df.empty or len(df) < 60:
        return False

    close = df["close"].values
    ma20 = np.mean(close[-20:])
    ma60 = np.mean(close[-60:])
    return close[-1] > ma20 and ma20 >= ma60 * 0.98


def is_market_risk(context):
    """市场风险状态：跌破 60 日趋势时触发组合风险卖出。"""
    df = get_price(
        g.market_index,
        count=80,
        end_date=context.previous_date,
        frequency="daily",
        fields=["close"],
        panel=False,
    )
    if df is None or df.empty or len(df) < 60:
        return True

    close = df["close"].values
    ma20 = np.mean(close[-20:])
    ma60 = np.mean(close[-60:])
    return close[-1] < ma60 and ma20 < ma60


def get_refined_pool(context):
    """基础股票池：全市场中等市值，过滤 ST、停牌、科创/北交所。"""
    current_data = get_current_data()
    all_stocks = list(get_all_securities(["stock"], context.current_dt).index)

    q = query(valuation.code).filter(
        valuation.code.in_(all_stocks),
        valuation.market_cap > g.min_market_cap,
        valuation.market_cap < g.max_market_cap,
    )
    df_valuation = get_fundamentals(q)
    codes = list(df_valuation.code)

    result = []
    for code in codes:
        try:
            cd = current_data[code]
        except Exception:
            continue
        if cd.paused or cd.is_st:
            continue
        if code.startswith(g.exclude_prefixes):
            continue
        if cd.last_price <= 0:
            continue
        info = get_security_info(code)
        if info is None:
            continue
        if (context.previous_date - info.start_date).days < g.min_listing_days:
            continue
        result.append(code)
    return result


def select_candidates(stock_list, context):
    """候选排序：日线趋势先筛，再用盘中量比和当日涨幅打分。"""
    if not stock_list:
        return []

    daily_info = build_daily_signal_info(stock_list, context)
    if not daily_info:
        return []

    daily_candidates = list(daily_info.keys())
    volume_ratio = get_intraday_volume_ratio(daily_candidates, context)
    if not volume_ratio:
        return []

    current_data = get_current_data()
    scored = []
    for code in daily_candidates:
        ratio = volume_ratio.get(code, 0)
        if ratio < g.min_volume_ratio:
            continue

        info = daily_info[code]
        try:
            cd = current_data[code]
        except Exception:
            continue
        price = cd.last_price
        day_return = price / info["prev_close"] - 1
        if day_return <= 0 or day_return > g.max_buy_return:
            continue
        if price >= cd.high_limit * 0.995:
            continue

        score = (
            min(ratio, 3.5) * 35
            + day_return * 400
            + info["trend_strength"] * 100
            + info["breakout_strength"] * 80
            - info["volatility"] * 120
        )
        scored.append((code, score, ratio, day_return))

    scored.sort(key=lambda item: item[1], reverse=True)
    for code, score, ratio, day_return in scored[:10]:
        log.info(
            "candidate %s score=%.2f vol_ratio=%.2f day_return=%.2f%%"
            % (code, score, ratio, day_return * 100)
        )
    return [item[0] for item in scored]


def build_daily_signal_info(stock_list, context):
    """批量构建日线趋势信息。"""
    df = get_price(
        stock_list,
        count=g.daily_count,
        end_date=context.previous_date,
        frequency="daily",
        fields=["close", "high"],
        panel=False,
    )
    if df is None or df.empty:
        return {}

    closes = df.set_index(["time", "code"])["close"].unstack()
    highs = df.set_index(["time", "code"])["high"].unstack()
    current_data = get_current_data()
    result = {}

    for code in closes.columns:
        c = closes[code].dropna().values
        h = highs[code].dropna().values
        if len(c) < 60 or len(h) < 60:
            continue

        try:
            price = current_data[code].last_price
        except Exception:
            continue
        if price <= 0:
            continue

        ma5 = np.mean(c[-5:])
        ma10 = np.mean(c[-10:])
        ma20 = np.mean(c[-20:])
        ma60 = np.mean(c[-60:])
        if not (price > ma20 and ma5 > ma10 > ma20 and ma20 >= ma60 * 0.98):
            continue

        rsi6, rsi12 = calc_rsi_pair(np.append(c, price))
        if np.isnan(rsi6) or np.isnan(rsi12):
            continue
        if not (50 <= rsi6 <= 75 and rsi6 > rsi12):
            continue

        rolling_high = np.max(h[-20:])
        breakout_strength = price / rolling_high - 1
        if breakout_strength < -0.035:
            continue

        pct = pd.Series(c).pct_change().dropna()
        volatility = pct.tail(20).std()
        trend_strength = price / ma20 - 1
        result[code] = {
            "prev_close": c[-1],
            "trend_strength": trend_strength,
            "breakout_strength": breakout_strength,
            "volatility": volatility,
        }

    return result


def calc_rsi_pair(close_values):
    """计算 RSI6/RSI12。聚宽通常支持 talib。"""
    if len(close_values) < 20:
        return np.nan, np.nan
    rsi6 = talib.RSI(close_values.astype(float), timeperiod=6)
    rsi12 = talib.RSI(close_values.astype(float), timeperiod=12)
    return rsi6[-1], rsi12[-1]


def get_intraday_volume_ratio(stock_list, context):
    """当前时点成交量 / 过去若干交易日同时间累计成交量均值。"""
    trade_days = list(get_trade_days(end_date=context.current_dt.date(), count=g.volume_lookback + 1))
    if len(trade_days) < g.volume_lookback + 1:
        return {}

    start_dt = "%s 09:30:00" % trade_days[0]
    end_dt = context.current_dt
    df = get_price(
        stock_list,
        start_date=start_dt,
        end_date=end_dt,
        frequency="1m",
        fields=["volume"],
        panel=False,
    )
    if df is None or df.empty:
        return {}

    df = df.copy()
    df["date"] = df["time"].dt.date
    df["minute"] = df["time"].dt.time
    current_date = context.current_dt.date()
    current_time = context.current_dt.time()
    df = df[df["minute"] <= current_time]

    current = df[df["date"] == current_date].groupby("code")["volume"].sum()
    hist = df[df["date"] != current_date].groupby(["date", "code"])["volume"].sum().reset_index()
    hist_avg = hist.groupby("code")["volume"].mean()

    ratio = {}
    for code in stock_list:
        base = hist_avg.get(code, np.nan)
        today = current.get(code, np.nan)
        if pd.isna(base) or pd.isna(today) or base <= 0:
            continue
        ratio[code] = today / base
    return ratio


def execute_risk_sell(context, force_market_risk=False):
    """止损、跟踪止盈、市场风险、均线破位和时间止损。"""
    current_data = get_current_data()
    positions = list(context.portfolio.positions.keys())
    for code in positions:
        position = context.portfolio.positions[code]
        if position.closeable_amount <= 0:
            continue

        try:
            cd = current_data[code]
        except Exception:
            continue
        price = cd.last_price
        if price <= 0 or price <= cd.low_limit * 1.005:
            continue

        g.highest_price[code] = max(g.highest_price.get(code, price), price)
        reason = get_sell_reason(context, code, price, position, force_market_risk)
        if reason:
            order_target(code, 0)
            g.buy_dates.pop(code, None)
            g.highest_price.pop(code, None)
            log.info("sell %s reason=%s price=%.2f" % (code, reason, price))


def get_sell_reason(context, code, price, position, force_market_risk):
    cost = position.avg_cost
    if cost > 0 and price <= cost * g.stop_loss:
        return "stop_loss"

    high = g.highest_price.get(code, price)
    if cost > 0 and high >= cost * g.trailing_start and price <= high * g.trailing_stop:
        return "trailing_stop"

    if force_market_risk:
        return "market_risk"

    ma10 = get_current_ma(code, context, 10)
    if ma10 and price < ma10:
        buy_date = g.buy_dates.get(code)
        if buy_date is None or (context.current_dt.date() - buy_date).days >= g.min_hold_days:
            return "ma10_break"

    buy_date = g.buy_dates.get(code)
    if buy_date is not None and (context.current_dt.date() - buy_date).days >= g.max_hold_days:
        if cost > 0 and price < cost * 1.03:
            return "time_stop"

    return ""


def get_current_ma(code, context, window):
    df = get_price(
        code,
        count=window,
        end_date=context.previous_date,
        frequency="daily",
        fields=["close"],
        panel=False,
    )
    if df is None or df.empty or len(df) < window:
        return None
    values = list(df["close"].values)
    price = get_current_data()[code].last_price
    values.append(price)
    return np.mean(values[-window:])


def buy_new_positions(context, candidates):
    """按固定仓位买入新标的。"""
    if not candidates:
        return

    current_positions = set(context.portfolio.positions.keys())
    slots = g.max_hold_count - len(current_positions)
    if slots <= 0:
        return

    new_targets = [code for code in candidates if code not in current_positions]
    if not new_targets:
        return

    new_targets = new_targets[:slots]
    target_value = context.portfolio.total_value * g.position_pct
    cash = context.portfolio.available_cash
    if cash < 3000:
        return

    current_data = get_current_data()
    for code in new_targets:
        try:
            cd = current_data[code]
        except Exception:
            continue
        if cd.paused or cd.last_price >= cd.high_limit * 0.995:
            continue
        value = min(target_value, cash / len(new_targets))
        if value < 3000:
            continue
        order_target_value(code, value)
        g.buy_dates[code] = context.current_dt.date()
        g.highest_price[code] = cd.last_price
        log.info("buy %s target_value=%.2f" % (code, value))
