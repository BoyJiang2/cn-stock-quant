# cn-stock-quant

个人使用的 A 股日频量化研究、回测、模拟盘和后续实盘扩展平台。

当前版本目标是先跑通最小闭环：

1. 使用 AkShare 同步 A 股基础数据和日线行情
2. 用统一策略接口生成目标仓位
3. 运行日频回测
4. 输出核心绩效指标、收益曲线和交易记录
5. 前端提供数据中心、策略管理、回测中心、模拟盘和交易计划入口

## 目录

```text
backend/      FastAPI 后端
frontend/     React + TypeScript 前端
strategies/   用户策略目录
docs/         设计文档和开发计划
data/         本地数据库和缓存数据
```

## 后端启动

```powershell
cd D:\CursorProjects\cn-stock-quant\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8010
```

访问：

- API: http://127.0.0.1:8010
- Swagger: http://127.0.0.1:8010/docs

## 前端启动

```powershell
cd D:\CursorProjects\cn-stock-quant\frontend
npm install
npm run dev -- --port 5174
```

访问：

- Web: http://127.0.0.1:5174

## 当前状态

这是 v0.1 工程骨架，重点是让数据、策略、回测、报告的主干先稳定下来。实盘模块只保留接口边界，不直接自动下单。
