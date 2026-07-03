import { Button, Tag } from "antd";
import { Activity, ArrowRight, BarChart3, Database, FlaskConical, Newspaper, ShieldCheck, WalletCards } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import type { BacktestRun, DataDiagnostics, StrategyItem } from "../types";

type HomeTarget = "data" | "kline" | "strategies" | "backtests" | "portfolio" | "risk";

interface HomePageProps {
  onNavigate: (target: HomeTarget) => void;
}

interface NewsItem {
  symbol: string;
  title: string;
  source: string;
  sentiment_label: string | null;
  sentiment_score: number | null;
  published_at: string;
}

const workflowItems = [
  { key: "data", label: "数据中心", icon: Database },
  { key: "strategies", label: "策略管理", icon: FlaskConical },
  { key: "backtests", label: "回测中心", icon: BarChart3 },
  { key: "portfolio", label: "模拟盘", icon: WalletCards }
] as const;

const researchArtifacts = [
  {
    title: "2026 ML 预测",
    meta: "wf-lgbm-fwd5-2026-v45-embargo15-predictions.csv"
  },
  {
    title: "新闻风控对比",
    meta: "ml-news-filter-research-pool-2026.md"
  },
  {
    title: "静态 LightGBM",
    meta: "lgbm-fwd5-static-2026-predictions.csv"
  }
];

function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return value.toLocaleString();
}

function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return `${(value * 100).toFixed(2)}%`;
}

function formatRange(start: string | null | undefined, end: string | null | undefined): string {
  if (!start || !end) return "暂无";
  return `${start} ~ ${end}`;
}

