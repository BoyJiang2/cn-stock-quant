import { Table, Tag } from "antd";
import { useEffect, useState } from "react";

import { api } from "../api/client";

interface StrategyItem {
  name: string;
  display_name: string;
}

export function StrategyPage() {
  const [strategies, setStrategies] = useState<StrategyItem[]>([]);

  useEffect(() => {
    api.get<StrategyItem[]>("/api/strategies").then((response) => setStrategies(response.data));
  }, []);

  return (
    <section className="page">
      <h1>策略管理</h1>
      <Table
        columns={[
          { title: "策略 ID", dataIndex: "name" },
          { title: "名称", dataIndex: "display_name" },
          {
            title: "状态",
            render: () => <Tag color="green">内置</Tag>
          }
        ]}
        dataSource={strategies}
        pagination={false}
        rowKey="name"
      />
    </section>
  );
}

