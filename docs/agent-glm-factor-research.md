# GLM 因子资料研究输出

日期：2026-06-30

范围：只基于公开可访问资料和本项目已有文档/代码阅读，未使用登录后或付费内容。本文只做候选研究，不构成投资建议。

## 公开资料依据

- 聚宽/JoinQuant 公开“量化课堂”因子研究系列把因子灵感分为估值、资本结构、成长、技术、市值/行业中性化等方向；公开页面可访问性不稳定，但搜索结果和公开标题可作为方向参考。
- 同花顺 SuperMind 风险模型公开列出风格因子：市值、Beta、动量、估值、盈利、成长、杠杆、波动、非线性市值、流动性，并说明风险模型以多因子模型解释收益。
- 同花顺数据平台公开列出分析师一致预期、新闻评级、举牌、实控人变更、定增、机构调研、龙虎榜等另类数据。
- 同花顺估值指标表公开给出 PE LYR/MRQ/TTM、预测 PE、PEG 等口径。
- Qlib 开源 Alpha158/Alpha360 使用 OHLCV/VWAP、K 线形态、滚动 ROC/MA/STD/斜率/R2/残差/价格分位/RSV/价量相关等表达式。
- WorldQuant 101 Alphas 论文公开说明 101 个公式化 alpha 主要基于 open/high/low/close/volume/vwap，也包含行业中性化和少量基本面输入。
- 新闻舆情公开研报摘要显示，新闻因子可从热度和情感评价两个维度刻画，进一步用“包含基本面信息”和“强相关”过滤可改善表现。

参考链接：
- https://quant.10jqka.com.cn/view/help/8
- https://quant.10jqka.com.cn/view/dataplatform
- https://quant.10jqka.com.cn/view/dataplatform/detail/345
- https://raw.githubusercontent.com/microsoft/qlib/main/qlib/contrib/data/loader.py
- https://arxiv.org/pdf/1601.00991
- https://docs.dolphindb.com/en/Tutorials/wq101alpha.html
- https://bigquant.com/wiki/doc/Rg1jo3cTQB
- https://www.joinquant.com/community/post/detailMobile?postId=3709

## 候选因子清单

字段说明：
- 当前可落地字段：`open/high/low/close/volume/amount/trade_date/symbol`，可派生 `vwap = amount / volume`。
- 后续财务字段：总市值、流通市值、净利润 TTM、营业收入 TTM、净资产、总资产、总负债、经营现金流、毛利率、ROE/ROA、分红等。
- 后续新闻字段：`published_at/fetched_at/symbol/source/title/body/sentiment/label/relevance/event_type`。

