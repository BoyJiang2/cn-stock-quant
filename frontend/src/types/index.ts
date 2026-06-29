export interface Stock {
  symbol: string;
  name: string;
  exchange: string;
  status: string;
}

export interface DailyStatus {
  symbol: string;
  start_date: string;
  end_date: string;
  bar_count: number;
}

export interface DataDiagnostics {
  stock_count: number;
  bar_count: number;
  symbols_with_bars: number;
  start_date: string | null;
  end_date: string | null;
  database: string;
}

export interface DailySyncResponse {
  symbol: string;
  synced: number;
  status: "success" | "cached" | "empty";
  message?: string;
}

export interface BatchDailySyncItem {
  symbol: string;
  status: string;
  synced: number;
  message: string;
}

export interface BatchDailySyncResponse {
  total: number;
  success: number;
  failed: number;
  items: BatchDailySyncItem[];
}

export interface ResearchSyncProgress {
  total: number;
  covered: number;
  remaining: number;
  percent: number;
}

export interface ResearchSyncNextResponse {
  total: number;
  success: number;
  failed: number;
  items: BatchDailySyncItem[];
  progress: ResearchSyncProgress;
}

export interface FullMarketSyncResponse {
  total: number;
  processed: number;
  success: number;
  empty: number;
  failed: number;
  skipped: number;
  completed: boolean;
  blocked: boolean;
  items: BatchDailySyncItem[];
  progress: ResearchSyncProgress;
}

export interface DataQualityItem {
  symbol: string;
  expected: number;
  present: number;
  missing: number;
  missing_dates: string[];
}

export interface DataQualityReport {
  expected_trading_days: number;
  symbols_checked: number;
  symbols_fully_covered: number;
  symbols_with_gaps: number;
  total_missing_bars: number;
  items: DataQualityItem[];
  warning: string;
}

export interface SymbolStatus {
  symbol: string;
  stock_exists: boolean;
  name: string | null;
  exchange: string | null;
  has_daily_bars: boolean;
  start_date: string | null;
  end_date: string | null;
  bar_count: number;
}

export interface DailyBar {
  symbol: string;
  trade_date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  amount: number;
}

export interface SyncJob {
  id: number;
  job_type: string;
  target: string;
  status: string;
  start_date: string | null;
  end_date: string | null;
  records: number;
  message: string;
}

export interface StrategyParameter {
  name: string;
  label: string;
  type: "int" | "float" | "string" | "bool";
  default: number | string | boolean | null;
  min: number | null;
  max: number | null;
  step: number | null;
  description: string;
}

export interface StrategyItem {
  name: string;
  display_name: string;
  description: string;
  source: "builtin" | "user";
  parameters: StrategyParameter[];
}

export interface BacktestMetrics {
  total_return: number;
  annual_return: number;
  max_drawdown: number;
  sharpe: number;
  final_equity: number;
  benchmark_return: number;
  excess_return: number;
}

export interface EquityPoint {
  trade_date: string;
  equity: number;
  cash: number;
  position_value: number;
  drawdown: number;
}

export interface BenchmarkPoint {
  trade_date: string;
  equity: number;
  return: number;
}

export interface TradeRecord {
  trade_date: string;
  symbol: string;
  side: string;
  price: number;
  quantity: number;
  amount: number;
}

export type SymbolSource = "manual" | "research_pool";

export interface BacktestRunRequest {
  strategy_name: string;
  symbol_source: SymbolSource;
  symbols: string[];
  pool_max_symbols: number;
  benchmark_symbol: string | null;
  start_date: string;
  end_date: string;
  initial_cash: number;
  rebalance_interval: number;
  risk_max_symbol_weight: number;
  risk_max_total_weight: number;
  risk_max_positions?: number | null;
  parameters: Record<string, number | string | boolean | null>;
}

export interface BacktestResponse {
  run_id: number | null;
  metrics: BacktestMetrics;
  equity_curve: EquityPoint[];
  benchmark_curve: BenchmarkPoint[];
  trades: TradeRecord[];
  symbol_source: SymbolSource;
  selected_symbols: string[];
}

export interface BacktestRun {
  id: number;
  strategy_name: string;
  start_date: string;
  end_date: string;
  initial_cash: number;
  final_equity: number;
  total_return: number;
  annual_return: number;
  max_drawdown: number;
  sharpe: number;
}
