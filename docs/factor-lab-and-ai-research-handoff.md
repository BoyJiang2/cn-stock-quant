# 因子实验室与 AI 研究接入交接

更新日期：2026-06-20

> 全 A 股分块同步、交易日历、质量报告和市场状态大模型上下文见：
> `docs/full-market-data-and-llm-regime-handoff.md`。

本文记录本轮 Codex、OpenCode GLM 5.2、Claude Code DeepSeek V4 Pro 的协同实现，供后续 agent 直接接续。

## 本轮完成

### 1. 指数与股票数据隔离

新增 `IndexDailyBar`，指数不再写入股票 `daily_bars`。

关键接口：

- `MarketDataRepository.replace_index_daily_bars`
- `MarketDataRepository.index_daily_bars`
- `MarketDataRepository.index_daily_bar_count`

`/api/data/sync/index` 改用指数表；回测 benchmark 只读指数表。

这修复了 `000905`、`000852` 等指数代码与同代码股票发生覆盖的高风险问题。

### 2. OHLCV 因子实验室

目录：`backend/app/factors/`

已实现 21 个因子：

- 动量：5/20/60 日动量、20 日动量跳过最近 5 日。
- 趋势：MA20/MA60 偏离、上涨日占比。
- 反转：5 日反转。
- 风险：20/60 日波动率、下行波动率、20/60 日最大回撤、ATR、日内振幅。
- 流动性：成交额对数、5/20 日成交额比、成交量比、成交额稳定性、Amihud 非流动性、量价相关。

研究口径：

- 因子计算只使用 T 日及以前数据。
- 标签使用 T+1 收盘入场，T+1+h 收盘退出。
- 按日 MAD 去极值、robust z-score、百分位排名。
- 输出 IC、RankIC、分组收益、多空收益和换手率。
- 换手率定义为 `0.5 * sum(abs(w_t - w_t-1))`。
- Pearson IC 使用连续标准化因子值，不使用百分位值。

API：

- `GET /api/factors`
- `POST /api/factors/experiments/run`

### 3. 高级 OHLCV 策略

文件：`backend/app/strategy/advanced.py`

新增：

- `volatility_contraction_breakout`
- `trend_filtered_mean_reversion`

已接入策略注册表和现有回测页动态参数体系。

### 4. 受控 AI 因子候选协议

目录：`backend/app/ai_research/`

目标：允许 GLM、DeepSeek、RD-Agent 等提出候选，但不允许模型直接执行任意代码或绕过验证。

协议：

```json
{
  "candidate_name": "example",
  "components": {
    "volatility_60d": 0.4,
    "atr_pct_14d": 0.3,
    "amount_stability_20d": 0.3
  }
}
```

系统自动完成：

- 因子白名单校验。
- 非有限权重拒绝。
- 因子方向统一。
- 横截面标准化。
- T+1 标签。
- IC、RankIC、分组收益、换手率评估。

API：

- `GET /api/ai-research/capabilities`
- `POST /api/ai-research/factor-candidates/evaluate`

Qlib 适配：

- `app.ai_research.to_qlib_frame`
- 输出 `datetime`、`instrument` 和因子列。

## 真实小样本结果

数据：

- 28 只股票。
- 2024-01-02 至 2025-12-31。
- 5 日前瞻收益。

单因子前列：

| 因子 | RankIC |
|---|---:|
| volatility_60d | 0.0814 |
| atr_pct_14d | 0.0781 |
| volatility_20d | 0.0767 |
| intraday_range_20d | 0.0685 |
| amount_stability_20d | 0.0664 |

AI 候选：

| 候选 | RankIC | 多空分组收益差 | 换手率 |
|---|---:|---:|---:|
| DeepSeek low-vol-quality | 0.0962 | 0.6437% | 7.08% |
| DeepSeek low-vol-quality-tail | 0.0918 | 0.5767% | 6.65% |
| GLM quality-stability-reversal | 0.0831 | 0.3768% | 20.10% |
| GLM defensive-low-vol-dd | 0.0749 | 0.5561% | 4.46% |

这些结果不能视为投资证据：

- 样本只有 28 只。
- 只有约两年。
- 股票池使用当前存续股票，存在幸存者偏差。
- 候选是在观察同一份样本后提出，存在数据窥探。

候选必须冻结后，用新股票池、新时间窗口和 walk-forward 验证。

## 开源大模型/量化项目接入顺序

### Qlib

第一优先级。

- 使用 `to_qlib_frame` 导出因子面板。
- 接入 LightGBM 等模型做横截面收益预测。
- Qlib 输出预测分数后，转换为现有 `dict[symbol -> weight]`。

### RD-Agent

在 Qlib 数据链路稳定后接入。

- RD-Agent 只生成候选因子表达式、模型配置或受控 JSON 组合。
- 所有候选必须进入本项目因子评估和 walk-forward。
- 禁止 RD-Agent 直接修改实盘交易模块。

### FinGPT / 中文金融模型

等消息面数据模型完成后接入。

- 新闻分类。
- 个股情绪。
- 负面事件。
- 政策事件。
- 输出结构化情绪因子，不直接输出买卖订单。

### vnpy

用于模拟盘和最终交易执行，不用于替代研究引擎。

## 下一步

1. 研究池扩到至少 300 只。
2. 接入交易日历和严格覆盖检查。
3. 建立 point-in-time 股票池，降低幸存者偏差。
4. 冻结本轮 AI 候选，做 36 个月训练 + 6 个月验证的 walk-forward。
5. 增加成本压力、参数稳定性和策略排行榜。
6. 接 Qlib LightGBM 首个预测模型。

在完成第 1-4 项前，不把任何候选标记为“可实盘”。
