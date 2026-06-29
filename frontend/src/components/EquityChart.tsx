import * as echarts from "echarts";
import { useEffect, useRef } from "react";

import type { BenchmarkPoint, EquityPoint } from "../types";

interface EquityChartProps {
  data: EquityPoint[];
  benchmark?: BenchmarkPoint[];
}

export function EquityChart({ data, benchmark = [] }: EquityChartProps) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) {
      return;
    }

    const benchmarkByDate = new Map(benchmark.map((point) => [point.trade_date, point.equity]));
    const chart = echarts.init(ref.current);
    chart.setOption({
      animation: false,
      grid: [
        { left: 54, right: 24, top: 34, height: 184 },
        { left: 54, right: 24, top: 260, height: 92 }
      ],
      legend: { data: ["Equity", "Benchmark", "Drawdown"], top: 0 },
      tooltip: { trigger: "axis" },
      xAxis: [
        { type: "category", data: data.map((point) => point.trade_date), boundaryGap: false },
        { type: "category", data: data.map((point) => point.trade_date), boundaryGap: false, gridIndex: 1 }
      ],
      yAxis: [
        { type: "value", scale: true },
        {
          type: "value",
          gridIndex: 1,
          axisLabel: {
            formatter: (value: number) => `${(value * 100).toFixed(0)}%`
          }
        }
      ],
      series: [
        {
          name: "Equity",
          type: "line",
          data: data.map((point) => point.equity),
          smooth: true,
          symbol: "none",
          lineStyle: { color: "#13795b", width: 2 }
        },
        {
          name: "Benchmark",
          type: "line",
          data: data.map((point) => benchmarkByDate.get(point.trade_date) ?? null),
          smooth: true,
          symbol: "none",
          lineStyle: { color: "#475467", width: 1.8, type: "dashed" }
        },
        {
          name: "Drawdown",
          type: "line",
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: data.map((point) => point.drawdown),
          symbol: "none",
          areaStyle: { color: "rgba(180, 35, 24, 0.16)" },
          lineStyle: { color: "#b42318", width: 1.5 }
        }
      ]
    });

    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [data, benchmark]);

  return <div className="chartPanel" ref={ref} />;
}
