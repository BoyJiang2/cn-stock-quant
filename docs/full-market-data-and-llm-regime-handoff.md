# 全 A 股数据与大模型市场判断交接

更新日期：2026-06-20

## 本轮协作

- OpenCode GLM 5.2：全市场分块同步编排器。
- Claude Code DeepSeek V4 Pro：确定性市场状态分析器和 LLM 上下文。
- Codex：交易日历、仓储、API、持久化熔断、质量报告、前端、真实同步和集成测试。

## 全市场数据能力

### 范围

当前股票列表共 5,515 只，包含：

- 上海证券交易所
- 深圳证券交易所
- 北京证券交易所
- 北交所新 `920xxx` 代码段

注意：当前“全 A 股”指 AkShare 当前股票列表中的现存证券，不是历史任意日期的完整上市/退市证券集合。历史回测仍存在幸存者偏差。

### 交易日历

新增 `TradingCalendar` 表。

真实同步结果：

- 8,797 个交易日
- 1990-12-19 至 2026-12-31

接口：

```text
POST /api/data/sync/calendar
```

### 全市场同步

接口：

```text
GET  /api/data/sync/full-market/progress
POST /api/data/sync/full-market/next
GET  /api/data/quality
```

设计：

- 单批最多 50 只，不执行单 HTTP 请求同步 5,000 只。
- 默认覆盖 SH/SZ/BJ。
- 股票间主动限流。
- `success`、`empty`、`failed`、`skipped` 分开统计。
- 连续失败次数从 `sync_jobs` 恢复，跨 HTTP 请求和进程重启有效。
- 熔断时返回 `blocked=true`，不会伪装成 `completed=true`。
- 可使用 `retry_failed=true` 显式重试熔断证券。

### 后台同步器

文件：

```text
backend/sync_full_market.py
```

示例：

```powershell
cd D:\CursorProjects\cn-stock-quant\backend
python sync_full_market.py `
  --start-date 2024-01-02 `
  --end-date 2025-12-31 `
  --batch-size 20 `
  --interval 0.35
```

当前后台任务已经启动。

日志：

```text
backend/full-market-sync.out.log
backend/full-market-sync.err.log
```

首批真实结果：

- 20/20 成功
- 0 失败
- 0 空数据
- 覆盖从 33 增加到 53，之后继续推进

### 数据质量报告

质量报告按交易日历计算，不使用自然日。

输出：

- 预期交易日数量
- 检查证券数量
- 完整覆盖数量
- 有缺口证券数量
- 缺失记录合计
- 小批量检查时的具体缺失日期

对 5,000+ 证券的大范围检查使用 SQL 聚合，不一次加载数百万日期记录。

缺失记录可能来自：

- 停牌
- 上市前
- 退市后
- 数据源缺失

在 point-in-time 证券状态完成前，质量报告不会把所有缺失都直接判定为数据错误。

## 市场状态与大模型入口

文件：

```text
backend/app/ai_research/market_regime.py
```

确定性输出：

- `BULL`
- `BEAR`
- `SIDEWAYS`
- `PANIC`
- `EUPHORIA`

输入：

- 指数 MA20/MA60/MA120
- 指数 20 日波动率
- 指数 120 日回撤
- 全市场股票位于 MA20/MA60 上方的横截面比例

输出：

- regime
- confidence
- trend_score
- breadth_score
- volatility_score
- drawdown
- reasons

接口：

```text
POST /api/ai-research/market-regime
```

安全边界：

- 市场状态计算不调用 LLM。
- `build_llm_market_context` 只生成结构化上下文。
- 返回 `can_trade_directly=false`。
- Prompt 明确禁止生成订单或绕过量化验证。
- 后续 DeepSeek、GLM、FinGPT 只能解释风险、提出候选或生成情绪因子。

当前沪深300真实同步仍受本机代理影响，市场状态 API 需要先有本地指数数据。

## 前端

数据中心新增：

- 同步交易日历
- 同步全市场下一批
- 自动同步全市场
- 全市场进度
- 质量报告
- 熔断提示

当前开发地址：

```text
前端 http://127.0.0.1:5175
后端 http://127.0.0.1:8013
```

## 验证

- 后端：330 tests passed
- 前端：`npm run build` passed
- 真实交易日历同步成功
- 真实全市场 5 股票 API 批次成功
- 后台首个 20 股票批次全部成功

## 下一阶段

1. 让后台同步完成 5,515 只当前 A 股。
2. 增加证券上市日期、退市日期、历史 ST 状态。
3. 建立 point-in-time 股票池，降低幸存者偏差。
4. 同步沪深300、中证500、中证1000指数及历史成分。
5. 用全市场数据重跑 21 因子和 AI 候选。
6. 接入 Qlib LightGBM 滚动训练。
7. 接入 RD-Agent，但只允许提交受控因子/模型候选。
8. 接入新闻抓取和 FinGPT/中文 LLM 情绪因子。

在 point-in-time 数据和 walk-forward 验证完成前，不把任何 AI 判断直接用于实盘。
