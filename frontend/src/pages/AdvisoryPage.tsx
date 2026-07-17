import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  DatePicker,
  Descriptions,
  Divider,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message
} from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import { Bot, Check, CircleX, FileText, Plus, Sparkles, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";

const { Text, Title } = Typography;

interface StrategyItem {
  name: string;
  display_name: string;
}

interface AdvisoryPosition {
  symbol: string;
  quantity: number;
}

interface AdvisoryFormValues {
  strategy_name: string;
  as_of_date: dayjs.Dayjs;
  validation_id?: number;
  symbols: string;
  cash: number;
  positions: AdvisoryPosition[];
  max_symbol_weight: number;
  max_total_weight: number;
  max_positions: number;
  allow_remote_llm: boolean;
}

interface AdvisoryTrade {
  symbol: string;
  side: "buy" | "sell";
  current_quantity: number;
  target_quantity: number;
  quantity: number;
  reference_price: number;
  estimated_amount: number;
}

interface MarketEvidence {
  available: boolean;
  benchmark_symbol: string;
  as_of_date: string;
  data_end_date: string | null;
  regime: string | null;
  confidence: number | null;
  trend_score: number | null;
  breadth_score: number | null;
  volatility_score: number | null;
  drawdown: number | null;
  reasons: string[];
  warning: string | null;
}

interface NewsEvidenceItem {
  symbol: string | null;
  source: string;
  title: string;
  event_type: string;
  sentiment_label: string;
  published_at: string;
  known_at: string;
}

interface NewsEvidence {
  availability_mode: "observed";
  window_start: string;
  as_of_at: string;
  total_items: number;
  severe_company_risk_count: number;
  company_risk_count: number;
  items: NewsEvidenceItem[];
}

interface FactorValue {
  name: string;
  direction: number;
  raw_value: number | null;
}

interface FactorSymbolEvidence {
  symbol: string;
  available: boolean;
  values: FactorValue[];
  warning: string | null;
}

interface FactorEvidence {
  availability_mode: "observed_trailing";
  as_of_date: string;
  data_start_date: string | null;
  data_end_date: string | null;
  factor_names: string[];
  symbols: FactorSymbolEvidence[];
  warnings: string[];
}

interface EligibleValidationOption {
  id: number;
  backtest_run_id: number;
  strategy_name: string;
  as_of_date: string;
  strategy_parameters: Record<string, unknown>;
  aggregate: Record<string, number>;
}

interface ValidationEvidence {
  validation_id: number;
  aggregate: Record<string, number>;
}

interface AdvisoryDraft {
  id: number;
  status: "draft" | "reviewed" | "expired" | "rejected";
  as_of_date: string;
  earliest_execution_date: string | null;
  price_basis: "research_close_only";
  strategy_name: string;
  total_equity: number;
  raw_target_weights: Record<string, number>;
  accepted_target_weights: Record<string, number>;
  rejected_target_weights: Record<string, string>;
  trade_plan: AdvisoryTrade[];
  market_evidence: MarketEvidence;
  news_evidence: NewsEvidence;
  factor_evidence: FactorEvidence;
  validation_evidence: ValidationEvidence | null;
  warnings: string[];
  remote_llm_enabled: boolean;
  llm_summary: string | null;
}

interface AdvisoryCapabilities {
  remote_llm_configured: boolean;
  remote_llm_default_enabled: boolean;
  streaming: boolean;
  requires_human_confirmation: boolean;
}

const formatAmount = (value: number) =>
  new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value);

const formatFactorValue = (value: number | null) => {
  if (value === null) {
    return "-";
  }
  return Math.abs(value) < 0.001 && value !== 0 ? value.toExponential(2) : value.toFixed(4);
};

const parseSymbols = (value: string) =>
  [...new Set(value.split(/[，,\s]+/).map((symbol) => symbol.trim()).filter(Boolean))];

