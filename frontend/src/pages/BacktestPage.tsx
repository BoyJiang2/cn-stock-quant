import { Button, DatePicker, Form, Input, InputNumber, Table, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import { Play } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import { EquityChart } from "../components/EquityChart";
import { MetricCard } from "../components/MetricCard";
import type { BacktestResponse, BacktestRun } from "../types";

const { RangePicker } = DatePicker;

interface BacktestFormValues {
  symbols: string;
  range: [dayjs.Dayjs, dayjs.Dayjs];
  initial_cash: number;
  fast_window: number;
  slow_window: number;
}

export function BacktestPage() {
  const [result, setResult] = useState<BacktestResponse | null>(null);
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [loading, setLoading] = useState(false);

  const loadRuns = async () => {
    const response = await api.get<BacktestRun[]>("/api/backtests");
    setRuns(response.data);
  };

  const loadRunDetail = async (id: number) => {
    const response = await api.get<BacktestResponse>(`/api/backtests/${id}`);
    setResult(response.data);
  };

  useEffect(() => {
    loadRuns().catch(() => undefined);
  }, []);

  const runBacktest = async (values: BacktestFormValues) => {
    setLoading(true);
    try {
      const response = await api.post<BacktestResponse>("/api/backtests/run", {
        strategy_name: "moving_average",
        symbols: values.symbols.split(",").map((item: string) => item.trim()),
        start_date: values.range[0].format("YYYY-MM-DD"),
        end_date: values.range[1].format("YYYY-MM-DD"),
        initial_cash: values.initial_cash,
        fast_window: values.fast_window,
        slow_window: values.slow_window
      });
      setResult(response.data);
      await loadRuns();
      message.success("回测完成");
    } catch (error: any) {
      message.error(error.response?.data?.detail || "回测失败");
    } finally {
      setLoading(false);
    }
  };

  const tradeColumns: ColumnsType<BacktestResponse["trades"][number]> = [
    { title: "日期", dataIndex: "trade_date" },
    { title: "股票", dataIndex: "symbol" },
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
      title: "总收益",
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
        className="formPanel"
        initialValues={{
          symbols: "000001",
          range: [dayjs().subtract(2, "year"), dayjs()],
          initial_cash: 1000000,
          fast_window: 20,
          slow_window: 60
        }}
        layout="inline"
        onFinish={runBacktest}
      >
        <Form.Item label="股票" name="symbols">
          <Input />
        </Form.Item>
        <Form.Item label="区间" name="range">
          <RangePicker />
        </Form.Item>
        <Form.Item label="资金" name="initial_cash">
          <InputNumber min={10000} step={10000} />
        </Form.Item>
        <Form.Item label="快线" name="fast_window">
          <InputNumber min={2} />
        </Form.Item>
        <Form.Item label="慢线" name="slow_window">
          <InputNumber min={5} />
        </Form.Item>
        <Button htmlType="submit" icon={<Play size={16} />} loading={loading} type="primary">
          运行回测
        </Button>
      </Form>
      {result && (
        <>
          <div className="metricGrid">
            <MetricCard label="最终权益" value={result.metrics.final_equity.toLocaleString()} />
            <MetricCard label="总收益" tone={result.metrics.total_return >= 0 ? "good" : "bad"} value={`${(result.metrics.total_return * 100).toFixed(2)}%`} />
            <MetricCard label="年化收益" value={`${(result.metrics.annual_return * 100).toFixed(2)}%`} />
            <MetricCard label="最大回撤" tone="bad" value={`${(result.metrics.max_drawdown * 100).toFixed(2)}%`} />
            <MetricCard label="夏普" value={result.metrics.sharpe.toFixed(2)} />
          </div>
          <EquityChart data={result.equity_curve} />
          <Table
            columns={tradeColumns}
            dataSource={result.trades}
            pagination={{ pageSize: 10 }}
            rowKey={(row) => `${row.trade_date}-${row.symbol}-${row.side}-${row.quantity}`}
          />
        </>
      )}
      <div className="panel">
        <div className="panelTitle">历史回测</div>
        <Table
          columns={runColumns}
          dataSource={runs}
          pagination={{ pageSize: 6 }}
          rowKey="id"
        />
      </div>
    </section>
  );
}
