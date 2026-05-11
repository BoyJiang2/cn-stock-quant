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

export interface BacktestMetrics {
  total_return: number;
  annual_return: number;
  max_drawdown: number;
  sharpe: number;
  final_equity: number;
}

export interface EquityPoint {
  trade_date: string;
  equity: number;
  cash: number;
  position_value: number;
  drawdown: number;
}

export interface TradeRecord {
  trade_date: string;
  symbol: string;
  side: string;
  price: number;
  quantity: number;
  amount: number;
}

export interface BacktestResponse {
  run_id: number | null;
  metrics: BacktestMetrics;
  equity_curve: EquityPoint[];
  trades: TradeRecord[];
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
