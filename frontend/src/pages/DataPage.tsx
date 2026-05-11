import { Button, DatePicker, Input, Table, message } from "antd";
import type { RangePickerProps } from "antd/es/date-picker";
import dayjs from "dayjs";
import { RefreshCw, Search } from "lucide-react";
import type { ChangeEvent } from "react";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { DailyStatus, Stock, SyncJob } from "../types";

const { RangePicker } = DatePicker;

export function DataPage() {
  const [stocks, setStocks] = useState<Stock[]>([]);
  const [keyword, setKeyword] = useState("");
  const [symbol, setSymbol] = useState("000001");
  const [batchSymbols, setBatchSymbols] = useState("000001,600000");
  const [dailyStatus, setDailyStatus] = useState<DailyStatus[]>([]);
  const [syncJobs, setSyncJobs] = useState<SyncJob[]>([]);
  const [range, setRange] = useState<[dayjs.Dayjs, dayjs.Dayjs]>([dayjs().subtract(2, "year"), dayjs()]);
  const [loading, setLoading] = useState(false);

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

  useEffect(() => {
    loadStocks().catch(() => undefined);
    loadDataStatus().catch(() => undefined);
  }, []);

  const syncStocks = async () => {
    setLoading(true);
    try {
      const response = await api.post("/api/data/sync/stocks");
      message.success(`已同步 ${response.data.synced} 只股票`);
      await loadStocks();
    } catch (error) {
      message.error("同步股票列表失败");
    } finally {
      setLoading(false);
    }
  };

  const syncDaily = async () => {
    setLoading(true);
    try {
      const response = await api.post("/api/data/sync/daily", {
        symbol,
        start_date: range[0].format("YYYY-MM-DD"),
        end_date: range[1].format("YYYY-MM-DD"),
        adjust: "qfq"
      });
      message.success(`${response.data.symbol} 已同步 ${response.data.synced} 条日线`);
      await loadDataStatus();
    } catch (error: any) {
      message.error(error.response?.data?.detail || "同步日线失败");
    } finally {
      setLoading(false);
    }
  };

  const syncDailyBatch = async () => {
    const symbols = batchSymbols
      .split(/[,，\s]+/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (!symbols.length) {
      message.warning("请输入股票代码");
      return;
    }

    setLoading(true);
    try {
      const response = await api.post("/api/data/sync/daily/batch", {
        symbols,
        start_date: range[0].format("YYYY-MM-DD"),
        end_date: range[1].format("YYYY-MM-DD"),
        adjust: "qfq"
      });
      message.success(`批量同步完成：成功 ${response.data.success}，失败 ${response.data.failed}`);
      await loadDataStatus();
    } catch (error: any) {
      message.error(error.response?.data?.detail || "批量同步失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="page">
      <h1>数据中心</h1>
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
      </div>
      <div className="toolbar">
        <Input onChange={(event: ChangeEvent<HTMLInputElement>) => setSymbol(event.target.value)} value={symbol} />
        <RangePicker onChange={handleRangeChange} value={range} />
        <Button loading={loading} onClick={syncDaily}>
          同步日线
        </Button>
      </div>
      <div className="toolbar">
        <Input.TextArea
          autoSize={{ minRows: 1, maxRows: 3 }}
          onChange={(event) => setBatchSymbols(event.target.value)}
          placeholder="多个代码用逗号、空格或换行分隔"
          value={batchSymbols}
        />
        <Button loading={loading} onClick={syncDailyBatch}>
          批量同步日线
        </Button>
        <Button onClick={loadDataStatus}>刷新状态</Button>
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
