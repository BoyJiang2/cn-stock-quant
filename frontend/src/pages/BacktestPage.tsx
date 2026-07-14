import { Button, DatePicker, Form, Input, InputNumber, Select, Switch, Table, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import { Play } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import { EquityChart } from "../components/EquityChart";
import { MetricCard } from "../components/MetricCard";
import type { BacktestResponse, BacktestRun, BacktestRunRequest, DataDiagnostics, StrategyItem, StrategyParameter, SymbolSource } from "../types";

const { RangePicker } = DatePicker;

const SYMBOL_SOURCE_OPTIONS = [
  { label: "手动输入", value: "manual" },
  { label: "研究股票池", value: "research_pool" }
];

const BENCHMARK_OPTIONS = [
  { label: "无", value: "" },
  { label: "沪深300 (000300)", value: "000300" },
  { label: "中证500 (000905)", value: "000905" },
  { label: "中证1000 (000852)", value: "000852" },
  { label: "创业板指 (399006)", value: "399006" }
];

interface BacktestFormValues {
  strategy_name: string;
  symbol_source: SymbolSource;
  symbols: string;
  pool_max_symbols: number;
  benchmark_symbol: string;
  range: [dayjs.Dayjs, dayjs.Dayjs];
  initial_cash: number;
  rebalance_interval: number;
  risk_max_symbol_weight: number;
  risk_max_total_weight: number;
  risk_max_positions?: number | null;
  parameters: Record<string, number | string | boolean | null>;
}

export function BacktestPage() {
  const [form] = Form.useForm<BacktestFormValues>();
  const [result, setResult] = useState<BacktestResponse | null>(null);
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [strategies, setStrategies] = useState<StrategyItem[]>([]);
  const [selectedStrategy, setSelectedStrategy] = useState<StrategyItem | null>(null);
  const [diagnostics, setDiagnostics] = useState<DataDiagnostics | null>(null);
  const [loading, setLoading] = useState(false);

  const symbolSource = Form.useWatch<SymbolSource>("symbol_source", form) ?? "manual";

  const applyStrategyDefaults = (strategy: StrategyItem) => {
    setSelectedStrategy(strategy);
    form.setFieldValue("parameters", {});
    form.setFieldsValue({
      strategy_name: strategy.name,
      parameters: Object.fromEntries(strategy.parameters.map((parameter) => [parameter.name, parameter.default]))
    });
  };

  const loadStrategies = async () => {
    try {
      const response = await api.get<StrategyItem[]>("/api/strategies");
      setStrategies(response.data);
      const currentName = form.getFieldValue("strategy_name") || "moving_average";
      const current = response.data.find((strategy) => strategy.name === currentName) || response.data[0];
      if (current) {
        applyStrategyDefaults(current);
      }
    } catch {
      message.error("策略列表加载失败");
    }
  };

  const loadRuns = async () => {
    const response = await api.get<BacktestRun[]>("/api/backtests");
    setRuns(response.data);
  };

  const loadDiagnostics = async () => {
    const response = await api.get<DataDiagnostics>("/api/data/diagnostics");
    const diagnostics = response.data;
    setDiagnostics(diagnostics);
    if (!diagnostics.start_date || !diagnostics.end_date) {
      return;
    }
    const earliest = dayjs(diagnostics.start_date);
    const latest = dayjs(diagnostics.end_date);
    const twoYearsBeforeLatest = latest.subtract(2, "year");
    form.setFieldValue("range", [
      twoYearsBeforeLatest.isBefore(earliest, "day") ? earliest : twoYearsBeforeLatest,
      latest
    ]);
  };

  const loadRunDetail = async (id: number) => {
    const response = await api.get<BacktestResponse>(`/api/backtests/${id}`);
    setResult(response.data);
  };

  useEffect(() => {
    loadStrategies().catch(() => undefined);
    loadRuns().catch(() => undefined);
    loadDiagnostics().catch(() => {
      message.error("数据可用区间加载失败");
    });
  }, []);

  const disableUnavailableDate = (current: dayjs.Dayjs) => {
    if (!diagnostics?.start_date || !diagnostics.end_date) {
      return false;
    }
    return current.isBefore(dayjs(diagnostics.start_date), "day") || current.isAfter(dayjs(diagnostics.end_date), "day");
  };

  const runBacktest = async (values: BacktestFormValues) => {
    setLoading(true);
    try {
      let symbols: string[] = [];
      if (values.symbol_source === "manual") {
        symbols = values.symbols
          .split(/[,，\s]+/)
          .map((item) => item.trim())
          .filter(Boolean);
        if (!symbols.length) {
          message.warning("请至少输入一个股票代码");
          return;
        }
      }
      const payload: BacktestRunRequest = {
        strategy_name: values.strategy_name,
        symbol_source: values.symbol_source,
        symbols,
        pool_max_symbols: values.symbol_source === "research_pool" ? values.pool_max_symbols : 100,
        benchmark_symbol: values.benchmark_symbol || null,
        start_date: values.range[0].format("YYYY-MM-DD"),
        end_date: values.range[1].format("YYYY-MM-DD"),
        initial_cash: values.initial_cash,
        rebalance_interval: values.rebalance_interval,
        risk_max_symbol_weight: values.risk_max_symbol_weight,
        risk_max_total_weight: values.risk_max_total_weight,
        risk_max_positions: values.risk_max_positions || null,
        parameters: values.parameters || {}
      };
      const response = await api.post<BacktestResponse>("/api/backtests/run", payload);
      setResult(response.data);
      await loadRuns();
      message.success("回测完成");
    } catch (error: any) {
      message.error(error.response?.data?.detail || "回测失败");
    } finally {
      setLoading(false);
    }
  };

  const selectStrategy = (name: string) => {
    const strategy = strategies.find((item) => item.name === name);
    if (strategy) {
      applyStrategyDefaults(strategy);
    }
  };

  const renderParameterInput = (parameter: StrategyParameter) => {
    if (parameter.type === "bool") {
      return <Switch />;
    }
    if (parameter.type === "int" || parameter.type === "float") {
      return (
        <InputNumber
          max={parameter.max ?? undefined}
          min={parameter.min ?? undefined}
          step={parameter.step ?? (parameter.type === "int" ? 1 : 0.01)}
        />
      );
    }
    return <Input />;
  };

  const tradeColumns: ColumnsType<BacktestResponse["trades"][number]> = [
    { title: "日期", dataIndex: "trade_date" },
    { title: "代码", dataIndex: "symbol" },
    { title: "方向", dataIndex: "side" },
    { title: "价格", dataIndex: "price" },
    { title: "数量", dataIndex: "quantity" },
    { title: "金额", dataIndex: "amount" }
  ];

  const runColumns: ColumnsType<BacktestRun> = [
    { title: "ID", dataIndex: "id", width: 80 },
    { title: "策略", dataIndex: "strategy_name" },
    { title: "开始", dataIndex: "start_date" },
    { title: "结束", dataIndex: "end_date" },
    {
      title: "收益",
      render: (_, row) => `${(row.total_return * 100).toFixed(2)}%`
    },
    {
      title: "最大回撤",
      render: (_, row) => `${(row.max_drawdown * 100).toFixed(2)}%`
    },
    {
      title: "操作",
      render: (_, row) => (
        <Button onClick={() => loadRunDetail(row.id)} size="small">
          查看
        </Button>
      )
    }
  ];

  return (
    <section className="page">
      <h1>回测中心</h1>
      <Form
        form={form}
        className="formPanel"
        initialValues={{
          strategy_name: "moving_average",
          symbol_source: "manual",
          symbols: "000001",
          pool_max_symbols: 50,
          benchmark_symbol: "000300",
          range: [dayjs().subtract(2, "year"), dayjs()],
          initial_cash: 1000000,
          rebalance_interval: 1,
          risk_max_symbol_weight: 1,
          risk_max_total_weight: 1,
          risk_max_positions: null
        }}
        layout="inline"
        onFinish={runBacktest}
      >
        <Form.Item label="策略" name="strategy_name" rules={[{ required: true, message: "请选择策略" }]}>
          <Select
            onChange={selectStrategy}
            options={strategies.map((strategy) => ({
              label: strategy.display_name,
              value: strategy.name
            }))}
            style={{ width: 180 }}
          />
        </Form.Item>
        <Form.Item label="股票来源" name="symbol_source" rules={[{ required: true, message: "请选择股票来源" }]}>
          <Select options={SYMBOL_SOURCE_OPTIONS} style={{ width: 140 }} />
        </Form.Item>
        {symbolSource === "manual" ? (
          <Form.Item label="股票代码" name="symbols" rules={[{ required: true, message: "请输入股票代码" }]}>
            <Input placeholder="多个代码用逗号或空格分隔" style={{ width: 220 }} />
          </Form.Item>
        ) : (
          <Form.Item
            label="池上限"
            name="pool_max_symbols"
            rules={[
              { required: true, message: "请输入池上限" },
              { type: "number", min: 1, max: 300, message: "范围 1~300" }
            ]}
          >
            <InputNumber min={1} max={300} step={1} style={{ width: 120 }} />
          </Form.Item>
        )}
        <Form.Item label="基准" name="benchmark_symbol">
          <Select options={BENCHMARK_OPTIONS} style={{ width: 180 }} />
        </Form.Item>
        <Form.Item label="区间" name="range" rules={[{ required: true, message: "请选择日期区间" }]}>
          <RangePicker disabledDate={disableUnavailableDate} />
        </Form.Item>
        <Form.Item label="初始资金" name="initial_cash" rules={[{ required: true, message: "请输入初始资金" }]}>
          <InputNumber min={10000} step={10000} />
        </Form.Item>
        <Form.Item label="调仓间隔" name="rebalance_interval" rules={[{ required: true, message: "请输入调仓间隔" }]}>
          <InputNumber min={1} step={1} />
        </Form.Item>
        <Form.Item label="单标的上限" name="risk_max_symbol_weight" rules={[{ required: true, message: "请输入单标的上限" }]}>
          <InputNumber max={1} min={0} step={0.05} />
        </Form.Item>
        <Form.Item label="总仓位上限" name="risk_max_total_weight" rules={[{ required: true, message: "请输入总仓位上限" }]}>
          <InputNumber max={1} min={0} step={0.05} />
        </Form.Item>
        <Form.Item label="最大持仓数" name="risk_max_positions">
          <InputNumber min={0} step={1} />
        </Form.Item>
        {selectedStrategy?.parameters.map((parameter) => (
          <Form.Item
            key={parameter.name}
            label={parameter.label}
            name={["parameters", parameter.name]}
            valuePropName={parameter.type === "bool" ? "checked" : "value"}
            tooltip={parameter.description || undefined}
          >
            {renderParameterInput(parameter)}
          </Form.Item>
        ))}
        <Button
          disabled={!diagnostics?.start_date || !diagnostics?.end_date}
          htmlType="submit"
          icon={<Play size={16} />}
          loading={loading}
          type="primary"
        >
          运行回测
        </Button>
      </Form>
      {result && (
        <>
          <div className="metricGrid">
            <MetricCard label="股票来源" value={result.symbol_source === "research_pool" ? "研究池" : "手动"} />
            <MetricCard label="实际股票数" value={String(result.selected_symbols.length)} />
            <MetricCard label="期末资产" value={result.metrics.final_equity.toLocaleString()} />
            <MetricCard label="累计收益" tone={result.metrics.total_return >= 0 ? "good" : "bad"} value={`${(result.metrics.total_return * 100).toFixed(2)}%`} />
            <MetricCard label="基准收益" value={`${((result.metrics.benchmark_return || 0) * 100).toFixed(2)}%`} />
            <MetricCard label="超额收益" tone={(result.metrics.excess_return || 0) >= 0 ? "good" : "bad"} value={`${((result.metrics.excess_return || 0) * 100).toFixed(2)}%`} />
            <MetricCard label="年化收益" value={`${(result.metrics.annual_return * 100).toFixed(2)}%`} />
            <MetricCard label="最大回撤" tone="bad" value={`${(result.metrics.max_drawdown * 100).toFixed(2)}%`} />
            <MetricCard label="夏普" value={result.metrics.sharpe.toFixed(2)} />
          </div>
          <EquityChart benchmark={result.benchmark_curve} data={result.equity_curve} />
          <Table
            columns={tradeColumns}
            dataSource={result.trades}
            pagination={{ pageSize: 10 }}
            rowKey={(row) => `${row.trade_date}-${row.symbol}-${row.side}-${row.quantity}`}
          />
        </>
      )}
      <div className="panel">
        <div className="panelTitle">回测历史</div>
        <Table columns={runColumns} dataSource={runs} pagination={{ pageSize: 6 }} rowKey="id" />
      </div>
    </section>
  );
}