const parseSse = (block: string) => {
  const event = block.match(/^event:\s*(.+)$/m)?.[1]?.trim();
  const data = block.match(/^data:\s*(.+)$/m)?.[1];
  if (!event || !data) {
    return null;
  }
  try {
    return { event, data: JSON.parse(data) as { text?: string; message?: string } };
  } catch {
    return null;
  }
};

export function AdvisoryPage() {
  const [form] = Form.useForm<AdvisoryFormValues>();
  const [strategies, setStrategies] = useState<StrategyItem[]>([]);
  const [capabilities, setCapabilities] = useState<AdvisoryCapabilities | null>(null);
  const [eligibleValidations, setEligibleValidations] = useState<EligibleValidationOption[]>([]);
  const [selectedValidation, setSelectedValidation] = useState<EligibleValidationOption | null>(null);
  const [loadingValidations, setLoadingValidations] = useState(false);
  const [validationLoadError, setValidationLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState<AdvisoryDraft | null>(null);
  const [loadingDraft, setLoadingDraft] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [updatingLifecycle, setUpdatingLifecycle] = useState(false);
  const [explanation, setExplanation] = useState("");

  useEffect(() => {
    api.get<StrategyItem[]>("/api/strategies")
      .then((response) => setStrategies(response.data))
      .catch(() => message.error("策略列表加载失败"));
    api.get<AdvisoryCapabilities>("/api/advisory/capabilities")
      .then((response) => setCapabilities(response.data))
      .catch(() => message.warning("LLM 运行状态暂时不可用"));
  }, []);

  useEffect(() => {
    setLoadingValidations(true);
    api.get<EligibleValidationOption[]>("/api/advisory/eligible-validations")
      .then((response) => setEligibleValidations(response.data))
      .catch(() => setValidationLoadError("Failed to load eligible OOS validation records."))
      .finally(() => setLoadingValidations(false));
  }, []);

  const targetWeightRows = useMemo(
    () => Object.entries(draft?.accepted_target_weights ?? {}).map(([symbol, weight]) => ({ symbol, weight })),
    [draft]
  );

  const rejectedWeightRows = useMemo(
    () => Object.entries(draft?.rejected_target_weights ?? {}).map(([symbol, reason]) => ({ symbol, reason })),
    [draft]
  );

  const createDraft = async (values: AdvisoryFormValues) => {
    const symbols = parseSymbols(values.symbols);
    if (!symbols.length) {
      message.warning("请至少输入一只股票代码");
      return;
    }
    setLoadingDraft(true);
    setDraft(null);
    setExplanation("");
    try {
      const response = await api.post<AdvisoryDraft>("/api/advisory/drafts", {
        strategy_name: values.strategy_name,
        as_of_date: values.as_of_date.format("YYYY-MM-DD"),
        validation_id: selectedValidation?.id,
        strategy_parameters: selectedValidation?.strategy_parameters ?? {},
        symbols,
        cash: values.cash,
        positions: (values.positions ?? [])
          .filter((position) => position?.symbol && position.quantity > 0)
          .map((position) => ({ symbol: position.symbol.trim(), quantity: position.quantity })),
        max_symbol_weight: values.max_symbol_weight,
        max_total_weight: values.max_total_weight,
        max_positions: values.max_positions,
        allow_remote_llm: values.allow_remote_llm
      });
      setDraft(response.data);
      message.success("已生成研究草案");
    } catch (error: any) {
      message.error(error.response?.data?.detail || "研究草案生成失败");
    } finally {
      setLoadingDraft(false);
    }
  };

  const streamExplanation = async () => {
    if (!draft) {
      return;
    }
    setStreaming(true);
    setExplanation("");
    try {
      const response = await fetch(`/api/advisory/drafts/${draft.id}/stream`, { method: "POST" });
      if (!response.ok || !response.body) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.detail || "模型解释服务暂不可用");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let completed = false;
      while (!completed) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() ?? "";
        for (const block of blocks) {
          const event = parseSse(block);
          if (!event) {
            continue;
          }
          if (event.event === "delta" && event.data.text) {
            setExplanation((current) => current + event.data.text);
          }
          if (event.event === "error") {
            throw new Error(event.data.message || "模型解释生成失败");
          }
          if (event.event === "complete") {
            completed = true;
          }
        }
        if (done) {
          completed = true;
        }
      }
    } catch (error: any) {
      message.error(error.message || "模型解释生成失败");
    } finally {
      setStreaming(false);
    }
  };

  const updateLifecycle = async (action: "review" | "reject") => {
    if (!draft) {
      return;
    }
    setUpdatingLifecycle(true);
    try {
      const status = await api.get<{ status: AdvisoryDraft["status"] }>(`/api/advisory/drafts/${draft.id}/status`);
      if (status.data.status !== draft.status) {
        setDraft((current) => current ? { ...current, status: status.data.status } : current);
      }
      if (status.data.status === "expired" || status.data.status === "rejected") {
        message.warning(`Advisory draft is ${status.data.status}; create a fresh draft instead.`);
        return;
      }
      const response = await api.post<{ status: AdvisoryDraft["status"] }>(
        `/api/advisory/drafts/${draft.id}/${action}`,
        action === "reject" ? {} : undefined
      );
      setDraft((current) => current ? { ...current, status: response.data.status } : current);
      message.success(action === "review" ? "Research draft marked as reviewed." : "Research draft rejected.");
    } catch (error: any) {
      message.error(error.response?.data?.detail || "Failed to update advisory lifecycle.");
    } finally {
      setUpdatingLifecycle(false);
    }
  };

  const tradeColumns: ColumnsType<AdvisoryTrade> = [
    { title: "代码", dataIndex: "symbol", width: 110 },
    {
      title: "建议动作",
      dataIndex: "side",
      width: 110,
      render: (side: AdvisoryTrade["side"]) => <Tag color={side === "buy" ? "green" : "red"}>{side === "buy" ? "买入草案" : "卖出草案"}</Tag>
    },
    { title: "当前数量", dataIndex: "current_quantity", align: "right" },
    { title: "目标数量", dataIndex: "target_quantity", align: "right" },
    { title: "调整数量", dataIndex: "quantity", align: "right" },
    { title: "研究收盘价", dataIndex: "reference_price", align: "right", render: (value: number) => value.toFixed(2) },
    { title: "估算金额", dataIndex: "estimated_amount", align: "right", render: (value: number) => formatAmount(value) }
  ];

  return (
    <section className="page">
      <div className="pageHeader">
        <div>
          <Title level={1}>A 股 ValueCell</Title>
          <Text type="secondary">以收盘数据生成下一交易日的研究草案。所有交易均须人工确认。</Text>
        </div>
        <Tag color="blue" icon={<FileText size={14} />}>研究模式</Tag>
      </div>

      <Alert
        className="advisoryNotice"
        message="确定性策略与风控先行，LLM 只生成解释与风险提示"
        description="草案价格仅为研究收盘价，不是实时行情或订单。远程 LLM 仅在你明确勾选授权且服务端完成配置后才会接收研究上下文。"
        showIcon
        type="info"
      />

      <Card className="panel advisoryForm" title="生成调仓研究草案">
        <Form
          form={form}
          initialValues={{
            strategy_name: "moving_average",
            as_of_date: dayjs().subtract(1, "day"),
            symbols: "000001,600000,002156",
            cash: 200000,
            positions: [],
            max_symbol_weight: 0.1,
            max_total_weight: 0.8,
            max_positions: 20,
            allow_remote_llm: false
          }}
          layout="vertical"
          onFinish={createDraft}
        >
          {validationLoadError && (
            <Alert message={validationLoadError} showIcon type="error" style={{ marginBottom: 16 }} />
          )}
          {!loadingValidations && !validationLoadError && eligibleValidations.length === 0 && (
            <Alert
              message="No eligible OOS validation is available. This draft will not include validated historical evidence."
              showIcon
              type="warning"
              style={{ marginBottom: 16 }}
            />
          )}
          <Form.Item label="OOS validation (optional)" name="validation_id">
            <Select
              allowClear
              loading={loadingValidations}
              onChange={(validationId) => {
                const option = eligibleValidations.find((item) => item.id === validationId) ?? null;
                setSelectedValidation(option);
                if (option) {
                  form.setFieldsValue({
                    strategy_name: option.strategy_name,
                    as_of_date: dayjs(option.as_of_date),
                  });
                }
              }}
              options={eligibleValidations.map((item) => ({
                value: item.id,
                label: `#${item.id} | ${item.strategy_name} | ${item.as_of_date} | OOS excess ${((item.aggregate.excess_return ?? 0) * 100).toFixed(2)}%`,
              }))}
              placeholder="Attach an eligible OOS validation"
            />
          </Form.Item>
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item label="策略" name="strategy_name" rules={[{ required: true, message: "请选择策略" }]}>
                <Select disabled={Boolean(selectedValidation)} options={strategies.map((strategy) => ({ label: strategy.display_name, value: strategy.name }))} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="决策日" name="as_of_date" rules={[{ required: true, message: "请选择决策日" }]}>
                <DatePicker disabled={Boolean(selectedValidation)} style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="可用现金" name="cash" rules={[{ required: true, message: "请输入可用现金" }]}>
                <InputNumber min={0} precision={2} style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col span={24}>
              <Form.Item label="股票池代码" name="symbols" rules={[{ required: true, message: "请输入股票代码" }]} extra="使用逗号或空格分隔。当前持仓会自动并入草案，未入选持仓将被明确评估为卖出候选。">
                <Input placeholder="例如：000001, 600000, 002156" />
              </Form.Item>
            </Col>
          </Row>

          <Divider orientation="left">当前持仓</Divider>
          <Form.List name="positions">
            {(fields, { add, remove }) => (
              <Space direction="vertical" size={8} style={{ width: "100%" }}>
                {fields.map((field) => (
                  <Row gutter={12} key={field.key} align="middle">
                    <Col xs={11} md={7}>
                      <Form.Item name={[field.name, "symbol"]} rules={[{ required: true, message: "请输入代码" }]} noStyle>
                        <Input placeholder="股票代码" />
                      </Form.Item>
                    </Col>
                    <Col xs={10} md={7}>
                      <Form.Item name={[field.name, "quantity"]} rules={[{ required: true, message: "请输入数量" }]} noStyle>
                        <InputNumber min={1} step={100} precision={0} placeholder="持仓股数" style={{ width: "100%" }} />
                      </Form.Item>
                    </Col>
                    <Col xs={3} md={2}>
                      <Button aria-label="删除持仓" icon={<Trash2 size={16} />} onClick={() => remove(field.name)} type="text" />
                    </Col>
                  </Row>
                ))}
                <Button icon={<Plus size={16} />} onClick={() => add()} type="dashed">添加持仓</Button>
              </Space>
            )}
          </Form.List>

          <Divider orientation="left">风险边界</Divider>
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item label="单票权重上限" name="max_symbol_weight">
                <InputNumber min={0} max={1} step={0.05} style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="总仓位上限" name="max_total_weight">
                <InputNumber min={0} max={1} step={0.05} style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="最大持仓数" name="max_positions">
                <InputNumber min={1} max={500} precision={0} style={{ width: "100%" }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="allow_remote_llm" valuePropName="checked">
            <Checkbox disabled={!capabilities?.remote_llm_configured}>
              我同意将必要的研究上下文发送至已配置的远程 LLM，以生成解释和风险提示
            </Checkbox>
          </Form.Item>
          {!capabilities?.remote_llm_configured && (
            <Text type="secondary">远程 LLM 尚未在服务器配置；仍可生成不含模型解释的确定性草案。</Text>
          )}
          <div className="formActions">
            <Button htmlType="submit" icon={<FileText size={16} />} loading={loadingDraft} type="primary">生成研究草案</Button>
          </div>
        </Form>
      </Card>

      {draft && (
        <Space direction="vertical" size={16} style={{ display: "flex", marginTop: 16 }}>
          <Card
            className="panel"
            title="草案概览"
            extra={
              <Space>
                <Tag color={draft.status === "draft" ? "blue" : draft.status === "reviewed" ? "green" : draft.status === "rejected" ? "red" : "orange"}>{draft.status}</Tag>
                <Button disabled={draft.status !== "draft"} icon={<Check size={16} />} loading={updatingLifecycle} onClick={() => updateLifecycle("review")}>Mark reviewed</Button>
                <Button danger disabled={draft.status !== "draft" && draft.status !== "reviewed"} icon={<CircleX size={16} />} loading={updatingLifecycle} onClick={() => updateLifecycle("reject")}>Reject</Button>
              </Space>
            }
          >
            <Descriptions column={{ xs: 1, sm: 2, lg: 4 }} size="small">
              <Descriptions.Item label="草案编号">#{draft.id}</Descriptions.Item>
              <Descriptions.Item label="OOS validation">
                {draft.validation_evidence
                  ? `#${draft.validation_evidence.validation_id} | excess ${((draft.validation_evidence.aggregate.excess_return ?? 0) * 100).toFixed(2)}%`
                  : "Not attached"}
              </Descriptions.Item>
              <Descriptions.Item label="决策日">{draft.as_of_date}</Descriptions.Item>
              <Descriptions.Item label="最早人工执行日">{draft.earliest_execution_date || "数据不足"}</Descriptions.Item>
              <Descriptions.Item label="账户估值">{formatAmount(draft.total_equity)}</Descriptions.Item>
              <Descriptions.Item label="价格口径" span={2}>研究收盘价，仅供研究</Descriptions.Item>
              <Descriptions.Item label="人工确认">必须确认后才可进入模拟盘流程</Descriptions.Item>
            </Descriptions>
            {draft.warnings.map((warning) => <Alert key={warning} message={warning} showIcon type="warning" style={{ marginTop: 10 }} />)}
          </Card>

          <Card className="panel" title="证据快照">
            <Descriptions column={{ xs: 1, sm: 2, lg: 4 }} size="small">
              <Descriptions.Item label="市场状态">
                {draft.market_evidence.available
                  ? <Tag color="blue">{draft.market_evidence.regime}</Tag>
                  : <Tag>本地指数数据不足</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="市场置信度">
                {draft.market_evidence.confidence === null ? "-" : `${(draft.market_evidence.confidence * 100).toFixed(1)}%`}
              </Descriptions.Item>
              <Descriptions.Item label="市场数据截至">
                {draft.market_evidence.data_end_date || "-"}
              </Descriptions.Item>
              <Descriptions.Item label="新闻窗口">
                {`${dayjs(draft.news_evidence.window_start).format("MM-DD HH:mm")} 至 ${dayjs(draft.news_evidence.as_of_at).format("MM-DD HH:mm")}`}
              </Descriptions.Item>
              <Descriptions.Item label="严重公司风险">
                <Tag color={draft.news_evidence.severe_company_risk_count > 0 ? "red" : "default"}>{draft.news_evidence.severe_company_risk_count}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="一般公司风险">
                <Tag color={draft.news_evidence.company_risk_count > 0 ? "orange" : "default"}>{draft.news_evidence.company_risk_count}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="可见新闻条数">
                {draft.news_evidence.total_items}
              </Descriptions.Item>
              <Descriptions.Item label="新闻可用性">截至决策日已观测</Descriptions.Item>
            </Descriptions>
            {draft.market_evidence.warning && <Alert message={draft.market_evidence.warning} showIcon type="warning" style={{ marginTop: 10 }} />}
            {draft.market_evidence.reasons.length > 0 && (
              <Text type="secondary" style={{ display: "block", marginTop: 10 }}>
                {draft.market_evidence.reasons.join(" | ")}
              </Text>
            )}
            <Divider orientation="left" plain>因子快照</Divider>
            <Text type="secondary">
              截至 {draft.factor_evidence.data_end_date || draft.factor_evidence.as_of_date} 的价格和成交量滚动变换；不包含未来收益、IC 或历史有效性结论。
            </Text>
            {draft.factor_evidence.warnings.map((warning) => (
              <Alert key={warning} message={warning} showIcon type="info" style={{ marginTop: 10 }} />
            ))}
            <Table
              columns={[
                { title: "代码", dataIndex: "symbol", width: 100 },
                {
                  title: "因子原始值",
                  render: (_, row: FactorSymbolEvidence) => row.values.map((value) => (
                    <Tag key={value.name} color={value.direction > 0 ? "blue" : "default"} style={{ marginBottom: 4 }}>
                      {`${value.name}: ${formatFactorValue(value.raw_value)}`}
                    </Tag>
                  ))
                },
                { title: "状态", dataIndex: "warning", width: 220, render: (value: string | null) => value || "可用" }
              ]}
              dataSource={draft.factor_evidence.symbols}
              locale={{ emptyText: "没有可用于该决策日的因子快照" }}
              pagination={false}
              rowKey="symbol"
              size="small"
              style={{ marginTop: 12 }}
            />
            <Divider orientation="left" plain>新闻证据</Divider>
            <Table
              columns={[
                { title: "代码", dataIndex: "symbol", width: 100, render: (value: string | null) => value || "市场" },
                { title: "事件", dataIndex: "event_type", width: 150 },
                { title: "标题", dataIndex: "title" },
                { title: "已知时间", dataIndex: "known_at", width: 180, render: (value: string) => dayjs(value).format("YYYY-MM-DD HH:mm") }
              ]}
              dataSource={draft.news_evidence.items}
              locale={{ emptyText: "决策日前 7 天没有已观测的相关新闻" }}
              pagination={false}
              rowKey={(row) => `${row.source}-${row.title}-${row.known_at}`}
              size="small"
              style={{ marginTop: 16 }}
            />
          </Card>

          <Card className="panel" title="风控后目标权重">
            <Table
              columns={[
                { title: "代码", dataIndex: "symbol" },
                { title: "目标权重", dataIndex: "weight", align: "right", render: (value: number) => `${(value * 100).toFixed(2)}%` }
              ]}
              dataSource={targetWeightRows}
              locale={{ emptyText: "风控后没有可配置的目标权重" }}
              pagination={false}
              rowKey="symbol"
              size="small"
            />
            {rejectedWeightRows.length > 0 && (
              <Table
                columns={[
                  { title: "已拒绝代码", dataIndex: "symbol" },
                  { title: "风控原因", dataIndex: "reason" }
                ]}
                dataSource={rejectedWeightRows}
                pagination={false}
                rowKey="symbol"
                size="small"
                style={{ marginTop: 16 }}
              />
            )}
          </Card>

          <Card className="panel" title="下一交易日调仓草案">
            <Table columns={tradeColumns} dataSource={draft.trade_plan} locale={{ emptyText: "当前没有满足条件的调仓草案" }} pagination={{ pageSize: 10 }} rowKey={(row) => `${row.symbol}-${row.side}`} />
          </Card>

          <Card
            className="panel"
            title="LLM 解释与风险提示"
            extra={
              <Button
                disabled={!draft.remote_llm_enabled || streaming}
                icon={<Sparkles size={16} />}
                loading={streaming}
                onClick={streamExplanation}
                type="primary"
              >
                生成解释
              </Button>
            }
          >
            {!draft.remote_llm_enabled && <Text type="secondary">本草案未获得远程 LLM 授权或服务端未配置模型，因此不会生成模型解释。</Text>}
            {draft.llm_summary && !explanation && <Text>{draft.llm_summary}</Text>}
            {explanation && <div className="advisoryExplanation"><Bot size={18} /><Text>{explanation}</Text></div>}
          </Card>
        </Space>
      )}
    </section>
  );
}
