import { Button, DatePicker, Input, Table, message } from "antd";
import type { RangePickerProps } from "antd/es/date-picker";
import dayjs from "dayjs";
import { Search } from "lucide-react";
import type { ChangeEvent } from "react";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import { KlineChart } from "../components/KlineChart";
import type { DailyBar } from "../types";

const { RangePicker } = DatePicker;

export function KlinePage() {
  const [symbol, setSymbol] = useState("000001");
  const [range, setRange] = useState<[dayjs.Dayjs, dayjs.Dayjs]>([dayjs().subtract(1, "year"), dayjs()]);
  const [bars, setBars] = useState<DailyBar[]>([]);
  const [loading, setLoading] = useState(false);

  const handleRangeChange: RangePickerProps["onChange"] = (dates) => {
    if (dates?.[0] && dates[1]) {
      setRange([dates[0], dates[1]]);
    }
  };

  const loadBars = async () => {
    setLoading(true);
    try {
      const response = await api.get<DailyBar[]>("/api/data/daily", {
        params: {
          symbol,
          start_date: range[0].format("YYYY-MM-DD"),
          end_date: range[1].format("YYYY-MM-DD")
        }
      });
      setBars(response.data);
      if (!response.data.length) {
        message.warning("本地没有该区间日线，请先到数据中心同步");
      }
    } catch (error: any) {
      message.error(error.response?.data?.detail || "加载 K 线失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadBars().catch(() => undefined);
  }, []);

  return (
    <section className="page">
      <h1>K线查看</h1>
      <div className="toolbar">
        <Input
          onChange={(event: ChangeEvent<HTMLInputElement>) => setSymbol(event.target.value)}
          onPressEnter={loadBars}
          value={symbol}
        />
        <RangePicker onChange={handleRangeChange} value={range} />
        <Button icon={<Search size={16} />} loading={loading} onClick={loadBars} type="primary">
          查询
        </Button>
      </div>
      <KlineChart data={bars} />
      <div className="panel">
        <div className="panelTitle">日线数据</div>
        <Table
          columns={[
            { title: "日期", dataIndex: "trade_date" },
            { title: "开盘", dataIndex: "open" },
            { title: "最高", dataIndex: "high" },
            { title: "最低", dataIndex: "low" },
            { title: "收盘", dataIndex: "close" },
            { title: "成交量", dataIndex: "volume" },
            { title: "成交额", dataIndex: "amount" }
          ]}
          dataSource={bars}
          pagination={{ pageSize: 10 }}
          rowKey={(row) => `${row.symbol}-${row.trade_date}`}
          size="small"
        />
      </div>
    </section>
  );
}

