# 下一阶段实施计划：PIT 数据、全市场因子、Qlib、RD-Agent 与新闻情绪

更新日期：2026-06-20

本文是后续 Codex、OpenCode GLM 5.2、Claude Code DeepSeek V4 Pro 的共同交接文档。
目标只有一个：建立能够持续产生、验证和淘汰有效策略的研究流水线，最终继续输出
`dict[symbol -> weight]`，不绕过现有回测、风控和交易计划接口。

## 当前基线

- 沪深 300 基准缺失时已支持自动同步，东方财富失败会降级到新浪和腾讯。
- 研究池不再要求请求区间绝对首尾覆盖，允许停牌、新上市和本地数据末日早于请求末日。
- 研究池默认排除当前 ST/退市风险名称，并返回空池诊断信息。
- 真实回测已通过：30 只股票、485 个沪深 300 基准点，`run_id=8`。
- 后端完整测试：347 passed。
- 全市场日线同步仍在后台运行；在同步完成前，因子结果只能标记为阶段性结果。

## Sprint 1 实施结果（2026-06-20）

已完成：

- 新增 PIT 表：证券状态、名称、指数成分、指数权重、研究池快照。
- 新增 PIT repository、AkShare provider、同步协调器和 `/api/data/pit/*` API。
- 回测请求支持 `point_in_time=true` 固定时点 universe。
- 回测响应返回 `universe_metadata`，包含池指纹、缺失状态、缺失名称和降级标记。
- 历史退市回填：706 条初始记录；当前状态同步后状态表共 6217 行。
- 当前证券名称快照：5286 行，只从抓取日生效，不向历史回填。
- 沪深 300 当前成分与权重快照：各 300 行。
- 当前 ST 接口不可用时，从全市场股票名称保守识别，当前识别 225 只。
- 重复同步会关闭摘帽、名称变更和指数移出成员的开放区间。
- 历史退市股即使不在当前 `stocks` 表，也会进入历史 PIT 候选集合。
- 完整后端测试：400 passed。
- 真实 PIT 回测：`run_id=11`，30 只股票，明确标记 `degraded=true`。

仍属降级状态：

- AkShare 不提供完整历史 ST/名称变更，当前名称不能代表过去。
- 沪深 300 目前只有 2026-06-18 成分快照和 2026-05-29 权重快照。
- 历史指数成分必须继续从中证指数历史文件或 Tushare 回填。
- 全市场日线同步仍在运行，当前进度约 15%。

因此，PIT 接口已经可用且不会静默制造历史，但在历史 ST 和指数成分回填完成前，
`pit_degraded=true` 的结果不得作为可实盘证据。

## 总体顺序

严格按以下依赖执行，不并行跳过数据地基：

1. 历史上市、退市、名称和 ST 状态。
2. 指数历史成分及权重。
3. Point-in-Time 研究池和滚动 universe。
4. 全市场因子重跑与因子仓库。
5. Qlib Alpha158 + LightGBM 基线。
6. RD-Agent 受控研发循环。
7. 新闻、公告和舆情数据管道。
8. 新闻情绪模型与价格因子融合。
9. Walk-forward、成本压力测试、模拟盘观察。

在第 3 步完成前，不把任何全市场因子或机器学习结果标记为可实盘候选。

## Phase A：Point-in-Time 数据地基

Owner：GLM  
接口审查：DeepSeek  
集成与最终验收：Codex

### 数据模型

新增独立表，保留现有 `stocks` 作为当前快照：

- `security_status_history`
  - `symbol`
  - `status`: `normal/st/*st/suspended/delisted`
  - `valid_from`
  - `valid_to`
  - `announced_at`
  - `source`
  - `confidence`
- `security_name_history`
  - `symbol`
  - `name`
  - `valid_from`
  - `valid_to`
  - `announced_at`
- `index_constituent_history`
  - `index_symbol`
  - `symbol`
  - `valid_from`
  - `valid_to`
  - `announced_at`
- `index_weight_snapshots`
  - `index_symbol`
  - `symbol`
  - `trade_date`
  - `weight`
- `research_universe_snapshots`
  - `universe_key`
  - `as_of_date`
  - `symbol`
  - `eligible`
  - `exclusion_reason`
  - `data_version`

