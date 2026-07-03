import { CandlestickChart, Database, FlaskConical, LayoutDashboard, LineChart, ShieldCheck, WalletCards } from "lucide-react";
import { useState } from "react";

import { BacktestPage } from "./pages/BacktestPage";
import { DataPage } from "./pages/DataPage";
import { HomePage } from "./pages/HomePage";
import { KlinePage } from "./pages/KlinePage";
import { PortfolioPage } from "./pages/PortfolioPage";
import { StrategyPage } from "./pages/StrategyPage";

type PageKey = "home" | "data" | "kline" | "strategies" | "backtests" | "portfolio" | "risk";

const navItems = [
  { key: "home", label: "总览", icon: LayoutDashboard },
  { key: "data", label: "数据中心", icon: Database },
  { key: "kline", label: "K 线查看", icon: CandlestickChart },
  { key: "strategies", label: "策略管理", icon: FlaskConical },
  { key: "backtests", label: "回测中心", icon: LineChart },
  { key: "portfolio", label: "模拟盘", icon: WalletCards },
  { key: "risk", label: "风控设置", icon: ShieldCheck }
] as const;

function App() {
  const [active, setActive] = useState<PageKey>("home");

  return (
    <div className="appShell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brandMark">Q</div>
          <div>
            <div className="brandTitle">CN Stock Quant</div>
            <div className="brandSub">A 股日频量化</div>
          </div>
        </div>
        <nav className="nav">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                className={active === item.key ? "navItem active" : "navItem"}
                key={item.key}
                onClick={() => setActive(item.key)}
                type="button"
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
      </aside>
      <main className="main">
        {active === "home" && <HomePage onNavigate={(target) => setActive(target)} />}
        {active === "data" && <DataPage />}
        {active === "kline" && <KlinePage />}
        {active === "strategies" && <StrategyPage />}
        {active === "backtests" && <BacktestPage />}
        {active === "portfolio" && <PortfolioPage />}
        {active === "risk" && (
          <section className="page">
            <h1>风控设置</h1>
            <div className="panel">
              <div className="panelTitle">预留规则</div>
              <div className="ruleGrid">
                <span>单票最大仓位</span>
                <strong>30%</strong>
                <span>组合最大仓位</span>
                <strong>95%</strong>
                <span>最小交易单位</span>
                <strong>100 股</strong>
                <span>ST 与退市风险</span>
                <strong>默认过滤</strong>
              </div>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}

export default App;