export function HomePage({ onNavigate }: HomePageProps) {
  const [diagnostics, setDiagnostics] = useState<DataDiagnostics | null>(null);
  const [strategies, setStrategies] = useState<StrategyItem[]>([]);
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [news, setNews] = useState<NewsItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    async function loadOverview() {
      setLoading(true);
      const [diagnosticsResult, strategiesResult, runsResult, newsResult] = await Promise.allSettled([
        api.get<DataDiagnostics>("/api/data/diagnostics"),
        api.get<StrategyItem[]>("/api/strategies"),
        api.get<BacktestRun[]>("/api/backtests"),
        api.get<NewsItem[]>("/api/data/news", { params: { limit: 50 } })
      ]);
      if (!mounted) return;
      if (diagnosticsResult.status === "fulfilled") setDiagnostics(diagnosticsResult.value.data);
      if (strategiesResult.status === "fulfilled") setStrategies(strategiesResult.value.data);
      if (runsResult.status === "fulfilled") setRuns(runsResult.value.data);
      if (newsResult.status === "fulfilled") setNews(newsResult.value.data);
      setLoading(false);
    }
    loadOverview().catch(() => setLoading(false));
    return () => {
      mounted = false;
    };
  }, []);

  const latestRun = runs[0];
  const mlStrategies = useMemo(
    () => strategies.filter((item) => item.name.toLowerCase().includes("ml") || item.name.toLowerCase().includes("score")),
    [strategies]
  );
  const negativeNewsCount = useMemo(
    () => news.filter((item) => item.sentiment_label === "negative" || (item.sentiment_score ?? 0) < -0.2).length,
    [news]
  );

  return (
    <section className="page homePage">
      <header className="homeHeader">
        <div>
          <div className="homeEyebrow">AI QUANT WORKBENCH</div>
          <h1>A 股量化研究台</h1>
          <div className="homeSubline">本地行情：{formatRange(diagnostics?.start_date, diagnostics?.end_date)}</div>
        </div>
        <div className="homeHeaderActions">
          <Button icon={<Database size={16} />} onClick={() => onNavigate("data")}>
            数据同步
          </Button>
          <Button icon={<BarChart3 size={16} />} type="primary" onClick={() => onNavigate("backtests")}>
            跑回测
          </Button>
        </div>
      </header>

      <div className="homeKpiGrid" aria-busy={loading}>
        <div className="homeKpi">
          <span>股票覆盖</span>
          <strong>{formatNumber(diagnostics?.stock_count)}</strong>
          <small>{formatNumber(diagnostics?.symbols_with_bars)} 只有行情</small>
        </div>
        <div className="homeKpi">
          <span>日线样本</span>
          <strong>{formatNumber(diagnostics?.bar_count)}</strong>
          <small>{diagnostics?.database ?? "sqlite://quant.db"}</small>
        </div>
        <div className="homeKpi">
          <span>策略库</span>
          <strong>{formatNumber(strategies.length)}</strong>
          <small>{formatNumber(mlStrategies.length)} 个 ML / Score 策略</small>
        </div>
        <div className="homeKpi">
          <span>消息面样本</span>
          <strong>{formatNumber(news.length)}</strong>
          <small>{formatNumber(negativeNewsCount)} 条风险新闻</small>
        </div>
      </div>

      <div className="homeCommandBar">
        {workflowItems.map((item) => {
          const Icon = item.icon;
          return (
            <button key={item.key} className="homeCommand" type="button" onClick={() => onNavigate(item.key)}>
              <Icon size={17} />
              <span>{item.label}</span>
              <ArrowRight size={15} />
            </button>
          );
        })}
      </div>

      <div className="homeGrid">
        <section className="homePanel">
          <div className="homePanelHeader">
            <div>
              <div className="homePanelTitle">研究状态</div>
              <div className="homePanelSub">数据、模型、风控链路</div>
            </div>
            <Tag color={diagnostics?.bar_count ? "green" : "default"}>LIVE</Tag>
          </div>
          <div className="statusList">
            <div className="statusRow">
              <Database size={17} />
              <span>全市场行情</span>
              <Tag color={(diagnostics?.stock_count ?? 0) > 5000 ? "green" : "gold"}>{formatNumber(diagnostics?.stock_count)}</Tag>
            </div>
            <div className="statusRow">
              <FlaskConical size={17} />
              <span>因子与 ML 策略</span>
              <Tag color={mlStrategies.length ? "green" : "default"}>{formatNumber(mlStrategies.length)}</Tag>
            </div>
            <div className="statusRow">
              <Newspaper size={17} />
              <span>新闻情绪过滤</span>
              <Tag color={news.length ? "green" : "gold"}>{news.length ? "已接入" : "待补样本"}</Tag>
            </div>
            <div className="statusRow">
              <ShieldCheck size={17} />
              <span>仓位与单票上限</span>
              <Tag color="green">已启用</Tag>
            </div>
          </div>
        </section>

        <section className="homePanel">
          <div className="homePanelHeader">
            <div>
              <div className="homePanelTitle">最近回测</div>
              <div className="homePanelSub">{latestRun ? `${latestRun.strategy_name} · ${latestRun.start_date} ~ ${latestRun.end_date}` : "暂无记录"}</div>
            </div>
            <Button size="small" onClick={() => onNavigate("backtests")}>
              查看
            </Button>
          </div>
          <div className="runSummary">
            <div>
              <span>累计收益</span>
              <strong className={(latestRun?.total_return ?? 0) >= 0 ? "goodText" : "badText"}>{formatPercent(latestRun?.total_return)}</strong>
            </div>
            <div>
              <span>最大回撤</span>
              <strong className="badText">{formatPercent(latestRun?.max_drawdown)}</strong>
            </div>
            <div>
              <span>夏普</span>
              <strong>{latestRun ? latestRun.sharpe.toFixed(2) : "-"}</strong>
            </div>
          </div>
        </section>

        <section className="homePanel">
          <div className="homePanelHeader">
            <div>
              <div className="homePanelTitle">研究产物</div>
              <div className="homePanelSub">最近可复用的模型与报告</div>
            </div>
            <Activity size={18} />
          </div>
          <div className="artifactList">
            {researchArtifacts.map((item) => (
              <div className="artifactItem" key={item.meta}>
                <span>{item.title}</span>
                <small>{item.meta}</small>
              </div>
            ))}
          </div>
        </section>
      </div>
    </section>
  );
}
