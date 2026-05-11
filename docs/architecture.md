# 架构设计

## 产品边界

CN Stock Quant 是个人使用的 A 股日频量化平台，第一阶段聚焦研究、回测、模拟盘和交易计划生成。实盘交易只保留接口边界，等回测和模拟盘稳定后再接具体券商。

核心原则：

- 日频优先，不做高频。
- 数据、策略、回测、交易接口解耦。
- 策略输出目标仓位，不直接下单。
- 风控模块独立于策略模块。
- 所有实盘动作默认先生成交易计划，后续再决定是否自动化。

## 技术栈

后端：

- Python
- FastAPI
- SQLAlchemy
- SQLite 起步，后续可迁移 PostgreSQL
- Pandas / NumPy
- AkShare

前端：

- React
- TypeScript
- Vite
- Ant Design
- ECharts
- lucide-react

## 模块划分

```text
backend/app
  api/          HTTP API
  core/         配置、数据库、日志
  data/         数据源适配与数据仓储
  strategy/     策略接口、策略注册
  backtest/     日频回测引擎
  portfolio/    模拟账户和组合管理，后续补充
  risk/         风控规则，后续补充
  broker/       实盘接口抽象，后续补充
  models/       数据库模型
  schemas/      API 入参和出参模型
```

## 数据流

```text
AkShare
  -> 数据适配层
  -> 本地 SQLite
  -> 策略读取历史行情
  -> 策略输出目标仓位
  -> 回测引擎生成订单和成交
  -> 报告指标和交易记录
  -> 前端展示
```

## 策略接口

策略以目标仓位为输出：

```python
{
    "000001": 0.95
}
```

好处：

- 回测、模拟盘和实盘可以共用策略结果。
- 交易规则和风控不用写进策略。
- 后续可以统一做组合层和风控层调整。

当前内置策略：

- `moving_average`：双均线择时策略

## 回测假设

v0.1 已实现：

- 日频收盘价成交
- 手续费
- 卖出印花税
- 滑点
- 100 股整数手
- 目标仓位调仓
- 总收益、年化收益、最大回撤、夏普

后续必须补齐：

- T+1 限制
- 涨停不能买入
- 跌停不能卖出
- 停牌不能交易
- ST 和退市风险过滤
- 基准指数对比
- 回测结果持久化

## 数据库表

当前表：

- `stocks`：股票基础信息
- `daily_bars`：日线行情
- `backtest_runs`：回测摘要
- `backtest_equity`：回测净值曲线
- `trade_records`：交易记录

下一阶段新增：

- `strategy_configs`：策略参数配置
- `mock_accounts`：模拟账户
- `mock_positions`：模拟持仓
- `trade_plans`：交易计划
- `risk_rules`：风控规则
- `sync_jobs`：数据同步任务记录

## API 设计

当前 API：

- `GET /health`
- `POST /api/data/sync/stocks`
- `POST /api/data/sync/daily`
- `GET /api/data/stocks`
- `GET /api/data/daily`
- `GET /api/strategies`
- `POST /api/backtests/run`
- `GET /api/backtests`
- `GET /api/backtests/{id}`

下一阶段 API：

- `POST /api/mock/accounts`
- `POST /api/mock/accounts/{id}/run`
- `GET /api/mock/accounts/{id}/positions`
- `GET /api/trade-plans`
