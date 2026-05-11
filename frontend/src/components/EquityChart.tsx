import * as echarts from "echarts";
import { useEffect, useRef } from "react";

import type { EquityPoint } from "../types";

interface EquityChartProps {
  data: EquityPoint[];
}

export function EquityChart({ data }: EquityChartProps) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) {
      return;
    }

    const chart = echarts.init(ref.current);
    chart.setOption({
      animation: false,
      grid: [
        { left: 54, right: 24, top: 28, height: 190 },
        { left: 54, right: 24, top: 260, height: 92 }
      ],
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
          name: "权益",
          type: "line",
          data: data.map((point) => point.equity),
          smooth: true,
          symbol: "none",
          lineStyle: { color: "#13795b", width: 2 }
        },
        {
          name: "回撤",
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
  }, [data]);

  return <div className="chartPanel" ref={ref} />;
}
