import * as echarts from "echarts";
import { useEffect, useMemo, useRef } from "react";

import type { DailyBar } from "../types";

interface KlineChartProps {
  data: DailyBar[];
}

export function KlineChart({ data }: KlineChartProps) {
  const ref = useRef<HTMLDivElement | null>(null);
  const dates = useMemo(() => data.map((item) => item.trade_date), [data]);
  const candles = useMemo(() => data.map((item) => [item.open, item.close, item.low, item.high]), [data]);
  const volumes = useMemo(() => data.map((item) => item.volume), [data]);
  const closes = useMemo(() => data.map((item) => item.close), [data]);

  useEffect(() => {
    if (!ref.current) {
      return;
    }

    const chart = echarts.init(ref.current);
    chart.setOption({
      animation: false,
      legend: {
        top: 8,
        left: 12,
        data: ["K线", "MA5", "MA10", "MA20", "成交量"]
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" }
      },
      axisPointer: {
        link: [{ xAxisIndex: "all" }]
      },
      grid: [
        { left: 58, right: 24, top: 42, height: 310 },
        { left: 58, right: 24, top: 390, height: 92 }
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1], start: 55, end: 100 },
        { type: "slider", xAxisIndex: [0, 1], bottom: 8, start: 55, end: 100 }
      ],
      xAxis: [
        { type: "category", data: dates, boundaryGap: true, axisLine: { onZero: false } },
        { type: "category", data: dates, gridIndex: 1, boundaryGap: true, axisLabel: { show: false } }
      ],
      yAxis: [
        { type: "value", scale: true, splitArea: { show: true } },
        { type: "value", gridIndex: 1, scale: true, splitNumber: 2 }
      ],
      series: [
        {
          name: "K线",
          type: "candlestick",
          data: candles,
          itemStyle: {
            color: "#d92d20",
            color0: "#039855",
            borderColor: "#d92d20",
            borderColor0: "#039855"
          }
        },
        lineSeries("MA5", movingAverage(closes, 5), "#2864b4"),
        lineSeries("MA10", movingAverage(closes, 10), "#8a5cf6"),
        lineSeries("MA20", movingAverage(closes, 20), "#d68000"),
        {
          name: "成交量",
          type: "bar",
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: volumes,
          itemStyle: { color: "#8aa0b8" }
        }
      ]
    });

    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [candles, closes, dates, volumes]);

  return <div className="klineChart" ref={ref} />;
}

function lineSeries(name: string, data: Array<number | null>, color: string) {
  return {
    name,
    type: "line",
    data,
    smooth: true,
    showSymbol: false,
    lineStyle: { width: 1.4, color }
  };
}

function movingAverage(values: number[], windowSize: number): Array<number | null> {
  return values.map((_, index) => {
    if (index + 1 < windowSize) {
      return null;
    }
    const slice = values.slice(index + 1 - windowSize, index + 1);
    return Number((slice.reduce((sum, value) => sum + value, 0) / windowSize).toFixed(4));
  });
}