所有历史记录必须保留 `source`、抓取时间和置信度。缺少历史 ST 数据时不得静默使用当前
名称替代，应返回 `degraded=true`。

### 核心接口

```python
status_as_of(symbol: str, as_of_date: date) -> SecurityStatus | None
name_as_of(symbol: str, as_of_date: date) -> str | None
index_members_as_of(index_symbol: str, as_of_date: date) -> list[IndexMember]
build_universe_as_of(spec: UniverseSpec, as_of_date: date) -> UniverseSnapshot
```

### 验收

- 2020 年回测能够包含 2022 年才退市的股票。
- 2022 年回测不会因为股票在 2024 年变成 ST 而提前剔除。
- 沪深 300 在不同调仓日期返回不同历史成分。
- 所有点时查询都满足 `announced_at <= as_of_date`。
- 数据不完整时明确输出降级比例和缺失来源。

## Phase B：全市场因子工厂

Owner：Codex  
接口审查：GLM  
结果审查：DeepSeek

### 因子范围

第一批扩展到至少 80 个可解释因子：

- 动量和趋势：5/10/20/60/120/250 日收益、新高距离、均线斜率、趋势一致性。
- 反转：1/3/5/10/20 日反转、隔夜和日内反转、跳空回补。
- 波动和尾部风险：实现波动率、下行波动、偏度、峰度、VaR、CVaR、最大回撤、ATR。
- 流动性：成交额、换手代理、Amihud、量价相关、零成交、冲击成本代理。
- K 线结构：实体、上下影线、振幅、缺口、连续涨跌、涨跌停接近度。
- 横截面风险：Beta、残差波动、相对强弱、行业中性版本。
- 基本面：待 PIT 财务数据可用后加入估值、质量、成长、现金流和股息。
- 事件和消息：在 Phase E 中追加。

### 计算与存储

- 原始行情保留 SQLite；批量因子结果写 Parquet，按 `factor/date` 分区。
- 每次运行生成不可变 `dataset_version`、`universe_version`、`factor_code_hash`。
- 支持增量重算、失败续跑和单因子重跑。
- 因子值只使用 T 日及以前数据；标签从 T+1 开始。

### 评估门槛

- IC、RankIC、ICIR。
- 分组收益和多空差。
- 换手率、容量和成本后收益。
- 行业和市值中性后的有效性。
- 训练、验证、测试三段完全隔离。
- Walk-forward 至少 36 个月训练、6 个月验证、6 个月测试；数据不足时缩短但必须标注。
- 因子必须通过多个年份和多个市场状态，不以单次最高收益排名。

## Phase C：Qlib Alpha158 + LightGBM

Owner：DeepSeek  
数据与接口审查：Codex  
可复现性审查：GLM

### 隔离原则

- Qlib 使用独立 Conda 环境或独立 worker 进程，不直接污染 FastAPI 运行环境。
- 主应用通过任务协议调用 worker，不 import Qlib 的重依赖。
- 输入是版本化 PIT universe、OHLCV 和标签；输出是版本化预测文件。
- Qlib 官方公开数据不作为生产真值，使用本项目同步并校验的数据生成 Qlib 数据集。

### 第一版工作流

1. 将本地 PIT 数据导出为 Qlib instrument/calendar/feature 数据。
2. 跑 Alpha158 + LightGBM 官方基线配置。
3. 使用滚动训练，禁止随机切分时间序列。
4. 保存模型、参数、特征版本、训练区间和预测区间。
5. 将每日预测转为横截面分数。
6. 经过流动性过滤、top-N、逆波动加权和风险裁剪。
7. 输出 `dict[symbol -> weight]` 给现有引擎。

### 模型注册

新增：

- `model_runs`
- `model_artifacts`
- `model_predictions`
- `model_evaluations`

模型晋级门槛必须同时包含样本外 IC、成本后收益、最大回撤、稳定性和容量。

## Phase D：RD-Agent 受控研发

Owner：DeepSeek  
沙箱和审批协议：Codex  
因子合法性审查：GLM

RD-Agent 只能提出候选，不能直接修改实盘模块或下单：

