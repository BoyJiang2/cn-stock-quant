# 全市场因子重跑交接记录 2026-06-30

## 当前状态

- 本地全市场日线同步已完成，区间为 `2024-01-01` 到 `2025-12-31`。
- 同步进度为 `5515/5515`，状态文件 `backend/full-market-sync.state.json` 显示 `exit_reason=completed`。
- 数据库行情概览：`stock_count=5515`，`symbols_with_bars=5463`，`bar_count=2590571`，日期范围 `2024-01-02` 到 `2026-06-18`。
- 同步任务结果：`success=5463`，`empty=52`，无失败任务。
- 数据质量检查：预期交易日 `485`，完全覆盖股票 `4643`，存在缺口股票 `872`，缺失 bar 合计 `84422`。

## 本轮代码改动

- 因子实验研究池上限从 `300` 提升到 `6000`，用于支持全市场因子重跑。
- `covered_research_symbols` 默认纳入 `SH/SZ/BJ`，避免北交所股票被默认排除。
- 因子实验响应新增 `run_metadata`，包含 `run_hash`、选股池 hash、因子列表、日期窗口、样本数和非 PIT 降级原因。
- 新增 `backend/run_factor_experiment.py`，用于命令行复现实验并输出 JSON。
- 当前因子实验仍是非 PIT 版本，不能直接当作实盘证据。

## 2025 全市场实验结果

实验参数：

- 日期：`2025-01-01` 到 `2025-12-31`
- horizon：`5d`
- 股票池：`5071`
- bar 行数：`2110110`
- 因子面板：`2195743 x 24`

RankIC Top 10：

| factor | RankIC | RankIC IR | long-short |
| --- | ---: | ---: | ---: |
| volatility_20d | 0.062104 | 0.319508 | -0.000544 |
| atr_pct_14d | 0.060577 | 0.303349 | -0.001595 |
| intraday_range_20d | 0.058051 | 0.286543 | -0.002210 |
| amount_stability_20d | 0.057899 | 0.906260 | 0.004291 |
| reversal_5d | 0.048093 | 0.410546 | 0.004168 |
| volatility_60d | 0.043634 | 0.205558 | -0.002216 |
| downside_volatility_20d | 0.033689 | 0.166946 | -0.002577 |
| max_drawdown_20d | 0.010285 | 0.057892 | -0.003621 |
| max_drawdown_60d | -0.003348 | -0.017825 | -0.004801 |
| up_day_ratio_20d | -0.025002 | -0.313997 | -0.001033 |

RankIC Bottom 5：

| factor | RankIC | long-short |
| --- | ---: | ---: |
| momentum_60d | -0.069541 | -0.004156 |
| ma_gap_20d | -0.071240 | -0.005364 |
| momentum_20d | -0.076367 | -0.005313 |
| log_amount_20d | -0.080344 | -0.006499 |
| ma_gap_60d | -0.082854 | -0.005538 |

GLM 新增因子复核：

| factor | RankIC | RankIC IR | long-short |
| --- | ---: | ---: | ---: |
| sharpe_20d | -0.054213 | -0.447708 | -0.002742 |
| return_skew_20d | -0.032559 | -0.524498 | -0.000790 |
| vwap_gap_20d | -0.061252 | -0.595326 | -0.006315 |

## 利润优先的策略候选

1. `amount_stability_20d + reversal_5d`：当前最值得优先做成组合信号。两个因子的 RankIC 和 long-short 都为正，且一个偏交易稳定性，一个偏短周期反转。
2. 低波防守组合：`volatility_20d / atr_pct_14d / intraday_range_20d` 的 RankIC 靠前，但 long-short 符号不一致，需要确认分组方向后再作为仓位或风控滤镜。
3. 反向动量：`momentum_20d / momentum_60d / ma_gap_20d / ma_gap_60d` 在 2025 全市场为负，短期应优先测试反向使用或加入市场状态过滤。
4. 成交额拥挤过滤：`log_amount_20d` 和 `amount_ratio_5d_20d` 表现偏弱，适合作为过热和拥挤惩罚项，而不是正向 alpha。

## 下一步协同任务

- GPT/Codex：把因子实验 CLI、API 元数据和测试稳定下来，并提交推送。
- GLM：基于本文件和实验 JSON 设计 3 到 5 个可回测的组合因子策略，优先考虑赚钱、回撤和换手。
- DeepSeek：复核 Qlib LightGBM 接入边界，提出最小可落地的数据格式、训练入口和回测接线方案。
- 三方集成后，先把最强的组合因子策略接入现有 `dict[symbol -> weight]` 策略接口，再做 Qlib LightGBM 和新闻情绪模型。

## 风险和限制

- 当前股票池不是历史时点股票池，存在幸存者偏差。
- 当前使用 qfq 日线，历史价格可能因未来复权事件被修订。
- 缺口股票尚未按上市、退市、停牌、ST 或数据源缺失分类。
- long-short 收益方向必须结合 `FACTOR_DIRECTIONS` 和分组定义二次确认，不能只看 RankIC。
- 实盘前必须补历史上市/退市/ST 状态、指数历史成分和 PIT 因子缓存。