| # | 因子 | 公式/定义 | 所需字段 | 未来函数/口径风险 |
|---:|---|---|---|---|
| 1 | kbar_body_1d | `(close - open) / open`，日 K 实体强度 | OHLC | 若用 T 日收盘后信号，成交必须从 T+1 开始；不能在 T 日盘中成交。 |
| 2 | intraday_range_20d | `mean((high - low) / close, 20)` | OHLC | 已有类似因子；复权 high/low 需与 close 同口径。 |
| 3 | upper_shadow_20d | `mean((high - max(open, close)) / (high - low + eps), 20)` | OHLC | 一字板或 high=low 要置 NaN；上影线可能是冲高回落也可能是盘口噪声。 |
| 4 | lower_shadow_20d | `mean((min(open, close) - low) / (high - low + eps), 20)` | OHLC | 停牌/一字板需处理；低位长下影常伴随高换手，需成本验证。 |
| 5 | close_location_20d | `mean((2*close - high - low) / (high - low + eps), 20)` | OHLC | 只代表收盘在日内区间的位置，不等同主动买卖盘。 |
| 6 | rsv_20d | `(close - rolling_min(low,20)) / (rolling_max(high,20)-rolling_min(low,20)+eps)` | OHLC | 突破/超买方向不固定，需按市场状态验证正向或反向。 |
| 7 | price_rank_20d | `rank(close in trailing 20 closes)`，0-1 | close | 只用历史窗口可无未来函数；与 MA gap/动量高度相关。 |
| 8 | distance_to_high_20d | `close / rolling_max(high,20) - 1` | high, close | 涨停附近可能买不到；需结合涨停过滤。 |
| 9 | distance_to_low_20d | `close / rolling_min(low,20) - 1` | low, close | 低位可能是下跌趋势延续；需与质量/停牌/ST 过滤结合。 |
| 10 | linear_slope_20d | `slope(close over 20) / close` | close | Qlib Alpha158 类；窗口内回归只用历史即可。 |
| 11 | trend_rsquare_20d | `R^2(close ~ time, 20)` | close | R2 不区分上涨/下跌，需与 slope 同用。 |
| 12 | trend_residual_20d | `last residual(close ~ time, 20) / close` | close | 可作为趋势偏离/回归信号；方向需单独评估。 |
| 13 | gap_overnight_1d | `open / prev_close - 1` | open, close | T 日 open 信息不能用于 T 日开盘前下单；若日线回测，只能用于 T+1 信号或收盘后研究。 |
| 14 | intraday_return_1d | `close / open - 1` | open, close | 同上，T 日完整 K 线只能 T 收盘后知道。 |
| 15 | reversal_3d | `close.shift(3) / close - 1` | close | 当前已有 5 日反转；短窗口换手高，交易成本敏感。 |
| 16 | reversal_10d | `close.shift(10) / close - 1` | close | 与 5 日反转相关但更慢；需验证是否降低换手。 |
| 17 | skip_momentum_60_5 | `close.shift(5) / close.shift(65) - 1` | close | 跳过近 5 日避免短反转；不能把未来收益混入窗口。 |
| 18 | volatility_ratio_5_20 | `std(ret,5) / std(ret,20)` | close | 高波放大既可能是机会也可能是风险；方向不稳定。 |
| 19 | downside_vol_ratio_5_20 | `downside_vol(ret,5) / downside_vol(ret,20)` | close | 下行波动低可能是停牌/无量，要结合 volume>0。 |
| 20 | beta_to_000300_60d | `cov(stock_ret, index_ret)/var(index_ret)` | close, benchmark close | 指数历史必须按交易日对齐；benchmark 不能缺失或未来修订。 |
| 21 | downside_beta_60d | 只在指数下跌日估计 beta | close, benchmark close | 下跌样本太少时置 NaN；适合风控，不一定是 alpha。 |
| 22 | amount_stability_20d | `mean(amount,20)/std(amount,20)` | amount | 已有且 2025 全市场表现较好；amount=0 或 std=0 要置 NaN。 |
| 23 | amount_acceleration_5_20 | `mean(amount,5)/mean(amount,20) - 1` | amount | 放量可能是拥挤或利好，需要和收益方向/新闻事件交互。 |
| 24 | amount_shock_z_20 | `(amount - mean(amount,20))/std(amount,20)` | amount | 当日收盘后才知道；极端放量常伴随涨跌停不可交易。 |
| 25 | price_volume_corr_20d | `corr(ret, log(volume+1), 20)` | close, volume | 已有类似因子；相关性方向随市场状态变化。 |
| 26 | volume_return_divergence_20d | `corr(close/prev_close, log(volume/prev_volume+1), 20)` | close, volume | Qlib CORD 类；prev_volume=0 需处理。 |
| 27 | amihud_20d | `mean(abs(ret)/amount,20)` | close, amount | 已有；amount 极小会爆值，需 winsorize。 |
| 28 | rolling_vwap_gap_20d | `(close - sum(amount,20)/sum(volume,20)) / rolling_vwap` | close, amount, volume | 已有类似；amount/volume 单位必须一致。 |
| 29 | earnings_yield_ttm | `net_profit_ttm / market_cap`，或 `1 / PE_TTM` | 财务, market_cap | 财报发布日期必须 PIT；不能用未来 TTM 修订。 |
| 30 | book_to_market | `equity / market_cap`，或 `1/PB` | 财务, market_cap | 净资产用已公告最新报告期；负净资产需剔除。 |
| 31 | sales_to_price | `revenue_ttm / market_cap`，或 `1/PS` | 财务, market_cap | TTM 滚动必须按公告日可见；周期行业口径差异大。 |
| 32 | roe_ttm | `net_profit_ttm / avg_equity` | 财务 | avg_equity 两端报告期不能用未公告数据。 |
| 33 | gross_margin_ttm | `gross_profit_ttm / revenue_ttm` | 财务 | 财务报表行业差异大，金融股需单独口径。 |
| 34 | accrual_quality | `(net_profit_ttm - operating_cashflow_ttm) / total_assets` | 财务 | 现金流公告滞后；必须用公告日而非报告期日期。 |
| 35 | leverage_assets | `total_liabilities / total_assets` | 财务 | 行业中性化必要；金融股需排除或独立处理。 |
| 36 | revenue_growth_yoy | `revenue_ttm / revenue_ttm_1y_ago - 1` | 财务 | 分母接近 0 会异常；需 winsorize 和缺失过滤。 |
| 37 | profit_growth_yoy | `net_profit_ttm / net_profit_ttm_1y_ago - 1` | 财务 | 亏损转盈利会产生极端值；建议用差分/资产缩放辅助。 |
| 38 | dividend_yield | `cash_dividend_ttm / market_cap` | 分红, market_cap | 除权除息日和公告日不同；不能提前使用未来分红。 |
| 39 | analyst_revision_60d | `consensus_np_now / consensus_np_60d_ago - 1` | 一致预期 | 预测数据必须有历史快照；不能用当前覆盖历史。 |
| 40 | news_sentiment_sum_5d | `sum(sentiment * relevance, published in last 5 trading days)` | 新闻 | 必须用 `published_at` 和 `fetched_at`，盘后新闻只能进下一交易日。 |
| 41 | news_heat_change_5_20 | `news_count_5d / avg_news_count_20d - 1` | 新闻 | 热度高可能是坏消息；需和 sentiment/event_type 交互。 |
| 42 | negative_news_shock_3d | `count(sentiment<阈值 and relevance high, 3d)` | 新闻 | 强负面更适合风控剔除；新闻去重和转载源合并很关键。 |
| 43 | sentiment_disagreement_20d | `std(sentiment,20)` 或正负新闻比例分歧 | 新闻 | 分歧可能代表关注度，不一定负向；样本数过少置 NaN。 |
| 44 | fundamental_news_sentiment_20d | 仅 `label=基本面` 且强相关新闻的情绪均值/和 | 新闻 | 标签模型不能用未来事件训练泄漏；需保存模型版本。 |
| 45 | announcement_risk_20d | 减持、监管、问询、诉讼、业绩预亏等事件计数/权重和 | 公告/新闻事件 | 事件发生日、公告日、抓取日要区分；更适合风控过滤。 |
| 46 | institution_visit_heat_60d | 机构调研次数、参与机构数、环比变化 | 机构调研 | 调研披露有滞后；只能在披露日后使用。 |

