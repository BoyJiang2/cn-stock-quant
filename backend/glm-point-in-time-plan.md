# Point-in-Time 证券状态、名称、指数成分与研究池方案（GLM 5.2 架构评审）

负责人：OpenCode GLM 5.2（架构子代理，仅评审与方案，不改代码）
项目：`D:\CursorProjects\cn-stock-quant`
更新日期：2026-06-20
状态：方案文档，待 Codex 拆分任务后由各代理按 owner 边界实施

> 本文件只由 GLM 维护。其他代理（Codex/DeepSeek）请勿覆盖本文件，可在各自 plan 文件中引用。
> 共享接口文件遵循 `docs/profit-first-roadmap-with-sentiment.md` Phase 8 的单 owner 约定：
> `backend/app/strategy/base.py`、`backend/app/schemas/backtest.py`、`backend/app/data/repository.py`、`backend/app/backtest/engine.py`。
> 本方案新增的表与 provider 属于 GLM 模块 A，不改动上述共享文件的行为签名（仅新增方法）。

## 0. 目标与范围

消除两类致命偏差：

1. **幸存者偏差（Survivorship Bias）**：回测股票池只含"当前存续"证券，已退市证券被剔除，导致历史收益虚高。
2. **未来函数（Look-ahead Bias）**：用"当前"ST/名称/指数成分去过滤"历史"某日的股票池，把未来才发生的状态用于过去。

本方案覆盖四类点时（Point-in-Time, PIT）数据：