```text
研究目标
  -> RD-Agent 生成候选因子表达式/模型配置
  -> 静态白名单与资源限制
  -> 隔离 worker 执行
  -> PIT 数据 + walk-forward 评估
  -> Critic 比较基线
  -> 候选登记或淘汰
```

禁止：

- 访问 broker 凭证。
- 修改 `broker/`、风控上限和生产策略注册表。
- 运行未限制的系统命令。
- 使用测试期结果继续调参。

每个候选必须记录 prompt、代码差异、依赖、运行日志、数据版本和淘汰原因。

## Phase E：新闻、公告与情绪

Owner：GLM  
模型评估：DeepSeek  
因子融合和回测：Codex

### 数据管道

```text
NewsProvider
  -> 原文归档
  -> URL/标题/正文哈希去重
  -> 股票、行业、指数实体映射
  -> 发布时间与可交易时间对齐
  -> 事件分类
  -> 情绪模型
  -> 日频聚合因子
```

首批数据源：

- 巨潮资讯公告。
- 交易所监管和问询。
- AkShare 可用新闻接口。
- 东方财富公开新闻。

股吧、雪球等高噪声源后接，必须单独标记来源可信度。

### 数据表

- `news_items`
- `news_entities`
- `news_events`
- `sentiment_scores`
- `sentiment_daily_features`

必须同时保存 `published_at` 和 `fetched_at`。盘后新闻只能进入下一交易日因子，禁止按自然日
直接回填，防止新闻时间穿越。

### 模型路线

1. 先做词典/规则基线，验证时间对齐和事件标签。
2. 再接中文金融情绪分类模型或 FinGPT 类模型。
3. LLM 负责结构化事件分类和解释，不直接输出订单。
4. 第一版只作为风险过滤器和仓位调节器：
   - 强负面公告降权。
   - 市场恐慌提高现金比例。
   - 极端乐观降低追涨权重。
5. 完成“价格因子基线 vs 加入情绪”的严格 A/B walk-forward。

## 三模型协作流程

每个 Phase 都执行：

1. GLM、Codex、DeepSeek 分别理解需求并提交方案。
2. Codex 比较方案，明确单 owner 文件和共享接口。
3. 三方分别实现不重叠模块。
4. GLM 阅读 Codex 的数据/因子接口。
5. Codex 阅读 DeepSeek 的 Qlib/RD-Agent 接口。
6. DeepSeek 阅读 GLM 的 PIT/新闻接口。
7. 三方修复集成问题。
8. 运行单元测试、全量测试、数据质量检查和真实样本 walk-forward。
9. 只有 Codex 负责最终合并、服务重启和用户可见验收。

## 近期任务队列

### Sprint 1：PIT 最小闭环

- GLM：新增历史状态、名称、指数成分模型和 repository。
- Codex：定义 `UniverseSpec/UniverseSnapshot`，接入回测请求但默认不开启。
- DeepSeek：编写幸存者偏差、未来函数和指数换仓测试矩阵。
- 三方：使用 synthetic 数据证明 PIT 与当前快照回测的 universe 差异。

### Sprint 2：全市场因子重跑

- 等全市场日线同步达到可接受覆盖率。
- Codex：Parquet 因子缓存和批量 runner。
- GLM：数据质量与断点续跑。
- DeepSeek：walk-forward 评估、因子相关性和淘汰规则。

### Sprint 3：LightGBM 基线

- DeepSeek：独立 Qlib worker、Alpha158、LightGBM。
- Codex：模型预测到目标仓位适配器。
- GLM：数据版本和结果复现审查。

### Sprint 4：新闻情绪与 RD-Agent

- GLM：新闻采集和结构化入库。
- DeepSeek：情绪模型与 RD-Agent 候选生成。
- Codex：时间对齐、因子融合、风险过滤和 A/B 回测。

## 停止条件

出现以下任一情况，不进入下一阶段：

- PIT universe 无法复现。
- 数据源时间戳不可信。
- 训练集、验证集、测试集发生交叉。
- 模型只在无成本回测中有效。
- 因子在扩大股票池后失效。
- RD-Agent 不能被限制在研究沙箱。
- 新闻无法确认发布时间或去重。

赚钱优先不等于追求最高历史收益。优先级是：无未来函数、可交易、成本后有效、跨时期稳定，
最后才是提高收益率。