## 第一批最适合工程实现的 10 个

优先级标准：只依赖当前日线 OHLCV/成交额、容易向量化、可接入现有 `FactorLab`，且能补足当前 24 个因子的空白。

| 优先级 | 因子 | 推荐原因 | 最小实现备注 |
|---:|---|---|---|
| 1 | upper_shadow_20d | Qlib kbar 类，补“冲高回落/抛压”形态；和现有波动因子不完全重复。 | rolling mean；high=low 置 NaN。 |
| 2 | lower_shadow_20d | 捕捉低位承接/下影反转，可和 `reversal_5d` 组合。 | rolling mean；方向需实证。 |
| 3 | close_location_20d | 日内收盘强弱，工程简单，信息密度高。 | `(2C-H-L)/(H-L)` 后 rolling。 |
| 4 | rsv_20d | 价格在 20 日高低区间的位置，KDJ/技术因子基础。 | 先做原始 RSV，不急着做 K/D/J 平滑。 |
| 5 | price_rank_20d | Qlib Rank 类，和 MA gap/动量相近但更稳健。 | rolling apply rank last value。 |
| 6 | linear_slope_20d | 趋势强度，比简单动量更平滑。 | 可用 rolling 回归闭式公式向量化。 |
| 7 | trend_rsquare_20d | 趋势线性质量，用来区分趋势和噪声。 | 与 slope 组合，单独方向不明确。 |
| 8 | trend_residual_20d | 趋势残差，适合作为“偏离趋势后的反转”信号。 | last close 减回归预测值，再除 close。 |
| 9 | volume_return_divergence_20d | Qlib CORD 类，捕捉价量变化一致/背离。 | `corr(C/Ref(C,1), log(V/Ref(V,1)+1),20)`。 |
| 10 | amount_shock_z_20 | 过热/拥挤过滤，与当前 stable_reversal 的成交额稳定性互补。 | `zscore(amount,20)`；极值 winsorize。 |

不建议第一批就做的内容：
- 财务因子：价值很高，但必须先有 PIT 财报公告日、TTM 口径和复权/市值口径，否则未来函数风险大。
- 新闻因子：需要先完成 `published_at/fetched_at`、去重、股票归因、情绪模型版本记录；否则比行情因子更容易时间穿越。
- WorldQuant 101 复杂组合：可作为灵感，但应先拆成可解释基础算子，避免一次性引入难排错表达式。

## 未来函数风险总原则

1. 行情日线因子：T 日 OHLCV 完整值只能在 T 日收盘后可见，标签/回测成交必须从 T+1 开始。
2. 财务因子：以公告日/实际可获得日为准，不以报告期截止日为准；所有 TTM、同比、预测修订都要有历史快照。
3. 新闻因子：同时保存发布时间和抓取时间；盘后发布或盘后抓取的新闻不得回填到当日盘中信号。
4. 当前全市场因子实验仍非 PIT universe；退市、ST、上市日期和历史指数成分会影响真实可交易股票池。
5. qfq 历史价格会被未来公司行为修订；严谨版本应保存数据版本或使用当时可见复权因子。
