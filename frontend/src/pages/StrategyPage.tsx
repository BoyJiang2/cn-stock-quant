import { Table, Tag, message } from "antd";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { StrategyItem } from "../types";

export function StrategyPage() {
  const [strategies, setStrategies] = useState<StrategyItem[]>([]);

  useEffect(() => {
    api
      .get<StrategyItem[]>("/api/strategies")
      .then((response) => setStrategies(response.data))
      .catch(() => message.error("策略列表加载失败"));
  }, []);

  return (
    <section className="page">
      <h1>策略管理</h1>
      <Table
        columns={[
          { title: "策略 ID", dataIndex: "name" },
          { title: "名称", dataIndex: "display_name" },
          { title: "说明", dataIndex: "description" },
          {
            title: "参数",
            render: (_, row) => row.parameters.map((parameter) => parameter.label).join(" / ")
          },
          {
            title: "状态",
            render: (_, row) => (
              <Tag color={row.source === "builtin" ? "green" : "blue"}>
                {row.source === "builtin" ? "内置" : "用户"}
              </Tag>
            )
          }
        ]}
        dataSource={strategies}
        pagination={false}
        rowKey="name"
      />
    </section>
  );
}
