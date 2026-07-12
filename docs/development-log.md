# 开发日志

## 2026-05-11

初始化新项目目录：`D:\CursorProjects\cn-stock-quant`。

本轮完成：

- 创建后端 FastAPI 项目骨架
- 创建前端 React + TypeScript 项目骨架
- 建立 SQLite 数据模型
- 实现 AkShare 数据适配层
- 实现股票列表和日线同步 API
- 实现双均线策略
- 实现日频回测引擎雏形
- 实现数据中心、策略管理、回测中心、模拟盘、风控设置页面
- 编写架构设计和开发路线文档

重要决策：

- 不使用原有 `D:\CursorProjects\a-share-quant` 目录。
- 第一阶段使用 SQLite，降低本地部署成本。
- 策略输出目标仓位，订单和成交由回测或交易模块处理。
- 实盘交易暂不自动下单，先保留 broker 抽象边界。

下一步：

- 安装依赖并运行后端测试
- 修正真实运行时问题
- 接入净值曲线图
- 做回测结果持久化

## 2026-05-11 第二轮

继续推进 v0.1 可运行闭环。

本轮完成：

- 回测结果保存到数据库
- 新增历史回测列表 API
- 新增单次回测详情 API
- 新增净值曲线和回撤曲线前端组件
- 前端回测页展示历史回测
- 新增风控引擎骨架
- 新增交易计划生成骨架
- 新增券商 broker 抽象接口
- 新增风控和交易计划测试

待验证：

- 安装后端依赖后运行完整测试
- 安装前端依赖后运行 TypeScript 构建
- 真实 AkShare 同步测试

运行端口：

- 后端：`8010`
- 前端：`5174`

说明：本机 `8000` 端口已有其他进程占用，因此本项目避开该端口。

验证结果：

- 后端依赖安装完成
- 前端依赖安装完成
- `python -m pytest backend\tests` 通过，3 个测试全部通过
- `npm run build` 通过
- 后端健康接口 `http://127.0.0.1:8010/health` 正常
- 前端 `http://127.0.0.1:5174` 正常
- AkShare 股票列表同步成功，写入 5515 只股票
- 000001 在 2024 年日线同步成功，写入 242 条
- 000001 双均线回测运行成功，保存为 `run_id=1`

注意：

- npm audit 当前提示 2 个中等风险依赖问题，暂未使用 `npm audit fix --force`，避免引入破坏性升级。
- 前端生产构建提示 chunk 大小超过 500 kB，主要来自 Ant Design 和 ECharts，后续做路由和图表懒加载优化。

## 2026-05-11 第三轮

修复日线同步失败并启动 v0.2 数据中心增强。

问题原因：

- 用户可能输入 `000001.SZ`、`sz000001` 等带市场前后缀的代码，AkShare 日线接口需要 6 位代码。
- AkShare 日线接口偶发远端断开连接，之前会直接表现为同步失败。
- 前端只显示“同步日线失败”，没有展示后端返回的真实原因。

修复：

- 新增 A 股代码规范化，统一转成 6 位代码。
- 日线同步失败时返回明确错误。
- 前端显示后端具体错误信息。
- AkShare 临时失败但本地已有该区间数据时，返回 `cached`，避免重复同步误报失败。

v0.2 已完成：

- 批量日线同步 API
- 同步日志表 `sync_jobs`
- 同步日志查询 API
- 日线数据覆盖状态 API
- 前端数据中心增加批量同步、数据状态、同步日志

验证：

- `000001.SZ` 可规范化为 `000001` 并同步。
- 批量同步 `000001.SZ, 600000, BAD001` 返回：2 个可用缓存、1 个非法代码失败。
- `python -m pytest backend\tests` 通过，4 个测试全部通过。
- `npm run build` 通过。

## 2026-06-17 策略框架与后续规划

本轮目标：

- 将策略模块从单个示例策略升级为可扩展策略体系。
- 基于 A 股日频约束，确定下一步优先做可信回测底座和完整版动量策略。

已完成：

- 策略基类增加参数元信息、策略说明和来源字段。
- 策略注册表支持内置策略和 `strategies/` 用户策略目录加载。
- 前端回测页根据策略元信息动态生成参数表单。
- 新增内置策略：动量排序、均值回归。
- 升级动量排序策略，增加跳过近期、流动性过滤、单票上限和组合仓位参数。
- README 增加本机稳定启动方式，明确使用 `D:\anaconda3\python.exe` 启动后端。

协作安排：

- 主线负责策略、风控、文档和最终集成。
- subagent 负责最小 A 股回测规则增强，仅修改 `backend/app/backtest/engine.py` 和相关回测测试。

下一步：

- 集成回测调仓日、信号延迟、T+1、涨跌停和停牌规则。
- 将风控引擎接入回测执行前的目标仓位处理。
- 完整验证动量策略多股票组合回测。

## 2026-06-17 风控接入回测

本轮完成：

- `BacktestConfig` 增加独立风控参数：
  - `risk_max_symbol_weight`
  - `risk_max_total_weight`
  - `risk_max_positions`
- `/api/backtests/run` 支持传入风控参数。
- 回测引擎在策略生成目标仓位后调用 `RiskEngine.evaluate()`，再将裁剪后的目标仓位交给撮合逻辑。
- 前端回测页增加风控单票、风控总仓、最大持仓数输入。
- 新增测试覆盖：策略返回三只超配股票时，风控会限制持仓数量、单票仓位和组合总仓位。

