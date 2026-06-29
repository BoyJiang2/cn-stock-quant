import { Alert, Button, DatePicker, Input, InputNumber, Progress, Table, Tag, message } from "antd";
import type { RangePickerProps } from "antd/es/date-picker";
import dayjs from "dayjs";
import { RefreshCw, Search } from "lucide-react";
import type { ChangeEvent } from "react";
import { useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import type {
  BatchDailySyncItem,
  BatchDailySyncResponse,
  DailyStatus,
  DailySyncResponse,
  DataDiagnostics,
  DataQualityReport,
  FullMarketSyncResponse,
  ResearchSyncNextResponse,
  ResearchSyncProgress,
  Stock,
  SymbolStatus,
  SyncJob
} from "../types";

const { RangePicker } = DatePicker;

const BATCH_SEPARATOR = /[,，;；\s]+/;

function formatRange(start: string | null, end: string | null): string {
  if (start && end) return `${start} ~ ${end}`;
  return "无";
}

export function DataPage() {
  const [stocks, setStocks] = useState<Stock[]>([]);
  const [keyword, setKeyword] = useState("");
  const [symbol, setSymbol] = useState("000001");
  const [batchSymbols, setBatchSymbols] = useState("000001,600000");
  const [dailyStatus, setDailyStatus] = useState<DailyStatus[]>([]);
  const [syncJobs, setSyncJobs] = useState<SyncJob[]>([]);
  const [diagnostics, setDiagnostics] = useState<DataDiagnostics | null>(null);
  const [diagnosis, setDiagnosis] = useState<SymbolStatus | null>(null);
  const [range, setRange] = useState<[dayjs.Dayjs, dayjs.Dayjs]>([
    dayjs().subtract(2, "year"),
    dayjs()
  ]);
  const [loading, setLoading] = useState(false);
  const [diagLoading, setDiagLoading] = useState(false);
  const [researchBatchSize, setResearchBatchSize] = useState(20);
  const [researchProgress, setResearchProgress] = useState<ResearchSyncProgress | null>(null);
  const [researchItems, setResearchItems] = useState<BatchDailySyncItem[]>([]);
  const [researchAutoRun, setResearchAutoRun] = useState(false);
  const [researchLoading, setResearchLoading] = useState(false);
  const [fullMarketProgress, setFullMarketProgress] = useState<ResearchSyncProgress | null>(null);
  const [fullMarketItems, setFullMarketItems] = useState<BatchDailySyncItem[]>([]);
  const [fullMarketLoading, setFullMarketLoading] = useState(false);
  const [fullMarketAutoRun, setFullMarketAutoRun] = useState(false);
  const [qualityReport, setQualityReport] = useState<DataQualityReport | null>(null);

  const researchAutoRunRef = useRef(false);
  const fullMarketAutoRunRef = useRef(false);
  const rangeRef = useRef(range);
  const batchSizeRef = useRef(researchBatchSize);
  useEffect(() => {
    rangeRef.current = range;
  }, [range]);
  useEffect(() => {
    batchSizeRef.current = researchBatchSize;
  }, [researchBatchSize]);
  useEffect(() => {
    researchAutoRunRef.current = researchAutoRun;
  }, [researchAutoRun]);
  useEffect(() => {
    fullMarketAutoRunRef.current = fullMarketAutoRun;
  }, [fullMarketAutoRun]);

  const handleRangeChange: RangePickerProps["onChange"] = (dates) => {
    if (dates?.[0] && dates[1]) {
      setRange([dates[0], dates[1]]);
    }
  };

  const loadStocks = async () => {
    const response = await api.get<Stock[]>("/api/data/stocks", { params: { keyword, limit: 200 } });
    setStocks(response.data);
  };

  const loadDataStatus = async () => {
    const [statusResponse, jobsResponse] = await Promise.all([
      api.get<DailyStatus[]>("/api/data/daily/status", { params: { limit: 200 } }),
      api.get<SyncJob[]>("/api/data/sync/jobs", { params: { limit: 50 } })
    ]);
    setDailyStatus(statusResponse.data);
    setSyncJobs(jobsResponse.data);
  };

  const loadDiagnostics = async () => {
    setDiagLoading(true);
    try {
      const response = await api.get<DataDiagnostics>("/api/data/diagnostics");
      setDiagnostics(response.data);
    } catch {
      message.error("加载数据概览失败");
    } finally {
      setDiagLoading(false);
    }
  };

  const diagnoseSymbol = async () => {
    if (!symbol.trim()) {
      message.warning("请输入股票代码");
      return;
    }
    setDiagLoading(true);
    try {
      const response = await api.get<SymbolStatus>("/api/data/symbol-status", {
        params: { symbol }
      });
      setDiagnosis(response.data);
    } catch (error: any) {
      message.error(error.response?.data?.detail || "诊断失败");
      setDiagnosis(null);
    } finally {
      setDiagLoading(false);
    }
  };

  const refreshAll = async () => {
    await Promise.all([loadStocks(), loadDataStatus(), loadDiagnostics()]);
  };

  useEffect(() => {
    loadStocks().catch(() => undefined);
    loadDataStatus().catch(() => undefined);
    loadDiagnostics().catch(() => undefined);
    loadResearchProgress().catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (researchAutoRun) {
      return;
    }
    loadResearchProgress().catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range]);

  const syncStocks = async () => {
    setLoading(true);
    try {
      const response = await api.post("/api/data/sync/stocks");
      message.success(`已同步 ${response.data.synced} 只股票`);
      await Promise.all([loadStocks(), loadDiagnostics()]);
    } catch {
      message.error("同步股票列表失败");
    } finally {
      setLoading(false);
    }
  };

  const syncCalendar = async () => {
    setFullMarketLoading(true);
    try {
      const response = await api.post("/api/data/sync/calendar");
      message.success(`交易日历已同步 ${response.data.synced} 天`);
    } catch (error: any) {
      message.error(error.response?.data?.detail || "交易日历同步失败");
    } finally {
      setFullMarketLoading(false);
    }
  };

  const loadFullMarketProgress = async () => {
    const response = await api.get<ResearchSyncProgress>("/api/data/sync/full-market/progress", {
      params: {
        start_date: rangeRef.current[0].format("YYYY-MM-DD"),
        end_date: rangeRef.current[1].format("YYYY-MM-DD")
      }
    });
    setFullMarketProgress(response.data);
  };

  const postFullMarketNext = async (): Promise<FullMarketSyncResponse> => {
    const response = await api.post<FullMarketSyncResponse>("/api/data/sync/full-market/next", {
      start_date: rangeRef.current[0].format("YYYY-MM-DD"),
      end_date: rangeRef.current[1].format("YYYY-MM-DD"),
      batch_size: batchSizeRef.current,
      max_failures: 3,
      min_request_interval: 0.35,
      adjust: "qfq",
      retry_failed: false
    });
    setFullMarketProgress(response.data.progress);
    setFullMarketItems(response.data.items);
    return response.data;
  };

  const syncFullMarketNextOnce = async () => {
    setFullMarketLoading(true);
    try {
      const result = await postFullMarketNext();
      if (result.progress.remaining === 0) {
        message.success("全市场同步任务已完成");
      } else {
        message.success(`处理 ${result.processed} 只，剩余 ${result.progress.remaining}`);
      }
    } catch (error: any) {
      message.error(error.response?.data?.detail || "全市场同步失败");
    } finally {
      setFullMarketLoading(false);
    }
  };

  const loadQualityReport = async () => {
    setFullMarketLoading(true);
    try {
      const response = await api.get<DataQualityReport>("/api/data/quality", {
        params: {
          start_date: rangeRef.current[0].format("YYYY-MM-DD"),
          end_date: rangeRef.current[1].format("YYYY-MM-DD"),
          limit: 200
        }
      });
      setQualityReport(response.data);
    } catch (error: any) {
      message.error(error.response?.data?.detail || "质量报告加载失败");
    } finally {
      setFullMarketLoading(false);
    }
  };

  useEffect(() => {
    if (!fullMarketAutoRun) return;
    let cancelled = false;
    void (async () => {
      while (!cancelled && fullMarketAutoRunRef.current) {
        setFullMarketLoading(true);
        try {
          const result = await postFullMarketNext();
          if (
            result.progress.remaining === 0 ||
            result.completed ||
            result.blocked ||
            result.processed === 0
          ) {
            if (result.blocked) {
              message.warning("剩余证券已触发失败熔断，请检查日志后手动重试");
            }
            fullMarketAutoRunRef.current = false;
            setFullMarketAutoRun(false);
            break;
          }
        } catch (error: any) {
          message.error(error.response?.data?.detail || "全市场同步失败");
          fullMarketAutoRunRef.current = false;
          setFullMarketAutoRun(false);
          break;
        } finally {
          if (!cancelled) setFullMarketLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fullMarketAutoRun]);

  const syncDaily = async () => {
    setLoading(true);
    try {
      const response = await api.post<DailySyncResponse>("/api/data/sync/daily", {
        symbol,
        start_date: range[0].format("YYYY-MM-DD"),
        end_date: range[1].format("YYYY-MM-DD"),
        adjust: "qfq"
      });
      const { symbol: sym, synced, status } = response.data;
      if (status === "empty") {
        message.warning(`${sym} 该区间无日线数据`);
      } else if (status === "cached") {
        message.info(`${sym} 使用本地缓存 ${synced} 条日线`);
      } else {
        message.success(`${sym} 已同步 ${synced} 条日线`);
      }
      await Promise.all([loadDataStatus(), loadDiagnostics()]);
    } catch (error: any) {
      message.error(error.response?.data?.detail || "同步日线失败");
    } finally {
      setLoading(false);
    }
  };

  const syncDailyBatch = async () => {
    const symbols = batchSymbols
      .split(BATCH_SEPARATOR)
      .map((item) => item.trim())
      .filter(Boolean);
    if (!symbols.length) {
      message.warning("请输入股票代码");
      return;
    }

    setLoading(true);
    try {
      const response = await api.post<BatchDailySyncResponse>("/api/data/sync/daily/batch", {
        symbols,
        start_date: range[0].format("YYYY-MM-DD"),
        end_date: range[1].format("YYYY-MM-DD"),
        adjust: "qfq"
      });
      message.success(`批量同步完成：成功 ${response.data.success}，失败 ${response.data.failed}`);
      await Promise.all([loadDataStatus(), loadDiagnostics()]);
    } catch (error: any) {
      message.error(error.response?.data?.detail || "批量同步失败");
    } finally {
      setLoading(false);
    }
  };

  const loadResearchProgress = async () => {
    try {
      const response = await api.get<ResearchSyncProgress>("/api/data/sync/research/progress", {
        params: {
          start_date: rangeRef.current[0].format("YYYY-MM-DD"),
          end_date: rangeRef.current[1].format("YYYY-MM-DD")
        }
      });
      setResearchProgress(response.data);
    } catch {
      message.error("加载研究池覆盖进度失败");
    }
  };

  const postResearchNext = async (): Promise<ResearchSyncNextResponse> => {
    const response = await api.post<ResearchSyncNextResponse>("/api/data/sync/research/next", {
      start_date: rangeRef.current[0].format("YYYY-MM-DD"),
      end_date: rangeRef.current[1].format("YYYY-MM-DD"),
      batch_size: batchSizeRef.current
    });
    setResearchProgress(response.data.progress);
    setResearchItems(response.data.items);
    await loadDataStatus();
    return response.data;
  };

  const syncResearchNextOnce = async () => {
    if (researchAutoRun) {
      return;
    }
    setResearchLoading(true);
    try {
      const { success, progress } = await postResearchNext();
      if (progress.remaining === 0) {
        message.success("研究股票池已全部覆盖");
      } else if (success === 0) {
        message.warning("本批无成功同步，可能存在失败循环，请检查后重试");
      } else {
        message.success(`本批同步 ${success} 只，剩余 ${progress.remaining}`);
      }
    } catch (error: any) {
      message.error(error.response?.data?.detail || "研究池同步失败");
    } finally {
      setResearchLoading(false);
    }
  };

  const toggleResearchAuto = () => {
    if (researchAutoRun) {
      researchAutoRunRef.current = false;
      setResearchAutoRun(false);
      setResearchLoading(false);
    } else {
      researchAutoRunRef.current = true;
      setResearchAutoRun(true);
    }
  };

  useEffect(() => {
    if (!researchAutoRun) {
      return;
    }
    let cancelled = false;
    void (async () => {
      while (!cancelled && researchAutoRunRef.current) {
        setResearchLoading(true);
        let stop = false;
        try {
          const { success, progress } = await postResearchNext();
          if (cancelled || !researchAutoRunRef.current) {
            break;
          }
          if (progress.remaining === 0) {
            stop = true;
            message.success("研究股票池已全部覆盖");
          } else if (success === 0) {
            stop = true;
            message.warning("本批无成功同步，已自动停止以防失败循环");
          }
        } catch (error: any) {
          if (cancelled || !researchAutoRunRef.current) {
            break;
          }
          stop = true;
          message.error(error.response?.data?.detail || "研究池同步失败");
        } finally {
          if (!cancelled) {
            setResearchLoading(false);
          }
        }
        if (stop || cancelled || !researchAutoRunRef.current) {
          researchAutoRunRef.current = false;
          setResearchAutoRun(false);
          break;
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [researchAutoRun]);

  const renderDiagnosis = () => {
    if (!diagnosis) {
      return null;
    }
    if (!diagnosis.stock_exists) {
      return (
        <Alert
          type="error"
          showIcon
          message={`证券不存在：${diagnosis.symbol}`}
          description="未在本地股票表中找到该代码，请先同步股票列表或检查代码是否正确。"
        />
      );
    }
    if (!diagnosis.has_daily_bars) {
      return (
        <Alert
          type="warning"
          showIcon
          message={`存在但无行情：${diagnosis.symbol} ${diagnosis.name ?? ""}`}
          description={`交易所 ${diagnosis.exchange ?? "-"}，本地暂无日线数据，请同步该股票的日线。`}
        />
      );
    }
    return (
      <Alert
        type="success"
        showIcon
        message={`区间覆盖：${diagnosis.symbol} ${diagnosis.name ?? ""}`}
        description={
          <>
            <Tag color="green">{diagnosis.exchange ?? "-"}</Tag>
            区间 {diagnosis.start_date} ~ {diagnosis.end_date}，共 {diagnosis.bar_count} 条日线
          </>
        }
      />
    );
  };

  return (
    <section className="page">
      <h1>数据中心</h1>
      <div className="panel">
        <div className="panelTitle">
          数据概览{" "}
          <Button size="small" loading={diagLoading} onClick={loadDiagnostics}>
            刷新概览
          </Button>
        </div>
        <div className="metricGrid">
          <div className="metricCard">
            <span>股票数</span>
            <strong>{diagnostics?.stock_count ?? "-"}</strong>
          </div>
          <div className="metricCard">
            <span>日线条数</span>
            <strong>{diagnostics?.bar_count ?? "-"}</strong>
          </div>
          <div className="metricCard">
            <span>有行情证券</span>
            <strong>{diagnostics?.symbols_with_bars ?? "-"}</strong>
          </div>
          <div className="metricCard">
            <span>行情区间</span>
            <strong>
              {diagnostics ? formatRange(diagnostics.start_date, diagnostics.end_date) : "-"}
            </strong>
          </div>
        </div>
        {diagnostics && (
          <div
            style={{
              marginTop: 10,
              color: "#607086",
              fontSize: 13,
              wordBreak: "break-all"
            }}
          >
            数据库：{diagnostics.database}
          </div>
        )}
      </div>
      <div className="panel">
        <div className="panelTitle">单股诊断</div>
        <div className="toolbar">
          <Input
            onChange={(event: ChangeEvent<HTMLInputElement>) => setSymbol(event.target.value)}
            placeholder="股票代码"
            style={{ width: 180 }}
            value={symbol}
          />
          <Button loading={diagLoading} onClick={diagnoseSymbol} type="primary">
            诊断
          </Button>
        </div>
        {renderDiagnosis()}
      </div>
      <div className="toolbar">
        <Button icon={<RefreshCw size={16} />} loading={loading} onClick={syncStocks} type="primary">
          同步股票列表
        </Button>
        <Input
          allowClear
          onChange={(event: ChangeEvent<HTMLInputElement>) => setKeyword(event.target.value)}
          onPressEnter={loadStocks}
          placeholder="代码或名称"
          prefix={<Search size={16} />}
          value={keyword}
        />
        <Button onClick={loadStocks}>查询</Button>
        <Button onClick={refreshAll}>刷新全部</Button>
      </div>
      <div className="toolbar">
        <Input
          onChange={(event: ChangeEvent<HTMLInputElement>) => setSymbol(event.target.value)}
          style={{ width: 180 }}
          value={symbol}
        />
        <RangePicker onChange={handleRangeChange} value={range} />
        <Button loading={loading} onClick={syncDaily}>
          同步日线
        </Button>
      </div>
      <div className="toolbar">
        <Input.TextArea
          autoSize={{ minRows: 1, maxRows: 3 }}
          onChange={(event) => setBatchSymbols(event.target.value)}
          placeholder="多个代码用逗号、分号、空格或换行分隔"
          value={batchSymbols}
        />
        <Button loading={loading} onClick={syncDailyBatch}>
          批量同步日线
        </Button>
        <Button onClick={loadDataStatus}>刷新状态</Button>
      </div>
      <div className="panel">
        <div className="panelTitle">全 A 股同步（沪深北，含风险证券）</div>
        <div className="toolbar">
          <RangePicker onChange={handleRangeChange} value={range} />
          <Button loading={fullMarketLoading} onClick={syncCalendar}>
            同步交易日历
          </Button>
          <Button
            disabled={fullMarketAutoRun}
            loading={fullMarketLoading && !fullMarketAutoRun}
            onClick={syncFullMarketNextOnce}
          >
            同步下一批
          </Button>
          <Button
            danger={fullMarketAutoRun}
            loading={fullMarketLoading && fullMarketAutoRun}
            onClick={() => {
              const next = !fullMarketAutoRun;
              fullMarketAutoRunRef.current = next;
              setFullMarketAutoRun(next);
            }}
            type={fullMarketAutoRun ? "default" : "primary"}
          >
            {fullMarketAutoRun ? "停止全市场同步" : "自动同步全市场"}
          </Button>
          <Button onClick={loadFullMarketProgress}>刷新进度</Button>
          <Button onClick={loadQualityReport}>质量报告</Button>
        </div>
        {fullMarketProgress && (
          <div style={{ marginTop: 12 }}>
            <Progress
              format={() => `${fullMarketProgress.covered}/${fullMarketProgress.total}`}
              percent={Math.round(fullMarketProgress.percent)}
              status={fullMarketProgress.remaining === 0 ? "success" : "active"}
            />
            <div style={{ marginTop: 6, color: "#607086", fontSize: 13 }}>
              全市场 {fullMarketProgress.total}　已处理 {fullMarketProgress.covered}　剩余{" "}
              {fullMarketProgress.remaining}
            </div>
          </div>
        )}
        {qualityReport && (
          <Alert
            message={`交易日 ${qualityReport.expected_trading_days}，检查 ${qualityReport.symbols_checked} 只，完整 ${qualityReport.symbols_fully_covered} 只`}
            description={`${qualityReport.warning} 缺口合计 ${qualityReport.total_missing_bars} 条。`}
            showIcon
            style={{ marginTop: 12 }}
            type={qualityReport.symbols_with_gaps ? "warning" : "success"}
          />
        )}
        {fullMarketItems.length > 0 && (
          <Table
            columns={[
              { title: "代码", dataIndex: "symbol" },
              { title: "状态", dataIndex: "status" },
              { title: "同步数", dataIndex: "synced" },
              { title: "消息", dataIndex: "message" }
            ]}
            dataSource={fullMarketItems}
            pagination={{ pageSize: 8 }}
            rowKey="symbol"
            size="small"
            style={{ marginTop: 12 }}
          />
        )}
      </div>
      <div className="panel">
        <div className="panelTitle">研究股票池同步（沪深，排除 ST/退）</div>
        <div className="toolbar">
          <RangePicker onChange={handleRangeChange} value={range} />
          <InputNumber
            addonBefore="批大小"
            max={50}
            min={1}
            onChange={(value) => setResearchBatchSize(typeof value === "number" ? value : 20)}
            style={{ width: 150 }}
            value={researchBatchSize}
          />
          <Button
            disabled={researchAutoRun}
            loading={researchLoading && !researchAutoRun}
            onClick={syncResearchNextOnce}
          >
            同步下一批
          </Button>
          <Button
            danger={researchAutoRun}
            loading={researchLoading && researchAutoRun}
            onClick={toggleResearchAuto}
            type={researchAutoRun ? "default" : "primary"}
          >
            {researchAutoRun ? "停止自动" : "自动连跑"}
          </Button>
          <Button disabled={researchAutoRun} onClick={loadResearchProgress}>
            刷新进度
          </Button>
        </div>
        {researchProgress && (
          <div style={{ marginTop: 12 }}>
            <Progress
              format={() => `${researchProgress.covered}/${researchProgress.total}`}
              percent={Math.round(researchProgress.percent)}
              status={researchProgress.remaining === 0 ? "success" : "active"}
            />
            <div style={{ marginTop: 6, color: "#607086", fontSize: 13 }}>
              总数 {researchProgress.total}　已覆盖 {researchProgress.covered}　剩余{" "}
              {researchProgress.remaining}　覆盖率 {researchProgress.percent}%
            </div>
          </div>
        )}
        {researchItems.length > 0 && (
          <Table
            columns={[
              { title: "代码", dataIndex: "symbol" },
              { title: "状态", dataIndex: "status" },
              { title: "同步数", dataIndex: "synced" },
              { title: "消息", dataIndex: "message" }
            ]}
            dataSource={researchItems}
            pagination={{ pageSize: 8 }}
            rowKey="symbol"
            size="small"
            style={{ marginTop: 12 }}
          />
        )}
      </div>
      <Table
        columns={[
          { title: "代码", dataIndex: "symbol" },
          { title: "名称", dataIndex: "name" },
          { title: "交易所", dataIndex: "exchange" },
          { title: "状态", dataIndex: "status" }
        ]}
        dataSource={stocks}
        pagination={{ pageSize: 12 }}
        rowKey="symbol"
        size="middle"
      />
      <div className="panel">
        <div className="panelTitle">日线数据状态</div>
        <Table
          columns={[
            { title: "代码", dataIndex: "symbol" },
            { title: "开始日期", dataIndex: "start_date" },
            { title: "结束日期", dataIndex: "end_date" },
            { title: "记录数", dataIndex: "bar_count" }
          ]}
          dataSource={dailyStatus}
          pagination={{ pageSize: 8 }}
          rowKey="symbol"
          size="small"
        />
      </div>
      <div className="panel">
        <div className="panelTitle">同步日志</div>
        <Table
          columns={[
            { title: "ID", dataIndex: "id", width: 72 },
            { title: "类型", dataIndex: "job_type" },
            { title: "目标", dataIndex: "target" },
            { title: "状态", dataIndex: "status" },
            { title: "开始", dataIndex: "start_date" },
            { title: "结束", dataIndex: "end_date" },
            { title: "记录", dataIndex: "records" },
            { title: "消息", dataIndex: "message" }
          ]}
          dataSource={syncJobs}
          pagination={{ pageSize: 8 }}
          rowKey="id"
          size="small"
        />
      </div>
    </section>
  );
}