- A 股历史上市/退市/ST/停牌状态区间
- 证券名称历史（含 ST/*ST/退 前缀变迁）
- 指数历史成分及权重（沪深300、中证500、中证1000、创业板指等）
- 点时研究池（按 `as_of` 日期重建可交易宇宙）

显式不在本轮范围：基本面财务因子点时（PE/PB/ROE）、消息面点时、ETF 成分。这些在 `docs/profit-first-roadmap-with-sentiment.md` 后续 Phase 处理。

## 1. 现状差距

### 1.1 证券状态：单行快照，无历史区间

`backend/app/models/entities.py:11-19` 的 `Stock` 表：

```
symbol(PK) | name | exchange | list_date(String) | status(String) | updated_at
```

问题：

- `status` 是单一当前值（`active` 等），无法表达"2020 年正常、2023-06 起 ST、2024-12 退市"的时间线。
- `list_date` 是 `String(16)` 而非 `Date`，且无 `delist_date`、无退市原因。
- `name` 是单一当前值，无法回查某历史日期的名称（用于 ST 前缀判定和展示）。

### 1.2 研究池过滤：用当前状态/名称过滤历史宇宙 → 幸存者偏差 + 未来函数

`backend/app/data/repository.py:688-713` `_research_stock_filters`：

```python
filters = [Stock.exchange.in_(exchanges), Stock.status == "active"]
if exclude_risk_names:
    # 用 Stock.name 的当前值做 ST/*ST/SST/S*ST 前缀和"退"字过滤
```

`select_research_symbols`（`repository.py:539-604`）和 `covered_research_symbols`（`repository.py:508-537`）都依赖该过滤器。后果：

- 一只 2022 年退市的股票，当前 `status != active` → 从 2020 年回测宇宙中被剔除（幸存者偏差）。
- 一只 2024 年才被 ST 的股票，2020 年回测时被当前 ST 名称误剔除（未来函数）。
- 一只 2020 年曾是 ST、现已摘帽的股票，2020 年回测时因当前名称不含 ST 而被错误保留（反向未来函数）。

`docs/factor-lab-and-ai-research-handoff.md:131-132` 与 `docs/full-market-data-and-llm-regime-handoff.md:22` 已明确承认：当前"全 A 股"指 AkShare 当前存续列表，历史回测存在幸存者偏差。

### 1.3 指数成分：完全缺失

- `backend/app/data/symbols.py:3` `INDEX_SYMBOL_WHITELIST = {"000300","000905","000852","399006"}` 硬编码 4 个指数代码。
- `entities.py` 无 `IndexConstituent` / 权重表。`IndexDailyBar`（`entities.py:39-52`）只存指数自身日线，不含成分与权重。
- 回测 benchmark 只读指数日线（`backtest.py:103`），无法做"指数成分股策略"，也无法用历史成分定义宇宙。
- 用"今天的沪深300成分"做历史回测 = 成分层面的幸存者偏差（半年一次调仓带来的纳入/剔除无法体现）。

### 1.4 已有的"好"基础（无需重做）

- 回测引擎信号侧无未来函数：`backend/app/backtest/engine.py:100` `history = bars[bars["trade_date"] <= current_date]`，策略只能看当日及以前。
- 前瞻收益标签严格 T+1：`backend/app/factors/returns.py:8-11`，`fwd_h(t)=close(t+1+h)/close(t+1)-1`，无未来函数。
- 因子计算逐列独立、`FactorInputs`（`factors/spec.py:28-43`）已隔离跨股票。
- `TradingCalendar`（`entities.py:55-60`）已存在，覆盖判断按交易日。
- 数据质量报告已自带告警：`repository.py:488-491` 明确 "point-in-time security status is not yet available"。

**结论：未来函数/幸存者偏差的唯一来源是宇宙构建层（状态/名称/成分），引擎与因子层本身是干净的。本方案聚焦数据层与宇宙重建，不动引擎与因子内核。**

## 2. 建议表结构

全部新增表，不修改 `Stock`/`DailyBar`/`IndexDailyBar`/`TradingCalendar` 现有列（迁移零破坏）。`Stock` 保留为"当前快照"视图，供向后兼容与 UI 展示。

### 2.1 `security_status` —— 证券状态区间（上市/退市/ST/停牌）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | Integer | PK, autoincrement | |
| `symbol` | String(16) | index, not null | 规范化 6 位代码 |
| `status` | String(16) | not null | `listed`/`delisted`/`st`/`sst`/`st_star`(*ST)/`suspended`/`normal` |
| `valid_from` | Date | not null | 该状态生效日（上市日/ST 公告生效日/退市日） |
| `valid_to` | Date | nullable | 该状态结束日（null=至今）；下一段 `valid_from` = 上一段 `valid_to`+1 |
| `announced_at` | Date | nullable, index | 市场知晓日；PIT 查询用此过滤，避免未来函数。未知则回退 `valid_from` |
| `delist_reason` | String(64) | nullable | 退市原因（面值/重大违法/主动等） |
| `source` | String(32) | not null | `akshare`/`tushare`/`csindex`/`manual` |
| `confidence` | String(8) | not null, default `high` | `high`/`medium`/`low`；`announced_at` 缺失时降级 |
| `updated_at` | DateTime | default utcnow | |

约束：`UniqueConstraint("symbol","status","valid_from", name="uq_sec_status_symbol_status_from")`。
索引：`(symbol, valid_from)`、`(announced_at)`。

PIT 状态查询语义：`as_of` 日 d 的有效状态 = `valid_from <= d < coalesce(valid_to, 9999-12-31)` 且 `announced_at <= d`（若 `announced_at` 为 null 则用 `valid_from` 并标记 `confidence=medium`）。

### 2.2 `security_name` —— 证券名称历史

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | Integer | PK | |
| `symbol` | String(16) | index, not null | |
| `name` | String(64) | not null | 含 ST/*ST/退 前缀的完整名称 |
| `valid_from` | Date | not null | 该名称启用日 |
| `valid_to` | Date | nullable | 该名称结束日（null=至今） |
| `announced_at` | Date | nullable | 名称变更公告日 |
| `source` | String(32) | not null | |
| `updated_at` | DateTime | default utcnow | |

约束：`UniqueConstraint("symbol","valid_from", name="uq_sec_name_symbol_from")`。
PIT 名称查询：`valid_from <= d < coalesce(valid_to, 9999-12-31)` 取 `valid_from` 最大者。

### 2.3 `index_constituent` —— 指数成分区间

CSI 指数半年度调仓，采用区间模型节省存储：

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | Integer | PK | |
| `index_symbol` | String(16) | not null, index | 如 `000300` |
| `symbol` | String(16) | not null, index | 成分股代码 |
| `valid_from` | Date | not null | 纳入日 |
| `valid_to` | Date | nullable | 剔除日（null=至今在册） |
| `announced_at` | Date | nullable | 调仓公告日（CSI 通常提前公布） |
| `source` | String(32) | not null | `csindex`/`akshare`/`tushare` |
| `updated_at` | DateTime | default utcnow | |

约束：`UniqueConstraint("index_symbol","symbol","valid_from", name="uq_idx_const_index_sym_from")`。
索引：`(index_symbol, valid_from)`。

### 2.4 `index_weight_snapshot` —— 指数权重快照

权重随调仓变化且日度微调，用快照表（非区间）：

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | Integer | PK | |
| `index_symbol` | String(16) | not null, index | |
| `symbol` | String(16) | not null, index | |
| `trade_date` | Date | not null | 权重快照日（通常调仓生效日） |
| `weight` | Float | nullable | 自由流通调整市值权重（0-1） |
| `source` | String(32) | not null | |
| `updated_at` | DateTime | default utcnow | |

约束：`UniqueConstraint("index_symbol","symbol","trade_date", name="uq_idx_wt_index_sym_date")`。
PIT 权重查询：取 `<= d` 的最近一次 `trade_date` 快照（前向填充）。

### 2.5 `research_pool_member` —— 点时研究池成员（物化结果）

把 PIT 宇宙物化，保证回测可复现、可审计：

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | Integer | PK | |
| `pool_key` | String(64) | not null, index | 参数指纹，如 `csi300|SH,SZ|exclude_st|2024-06-03` |
| `as_of` | Date | not null | 重建宇宙的时点 |
| `symbol` | String(16) | not null | |
| `eligible` | Boolean | not null | 是否入选 |
| `exclusion_reason` | String(32) | nullable | `delisted`/`st`/`not_listed`/`suspended`/`no_bars`/`not_in_index` |
| `name_at` | String(64) | nullable | `as_of` 时的名称（便于审计 ST 判定） |
| `status_at` | String(16) | nullable | `as_of` 时的状态 |
| `created_at` | DateTime | default utcnow | |

约束：`UniqueConstraint("pool_key","as_of","symbol", name="uq_pool_key_date_sym")`。
注：此表为派生表，可随时由 `security_status`+`security_name`+`index_constituent`+`DailyBar` 覆盖重建；物化是为了回测可复现与审计。

## 3. 数据源与降级

### 3.1 上市/退市历史

| 项 | 主源（AkShare） | 降级 | 说明 |
|---|---|---|---|
| 当前存续列表 | `ak.stock_info_a_code_name`（已用，`akshare_provider.py:177-185`） | 本地 `stocks` 表 | 当前快照，写入 `status=listed/normal` 区间 `valid_to=null` |
| 沪市已退市 | `ak.stock_info_sh_delist` | Tushare `stock_basic`（list_status='D'，需 token） | 写入 `status=delisted`，`valid_from=退市日` |
| 深市已退市 | `ak.stock_info_sz_delist` | Tushare | 同上 |
| 上市日 | AkShare 多数接口不含历史上市日 | Tushare `stock_basic.list_date`；缺失则取该股最早 `DailyBar.trade_date` 作 `valid_from`，`confidence=medium` | |

### 3.2 ST 历史与名称历史

| 项 | 主源 | 降级 | 说明 |
|---|---|---|---|
| 当前 ST/*ST | `ak.stock_zh_a_st_em` | 本地按当前名称前缀推断 | 写入 `status=st/st_star` 区间，`valid_from=今日`，`announced_at` 多缺失 → `confidence=medium` |
| 历史 ST 时段 | AkShare **无**直接历史 ST 接口 | (1) Tushare `namechange`：名称变更记录可还原 ST 摘帽时点；(2) 部分日线接口返回当时名称，可按名称前缀重建；(3) 无 token 时：仅保留"当前 ST + 摘帽日未知"近似，并在 `confidence=low` 标记，回测时默认**不**剔除（保守=纳入），由 `BacktestRequest.pit_st_policy` 控制 | **关键降级**：历史 ST 是本方案最难的数据点 |
| 名称变更历史 | Tushare `namechange` | AkShare 无；缺失则用当前名称单段 `valid_from=earliest_bar`，`confidence=medium` | 名称变迁同时驱动 ST 前缀判定 |

降级原则：**当历史 ST 不可得时，回测默认"纳入疑似 ST"而非"剔除"**。理由：误纳入一只 ST 的损失远小于系统性剔除一批历史曾 ST 但当时正常的股票所造成的样本偏差。该策略在 API 层可显式覆盖。

### 3.3 指数成分与权重

| 项 | 主源 | 降级 | 说明 |
|---|---|---|---|
| 当前成分 | `ak.index_stock_cons_csindex`(沪深300/中证500/1000) / `ak.index_stock_cons`(Sina 通用) | `ak.index_stock_cons_sina`/`ak.index_stock_cons_sw` | 写入 `index_constituent` 区间 `valid_from=今日` |
| 历史成分 | AkShare **无**历史成分接口 | (1) csindex.com.cn 官网历史成分公告（CSV 下载，半年度）；(2) Tushare `index_weight`/`index_member`；(3) 无源时：从今日起前向累计快照，历史段用"今日成分 + 已知调仓日"近似，`confidence=medium` | **从今日起每半年度落快照**，逐步积累真实历史 |
| 权重 | `ak.index_stock_cons_weight_csindex`（当前） | Tushare `index_weight`（历史日度）；csindex 月度文件 | 写入 `index_weight_snapshot` |

### 3.4 数据可用性时点（announced_at）原则

- 上市日：`announced_at = list_date`（IPO 上市即生效）。
- 退市：`announced_at = 退市公告日`（通常提前 15 交易日），未知则 `= valid_from`，`confidence=medium`。
- ST：交易所 ST 公告日通常为年报披露后次日；未知则 `= valid_from`，`confidence=medium`。
- 指数调仓：CSI 提前公布调仓名单，`announced_at = 公告日`，`valid_from = 生效日`；二者不同正是 PIT 价值所在（生效日前成分不变）。

所有 `announced_at` 缺失的行必须带 `confidence <= medium`，并在 PIT 查询日志中可统计占比，用于报告可信度。

## 4. 同步作业

复用现有 `SyncJob`（`entities.py:106-117`）与 `create_sync_job`（`repository.py:715-736`）。新增 job_type：

| job_type | 频率 | 范围 | 产物 |
|---|---|---|---|
| `security_status_current` | 每日盘后 | 全市场当前 ST + 当前存续 | 刷新 `security_status` 当前列 |
| `security_delist` | 每周 | 沪/深退市列表 | 补 `delisted` 区间 |
| `security_names` | 每日 | 名称变更（Tushare 若可用） | 补 `security_name` 区间 |
| `index_constituents` | 每半年度 + 手动触发 | 白名单指数 | 补 `index_constituent` 区间 |
| `index_weights` | 每月 | 白名单指数 | 补 `index_weight_snapshot` |
| `security_status_backfill` | 一次性 | 历史 ST/上市日回填 | 一次性回填任务 |

实现沿用 `full_market.py` 的编排模式（Protocol + Coordinator + 每源熔断 + `sync_jobs` 记录），新增 `backend/app/data/pit_sync.py`。该文件属 GLM 模块 A，不依赖 FastAPI，便于测试。

## 5. API 契约（新增，不改动现有端点）

所有新增端点挂在 `backend/app/api/routes/data.py` 之下，或新建 `backend/app/api/routes/pit.py`（GLM owner）。Schema 新增到 `backend/app/schemas/pit.py`（新文件，不动 `schemas/data.py`）。

### 5.1 同步类

```
POST /api/data/sync/security-status        # body: { exchanges?: ["SH","SZ","BJ"] }
  -> { synced, st_count, delist_count, source }
POST /api/data/sync/security-names         # body: { symbols?: [...] }  // 省略=全量
  -> { synced, source }
POST /api/data/sync/index-constituents     # body: { index_symbol, as_of?, backfill?: bool }
  -> { index_symbol, constituents, weights_synced, source }
```

### 5.2 查询类（点时）

```
GET /api/data/security-status?symbol=000001&as_of=2022-06-01
  -> { symbol, status, valid_from, valid_to, announced_at, confidence, name_at }
GET /api/data/security-name?symbol=000001&as_of=2022-06-01
  -> { symbol, name, valid_from, valid_to }
GET /api/data/index-constituents?index_symbol=000300&as_of=2022-06-01&with_weights=true
  -> { index_symbol, as_of, constituents: [{ symbol, weight?, name_at? }], source, confidence }
GET /api/data/research-pool?as_of=2022-06-01&exchanges=SH,SZ&exclude_st=true&index=000300&limit=300
  -> { pool_key, as_of, members: [{ symbol, eligible, exclusion_reason?, name_at, status_at }] }
```

### 5.3 回测接入（共享文件，由 Codex 实施）

`BacktestRequest`（`schemas/backtest.py`，Codex owner）新增**可选**字段，默认保持现有行为：

```
point_in_time: bool = false        // false=沿用 select_research_symbols（现状）；true=PIT 重建
universe_as_of_strategy: str = "rolling"  // "rolling"=每个调仓日按当日重建；"fixed"=按 start_date 一次
pit_st_policy: str = "exclude_known"      // "exclude_known"|"include_unknown"|"strict"
```

`backtest.py:25-65`（Codex owner）在 `point_in_time=true` 时改调 GLM 提供的新仓储方法 `select_research_symbols_pit(...)`。GLM 只负责提供该方法，不改路由。

## 6. 迁移兼容

1. **零破坏新增**：5 张新表全部 `CREATE TABLE`，不动 `stocks`/`daily_bars`/`index_daily_bars`/`trading_calendar` 列。SQLite 用 `Base.metadata.create_all`（或 Alembic 迁移脚本，由 Codex 决定）。
2. **`Stock` 保留**：作为当前快照继续服务 `/api/data/stocks`、UI 搜索、`symbol_data_status`。新表与 `Stock` 并存。
3. **`list_date` 字符串不动**：新表用 `Date`，不迁移老列类型。
4. **现有 `_research_stock_filters`/`select_research_symbols` 不删**：保持现状作为 `point_in_time=false` 路径，保证既有回测结果可复现。新增 `select_research_symbols_pit` 并行存在。
5. **白名单扩展**：`INDEX_SYMBOL_WHITELIST`（`symbols.py:3`，GLM owner 的 data 模块）可扩展支持更多指数，但不删现有 4 个。
6. **回退**：若 PIT 数据未同步，`point_in_time=true` 路径在 `security_status` 表为空时回退到现状逻辑并返回 `pit_degraded=true` 标志，不抛错。

## 7. 测试验收

新增 `backend/tests/test_point_in_time.py`（GLM owner）。

### 7.1 状态/名称 PIT 单元测试

- 已退市股 X（2022 退市）：`as_of=2020` → `status=listed/normal`；`as_of=2023` → `status=delisted`。
- ST 股 Y（2023-06 起 ST）：`as_of=2022` 名称不含 ST；`as_of=2024` 名称含 ST 前缀。
- `announced_at` 过滤：退市公告日 2022-05-20、生效日 2022-06-01 → `as_of=2022-05-25` 仍判 `listed`（未生效且未公告完毕），`as_of=2022-06-02` 判 `delisted`。

### 7.2 幸存者偏差回归测试

- 构造含 1 只退市股 + 3 只存续股的内存 DB，PIT 回测 2020 区间 → 选中 4 只；非 PIT（现状）→ 选中 3 只（退市股被剔）。断言 PIT 模式包含退市股。

### 7.3 未来函数回归测试

- 股 Z：2020 正常、2023-06 起 ST、至今未摘帽。PIT 回测 2022 区间 → Z 入选；PIT 回测 2024 区间 → Z 被剔（若 `pit_st_policy=exclude_known`）。

### 7.4 指数成分 PIT 测试

- 沪深300 `as_of=2020-06-01` 成员集合 ≠ `as_of=2024-06-01` 成员集合（用 synthetic 调仓数据断言差异）。
- 权重前向填充：`as_of` 在两次快照之间时取前一次。

### 7.5 集成与可复现性

- 同一 `pool_key`+`as_of` 两次重建 `research_pool_member` 结果一致。
- `point_in_time=true` 回测的 `selected_symbols` 落库可审计。
- 运行 `D:\anaconda3\python.exe -m pytest backend\tests` 全量通过（现有 330 测试不回归）。
- 前端 `npm run build` 通过（前端改动属 Phase 4，本轮不强制）。

## 8. 分阶段任务

| 阶段 | 任务 | Owner | 共享文件? |
|---|---|---|---|
| **P1 模块 A** | 新增 5 张表 entities + PIT 仓储查询方法 + AkShare 状态/名称/成分 provider + `pit_sync.py` 编排 + sync 路由 + PIT 查询路由 + schema + 单元测试 | **GLM** | 否（全为新文件 + `repository.py` 仅新增方法） |
| P2 | `BacktestRequest` 加 `point_in_time` 等字段；`backtest.py` 路由在 PIT 模式调 `select_research_symbols_pit`；引擎按调仓日重建宇宙 | Codex | 是（`schemas/backtest.py`、`backtest.py`、可能 `engine.py`） |
| P3 | 指数历史成分回填（csindex 官网/Tushare）；权重快照月度作业 | GLM | 否 |
| P4 | `research_pool_member` 物化 + 诊断 API + 前端数据中心 PIT 面板 | GLM(后端)+Codex(前端) | 否 |
| P5 | 数据齐全后把 `point_in_time` 默认翻为 `true`；更新 `architecture.md`/`roadmap.md` 表清单 | Codex | 文档 |
| P6 | 用 PIT 全市场重跑 21 因子与 AI 候选，冻结 walk-forward | DeepSeek | 否 |

## 9. 明确由 GLM 实现的模块 A

**模块 A = 点时数据底座（Point-in-Time Data Foundation）**，对应上表 P1 + P3（后端）。边界：

GLM **新建并独占**（不改共享签名）：

- `backend/app/models/pit.py` —— 5 张新表的 SQLAlchemy 模型（或在 `entities.py` 追加，但 `entities.py` 当前被多代理修改，建议独立文件并在 `models/__init__.py` 导出，`__init__.py` 的追加由 GLM 负责）。
- `backend/app/data/pit_sync.py` —— 状态/名称/成分同步编排（仿 `full_market.py` 的 Protocol+Coordinator）。
- `backend/app/data/akshare_pit_provider.py` —— AkShare 的 ST/退市/成分/权重抓取与列规范化。
- `backend/app/data/pit_repository.py` —— PIT 查询方法（`status_as_of`、`name_as_of`、`index_constituents_as_of`、`select_research_symbols_pit`、`materialize_research_pool`）。**或将这些方法追加到 `repository.py`**；因 `repository.py` 是共享单 owner 文件，GLM 实施 P1 前须与 Codex 确认：优先方案是新建 `pit_repository.py`，由 `MarketDataRepository` 组合委托，避免并发覆盖。
- `backend/app/api/routes/pit.py` —— sync + 查询路由。
- `backend/app/schemas/pit.py` —— Pydantic 模型。
- `backend/tests/test_point_in_time.py` —— 全套 PIT 测试。

GLM **不碰**（交给对应 owner）：

- `backend/app/backtest/engine.py`（Codex）—— 引擎宇宙重建接入由 Codex 在 P2 做。
- `backend/app/strategy/base.py`（共享）—— 策略接口不变。
- `backend/app/schemas/backtest.py`（共享）—— `point_in_time` 字段由 Codex 加。
- `backend/app/api/routes/backtest.py`（Codex）—— 路由分支由 Codex 加。
- 前端 PIT 面板（Codex/前端 owner）。

GLM 对外契约（供 Codex P2 调用）：

```python
# backend/app/data/pit_repository.py
def select_research_symbols_pit(
    self,
    as_of: date,
    start_date: date,
    end_date: date,
    *,
    exchanges: tuple[str, ...] = ("SH", "SZ", "BJ"),
    exclude_st: bool = True,
    index_symbol: str | None = None,
    min_trading_days: int | None = None,
    min_coverage_ratio: float = 0.8,
    limit: int = 300,
    st_policy: str = "exclude_known",
) -> tuple[list[str], dict]  # (symbols, meta{pit_degraded, pool_key, counts})
```

返回 `meta` 让路由层可在响应中标注 `pit_degraded`，前端据此提示"历史 ST 不可得，已按保守策略纳入"。

## 10. 风险与边界

- **历史 ST 数据缺口**是最大风险。降级为"保守纳入 + confidence 标记"，绝不在数据缺失时默认剔除（否则重新引入幸存者偏差）。
- **指数历史成分**AkShare 无接口；依赖 csindex 官网或 Tushare。若无任何历史源，P3 阶段先积累"今日向前"的快照，历史段显式标 `confidence=medium` 且不默认用于成分股策略回测。
- **announced_at 缺失**普遍存在；PIT 查询回退 `valid_from` 时必须降 `confidence`，并在诊断 API 输出占比。
- 本方案不改引擎/因子内核，P2 接入前 PIT 数据可独立同步与验证，不阻塞现有回测。
- 不与 DeepSeek 的 `deepseek-qlib-rdagent-plan`、`deepseek-research-pool-fix` 任务冲突：GLM 提供数据底座，DeepSeek 在 P6 消费。

## 11. 验收里程碑

- M1（P1 完成）：5 张表建立；`/api/data/security-status?symbol=&as_of=` 返回正确历史状态；`test_point_in_time.py` 全绿；现有 330 测试不回归。
- M2（P2 完成）：`point_in_time=true` 回测可用，退市股出现在历史宇宙中。
- M3（P3 完成）：沪深300/中证500/1000 历史成分可按 `as_of` 查询。
- M4（P4 完成）：研究池物化 + 前端可见 PIT 诊断。
- M5（P5 完成）：默认 PIT，文档更新。

在 M2 之前，不把任何 AI 候选或 PIT 回测结果标记为"可实盘"（与 `docs/factor-lab-and-ai-research-handoff.md:135` 一致）。