验证：

- `D:\anaconda3\python.exe -m pytest tests` 通过，16 个测试全部通过。
- `npm run build` 通过。
## 2026-06-18 Phase 1 closeout

Completed:
- Added benchmark support to new backtest runs through `benchmark_symbol`, `benchmark_curve`, `benchmark_return`, and `excess_return`.
- Updated the equity chart to show strategy equity, benchmark equity, and drawdown.
- Added regression coverage for benchmark/excess return metrics.
- Added `docs/phase1-handoff.md` for agent handoff.

Validation:
- `D:\anaconda3\python.exe -m pytest tests`: 17 passed, with non-blocking `.pytest_cache` permission warnings.
- `npm run build`: passed, with the existing Vite large chunk warning.

## 2026-06-19 赚钱优先路线与消息面规划

本轮新增后续主交接文档：

- `docs/profit-first-roadmap-with-sentiment.md`

文档明确后续重点：

- 先扩充研究池和交易日历，保证策略验证可信。
- 增加低波红利、小盘动量、ETF 轮动等赚钱导向策略池。
- 新增消息面/市场情绪引擎，把新闻、公告、股吧、研报等数据结构化为可回测因子。
- 规划 `NewsProvider`、`SentimentAnalyzer`、`SentimentFactorEngine` 三层接口。
- 后续三 Agent 协作时以该文档为主线，Codex 负责接口/测试/集成，GLM 负责数据和因子，DeepSeek 负责策略和情绪策略优化。

## 2026-06-20 因子实验室与 AI 研究闭环

三方协作：

- OpenCode GLM 5.2：实现 OHLCV 因子实验室。
- Claude Code DeepSeek V4 Pro：实现波动收缩突破和趋势过滤反转策略。
- Codex：完成指数隔离、共享接口、API、互审修复、真实实验和最终集成。

完成：

- 新增独立指数日线表，避免指数覆盖同代码股票。
- 新增 21 个 OHLCV 因子及 IC/RankIC/分组收益/换手率评估。
- 新增因子实验 API。
- 新增两个高级 OHLCV 策略。
- 新增受控 AI 因子候选协议和 Qlib 格式适配器。
- GLM、DeepSeek 分别提出候选组合，并由统一接口完成评估。

详细交接：

- `docs/factor-lab-and-ai-research-handoff.md`

## 2026-06-20 全 A 股与市场状态判断

完成：

- 交易日历表与真实日历同步。
- SH/SZ/BJ 全市场分块同步。
- 北交所 `920xxx` 代码识别。
- 跨请求持久化失败熔断。
- 全市场数据质量报告。
- 可恢复后台同步器。
- 市场状态分析与受控 LLM 上下文。
- 数据中心全市场同步面板。

验证：

- 330 个后端测试通过。
- 前端构建通过。
- 交易日历真实同步 8,797 天。
- 当前股票列表 5,515 只。
- 真实首批 20 只全市场同步全部成功。

详细交接：

- `docs/full-market-data-and-llm-regime-handoff.md`

## 2026-07-07 新闻文本质量与基准回测修复

完成：

- 回测基准数据自动同步增强：当 `000300/000905/000852` 本地已有数据但覆盖不足时，回测会自动重新同步请求区间并重新验证覆盖。
- 修复用户遇到的 `Benchmark 000300 does not cover strategy trading dates 2024-07-03 through 2026-07-02`。
- 本地已补齐 `000300` 至 `2026-07-02`，同区间回测 API 验证通过。
- 新增新闻文本清洗模块，修复常见 UTF-8 被 Latin-1/Windows-1252 误解码的乱码。
- 新闻 provider、repository 写入、repository 读取都接入文本清洗。
- 新增 `backend/repair_news_text.py`，用于扫描/修复历史新闻文本。

验证：

- `pytest backend\tests -q`：531 passed。
- `python backend\repair_news_text.py --dry-run`：`scanned=10`，`updated=0`，`remaining_suspect=0`。
- 直接 Python 读取 `data/quant.db` 新闻标题为正常中文；此前控制台看到的 `å...` 多数是 PowerShell 输出编码问题。

## 2026-07-12 批量新闻同步与覆盖报告

完成：

- 新增 `backend/sync_news.py` 批量新闻同步 CLI。
- 支持 `manual` 和 `research_pool` 两种股票来源。
- 支持 `--start-date/--end-date`、`--batch-size`、`--pool-max-symbols`、`--min-request-interval`、`--dry-run`。
- 每只股票输出同步状态、新闻条数、风险新闻条数、发布时间范围、来源列表和错误信息。
- 输出 JSON 和 Markdown 覆盖报告，写入 `backend/artifacts/news/`。

验证：

- `pytest backend\tests\test_sync_news_runner.py backend\tests\test_news_text.py backend\tests\test_data_routes.py::test_sync_news_accepts_stock_name_and_list_news_filters_by_name -q`：7 passed。
- 研究池 dry-run：20 只股票被选出，不触发网络。
- 单股 live sync：`002156` 在 `2026-06-01` 至 `2026-07-03` 同步成功，返回 2 条新闻。

后续：

- 用 `sync_news.py` 对研究池先跑 100 只，再扩到 300 只。
- 生成覆盖报告后重跑 `compare_ml_news_filter.py`，比较 price-only 与 news-risk-filter。
