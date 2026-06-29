# JoinQuant 趋势动量策略说明

文件：`strategies/joinquant_momentum_market_filter.py`

这是一份独立的 JoinQuant/聚宽策略脚本，不依赖本项目后端。当前版本已参考桌面 `1.txt` 的可运行风格重写。

## 主要变化

原版本使用：

- `before_trading_start(context)` 生成信号
- `handle_data(context, data)` 执行调仓
- 指数成分股池
- 纯日频动量排序

新版本改为：

- `run_daily(risk_monitor, time="10:30")`
- `run_daily(risk_monitor, time="11:30")`
- `run_daily(risk_monitor, time="14:00")`
- `run_daily(rebalance, time="14:30")`
- 全市场市值过滤股票池
- 日线趋势 + RSI + 突破强度 + 盘中量比综合打分
- 盘中只做风控卖出，尾盘才开新仓

这样更接近聚宽常见可运行策略模板，也更符合 T+1 下“少开仓、多风控”的使用方式。

## 策略逻辑

- 市场过滤：默认用 `000852.XSHG`，指数趋势满足条件才允许开新仓。
- 股票池：全市场股票，按市值过滤，默认 30-200 亿。
- 排除：ST、停牌、科创板、北交所、上市时间不足 120 日。
- 日线过滤：
  - 当前价高于 20 日均线
  - `MA5 > MA10 > MA20`
  - `MA20` 不明显弱于 `MA60`
  - RSI6 在 50-75，且 RSI6 大于 RSI12
  - 价格不能明显跌破近 20 日高点
- 盘中确认：
  - 当前累计成交量 / 过去同时间成交量均值 >= 1.5
  - 当日涨幅为正，但不能超过 8.5%
  - 不追接近涨停的股票
- 买入：
  - 最多持有 5 只
  - 单只目标仓位约 18%
  - 14:30 附近开新仓
- 卖出：
  - 固定止损
  - 跟踪止盈
  - 市场风险
  - 跌破 MA10 且满足最小持有天数
  - 超过最大持有天数且收益不足

## 主要参数

在 `initialize(context)` 中修改：

```python
g.market_index = "000852.XSHG"
g.min_market_cap = 30
g.max_market_cap = 200
g.max_hold_count = 5
g.position_pct = 0.18
g.min_volume_ratio = 1.5
g.max_buy_return = 0.085
g.stop_loss = 0.95
g.trailing_start = 1.08
g.trailing_stop = 0.94
g.max_hold_days = 10
g.min_hold_days = 2
```

## 如何在聚宽使用

1. 打开 JoinQuant/聚宽，新建股票策略。
2. 将 `strategies/joinquant_momentum_market_filter.py` 内容完整复制进去。
3. 先使用日频或分钟级回测环境测试。
4. 如果平台不支持 `talib`，需要把 `calc_rsi_pair()` 改成手写 RSI。
5. 如果候选太少，可以放宽：
   - `g.min_volume_ratio`
   - `g.min_market_cap`
   - `g.max_market_cap`
   - `g.max_buy_return`

## 兼容注意

- 本版本使用 `get_price(..., panel=False)`，这是聚宽常见批量取数写法。
- 策略依赖 `talib.RSI`。聚宽大多数环境支持，但如果报错，需要改成手写 RSI。
- `run_daily(..., time="14:30")` 需要在聚宽支持定时函数的环境运行。
- `context.previous_date` 是聚宽常见字段；如果你的环境不存在，需要改用 `get_trade_days()` 获取上一交易日。
- 这份策略不是投资建议，只是用于研究和回测。
