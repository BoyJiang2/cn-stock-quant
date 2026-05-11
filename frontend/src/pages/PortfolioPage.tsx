import { Table } from "antd";

export function PortfolioPage() {
  return (
    <section className="page">
      <h1>模拟盘</h1>
      <div className="panel">
        <div className="panelTitle">交易计划预览</div>
        <Table
          columns={[
            { title: "股票代码", dataIndex: "symbol" },
            { title: "股票名称", dataIndex: "name" },
            { title: "操作", dataIndex: "side" },
            { title: "目标仓位", dataIndex: "targetWeight" },
            { title: "建议股数", dataIndex: "quantity" },
            { title: "参考价", dataIndex: "price" }
          ]}
          dataSource={[]}
          pagination={false}
          rowKey="symbol"
        />
      </div>
    </section>
  );
}

