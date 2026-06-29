# cn-stock-quant

Phase 1 handoff: see [docs/phase1-handoff.md](docs/phase1-handoff.md).

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

## 快速启动

后端和前端需要分别在两个 PowerShell 窗口启动。

后端：

```powershell
cd D:\CursorProjects\cn-stock-quant\backend
D:\anaconda3\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

前端：

```powershell
cd D:\CursorProjects\cn-stock-quant\frontend
npm run dev -- --host=127.0.0.1 --port=5173
```

启动后访问：

- Web: http://127.0.0.1:5173
- Health: http://127.0.0.1:8010/health
- Swagger: http://127.0.0.1:8010/docs

## 后端启动

本机当前推荐直接使用 Anaconda Python 启动。普通 `python` 可能指向 MSYS2 环境，导致找不到 FastAPI、Uvicorn、AkShare 等依赖。

```powershell
cd D:\CursorProjects\cn-stock-quant\backend
D:\anaconda3\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

看到下面类似输出说明后端已启动：

```text
Uvicorn running on http://127.0.0.1:8010
```

也可以使用仓库里的启动脚本：

```powershell
cd D:\CursorProjects\cn-stock-quant\backend
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_dev_server.ps1
```

如果要使用独立虚拟环境：

```powershell
cd D:\CursorProjects\cn-stock-quant\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

访问：

- API: http://127.0.0.1:8010
- Health: http://127.0.0.1:8010/health
- Swagger: http://127.0.0.1:8010/docs

## 前端启动

```powershell
cd D:\CursorProjects\cn-stock-quant\frontend
npm install
npm run dev -- --host=127.0.0.1 --port=5173
```

访问：

- Web: http://127.0.0.1:5173

如果 Vite 启动时报 `node_modules\.vite` 的 `EPERM`，通常是缓存文件被残留进程占用。关闭已有 Node/Vite 进程后清理缓存再启动：

```powershell
cd D:\CursorProjects\cn-stock-quant\frontend
Remove-Item -Recurse -Force .\node_modules\.vite
npm run dev -- --host=127.0.0.1 --port=5173
```

## 常用验证

```powershell
cd D:\CursorProjects\cn-stock-quant\backend
D:\anaconda3\python.exe -m pytest

cd D:\CursorProjects\cn-stock-quant\frontend
npm run build
```

## 当前状态

这是 v0.1 工程骨架，重点是让数据、策略、回测、报告的主干先稳定下来。实盘模块只保留接口边界，不直接自动下单。
