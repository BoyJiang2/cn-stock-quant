import { Alert, Button, Card, Col, DatePicker, Descriptions, Divider, Form, Input, InputNumber, Row, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import { Plus, RefreshCw, Save, Trash2, WalletCards } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import { MetricCard } from "../components/MetricCard";
import type { PaperPortfolioDiagnostics, PaperPortfolioSnapshot, PaperPortfolioState, PaperPortfolioValuation } from "../types";

const { Text, Title } = Typography;

interface SnapshotFormValues {
  as_of_date: dayjs.Dayjs;
  cash: number;
  positions: { symbol: string; quantity: number }[];
}

const formatAmount = (value: number) => new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value);

export function PortfolioPage() {
  const [form] = Form.useForm<SnapshotFormValues>();
  const [portfolio, setPortfolio] = useState<PaperPortfolioState | null>(null);
  const [diagnostics, setDiagnostics] = useState<PaperPortfolioDiagnostics | null>(null);
  const [history, setHistory] = useState<PaperPortfolioValuation[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const loadPortfolio = async () => {
    setLoading(true);
    try {
      const [current, valuationHistory, diagnosticResult] = await Promise.all([
        api.get<PaperPortfolioState>("/api/portfolio/current"),
        api.get<PaperPortfolioValuation[]>("/api/portfolio/history"),
        api.get<PaperPortfolioDiagnostics>("/api/portfolio/diagnostics")
      ]);
      setPortfolio(current.data);
      setHistory(valuationHistory.data);
      setDiagnostics(diagnosticResult.data);
      form.setFieldsValue({
        as_of_date: current.data.as_of_date ? dayjs(current.data.as_of_date) : dayjs().subtract(1, "day"),
        cash: current.data.cash,
        positions: current.data.positions.map((position) => ({ symbol: position.symbol, quantity: position.quantity }))
      });
    } catch (error: any) {
      message.error(error.response?.data?.detail || "Failed to load paper portfolio.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadPortfolio();
  }, []);

  const saveSnapshot = async (values: SnapshotFormValues) => {
    setSaving(true);
    try {
      const payload: PaperPortfolioSnapshot = {
        as_of_date: values.as_of_date.format("YYYY-MM-DD"),
        cash: values.cash,
        positions: (values.positions ?? [])
          .filter((position) => position?.symbol && position.quantity > 0)
          .map((position) => ({ symbol: position.symbol.trim(), quantity: position.quantity }))
      };
      const response = await api.put<PaperPortfolioState>("/api/portfolio/snapshot", payload);
      setPortfolio(response.data);
      form.setFieldsValue({
        as_of_date: dayjs(response.data.as_of_date),
        cash: response.data.cash,
        positions: response.data.positions.map((position) => ({ symbol: position.symbol, quantity: position.quantity }))
      });
      const valuationHistory = await api.get<PaperPortfolioValuation[]>("/api/portfolio/history");
      const diagnosticResult = await api.get<PaperPortfolioDiagnostics>("/api/portfolio/diagnostics");
      setHistory(valuationHistory.data);
      setDiagnostics(diagnosticResult.data);
      message.success("Paper portfolio snapshot saved.");
    } catch (error: any) {
      message.error(error.response?.data?.detail || "Failed to save paper portfolio snapshot.");
    } finally {
      setSaving(false);
    }
  };

  const positionColumns: ColumnsType<NonNullable<PaperPortfolioState>["positions"][number]> = [
    { title: "代码", dataIndex: "symbol", width: 100 },
    { title: "名称", dataIndex: "name", width: 150, render: (value: string | null) => value || "-" },
    { title: "数量", dataIndex: "quantity", align: "right" },
    { title: "收盘价", dataIndex: "reference_price", align: "right", render: (value: number) => value.toFixed(2) },
    { title: "价格日期", dataIndex: "price_date", width: 120 },
    { title: "估值", dataIndex: "market_value", align: "right", render: (value: number) => formatAmount(value) },
    {
      title: "账户占比",
      align: "right",
      render: (_, row) => portfolio?.equity ? `${((row.market_value / portfolio.equity) * 100).toFixed(2)}%` : "-"
    }
  ];

  const historyColumns: ColumnsType<PaperPortfolioValuation> = [
    { title: "日期", dataIndex: "as_of_date", width: 140 },
    { title: "账户权益", dataIndex: "equity", align: "right", render: (value: number) => formatAmount(value) },
    { title: "持仓市值", dataIndex: "position_value", align: "right", render: (value: number) => formatAmount(value) },
    { title: "可用现金", dataIndex: "cash", align: "right", render: (value: number) => formatAmount(value) }
  ];

  const latestHistory = useMemo(() => [...history].reverse(), [history]);

  return (
    <section className="page">
      <div className="pageHeader">
        <div>
          <Title level={1}>模拟盘</Title>
          <Text type="secondary">账户快照按本地研究收盘价估值，不代表券商成交或实时资产。</Text>
        </div>
        <Space>
          <Tag icon={<WalletCards size={14} />} color={portfolio?.as_of_date ? "blue" : "default"}>
            {portfolio?.as_of_date ? `截至 ${portfolio.as_of_date}` : "尚未录入快照"}
          </Tag>
          <Button aria-label="刷新模拟盘" icon={<RefreshCw size={16} />} loading={loading} onClick={loadPortfolio} />
        </Space>
      </div>

      <Alert
        className="advisoryNotice"
        message="快照只保存账户状态"
        description="保存会替换当前持仓，并为该日期写入估值记录。持仓股票必须在所选日期具有本地日线收盘价。"
        showIcon
        type="info"
      />

      <div className="metricGrid" style={{ marginTop: 16 }}>
        <MetricCard label="账户权益" value={formatAmount(portfolio?.equity ?? 0)} />
        <MetricCard label="持仓市值" value={formatAmount(portfolio?.position_value ?? 0)} />
        <MetricCard label="可用现金" value={formatAmount(portfolio?.cash ?? 0)} />
        <MetricCard label="持仓数量" value={String(portfolio?.positions.length ?? 0)} />
      </div>

      <Card className="panel" style={{ marginTop: 16 }} title="风险诊断">
        <Descriptions column={{ xs: 2, md: 4 }} size="small">
          <Descriptions.Item label="现金比例">{`${((diagnostics?.cash_weight ?? 0) * 100).toFixed(2)}%`}</Descriptions.Item>
          <Descriptions.Item label="总敞口">{`${((diagnostics?.gross_exposure ?? 0) * 100).toFixed(2)}%`}</Descriptions.Item>
          <Descriptions.Item label="最大单一持仓">{`${((diagnostics?.largest_position_weight ?? 0) * 100).toFixed(2)}%`}</Descriptions.Item>
          <Descriptions.Item label="前三持仓">{`${((diagnostics?.top_three_weight ?? 0) * 100).toFixed(2)}%`}</Descriptions.Item>
          <Descriptions.Item label="当前回撤">{`${((diagnostics?.current_drawdown ?? 0) * 100).toFixed(2)}%`}</Descriptions.Item>
          <Descriptions.Item label="历史最大回撤">{`${((diagnostics?.max_drawdown ?? 0) * 100).toFixed(2)}%`}</Descriptions.Item>
          <Descriptions.Item label="集中度 HHI">{(diagnostics?.concentration_hhi ?? 0).toFixed(3)}</Descriptions.Item>
        </Descriptions>
        {diagnostics?.warnings.map((warning) => <Alert key={warning} message={warning} showIcon type="warning" style={{ marginTop: 10 }} />)}
      </Card>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={15}>
          <Card className="panel" title="当前持仓">
            <Table
              columns={positionColumns}
              dataSource={portfolio?.positions ?? []}
              loading={loading}
              locale={{ emptyText: "尚未保存持仓快照" }}
              pagination={false}
              rowKey="symbol"
              size="small"
            />
          </Card>
        </Col>
        <Col xs={24} lg={9}>
          <Card className="panel" title="录入账户快照">
            <Form form={form} layout="vertical" onFinish={saveSnapshot}>
              <Row gutter={12}>
                <Col span={12}>
                  <Form.Item label="快照日期" name="as_of_date" rules={[{ required: true }]}>
                    <DatePicker style={{ width: "100%" }} />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label="现金" name="cash" rules={[{ required: true }]}>
                    <InputNumber min={0} precision={2} style={{ width: "100%" }} />
                  </Form.Item>
                </Col>
              </Row>
              <Divider orientation="left" plain>持仓</Divider>
              <Form.List name="positions">
                {(fields, { add, remove }) => (
                  <Space direction="vertical" size={8} style={{ width: "100%" }}>
                    {fields.map((field) => (
                      <Row gutter={8} key={field.key}>
                        <Col flex="auto">
                          <Form.Item name={[field.name, "symbol"]} rules={[{ required: true }]} noStyle>
                            <Input placeholder="股票代码或名称" />
                          </Form.Item>
                        </Col>
                        <Col flex="112px">
                          <Form.Item name={[field.name, "quantity"]} rules={[{ required: true }]} noStyle>
                            <InputNumber min={1} precision={0} step={100} style={{ width: "100%" }} />
                          </Form.Item>
                        </Col>
                        <Col flex="32px">
                          <Button aria-label="删除持仓" icon={<Trash2 size={16} />} onClick={() => remove(field.name)} type="text" />
                        </Col>
                      </Row>
                    ))}
                    <Button icon={<Plus size={16} />} onClick={() => add()} type="dashed">添加持仓</Button>
                  </Space>
                )}
              </Form.List>
              <div className="formActions" style={{ marginTop: 16 }}>
                <Button htmlType="submit" icon={<Save size={16} />} loading={saving} type="primary">保存快照</Button>
              </div>
            </Form>
          </Card>
        </Col>
      </Row>

      <Card className="panel" style={{ marginTop: 16 }} title="估值历史">
        <Table
          columns={historyColumns}
          dataSource={latestHistory}
          loading={loading}
          locale={{ emptyText: "保存快照后会在这里保留每日估值" }}
          pagination={{ pageSize: 12, hideOnSinglePage: true }}
          rowKey="as_of_date"
          size="small"
        />
      </Card>
    </section>
  );
}
