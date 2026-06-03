import { Card, Descriptions, Table, Tag, Typography, Space } from "antd";
import {
  DatabaseOutlined,
  TableOutlined,
} from "@ant-design/icons";

const schemaData = {
  tables: [
    {
      name: "dim_account",
      desc: "会计科目维度表",
      rows: 14,
      columns: [
        { name: "account_id", type: "INTEGER", key: "PK, 自增", comment: "科目主键" },
        { name: "account_code", type: "VARCHAR(20)", key: "", comment: "科目编码：1001/1122/6001等" },
        { name: "account_name", type: "VARCHAR(200)", key: "", comment: "科目名称" },
        { name: "account_category", type: "VARCHAR(50)", key: "", comment: "类别：资产/负债/权益/收入/费用" },
        { name: "parent_account", type: "VARCHAR(20)", key: "", comment: "上级科目编码" },
      ],
    },
    {
      name: "dim_cost_center",
      desc: "成本中心维度表",
      rows: 6,
      columns: [
        { name: "cc_id", type: "INTEGER", key: "PK, 自增", comment: "成本中心主键" },
        { name: "cc_code", type: "VARCHAR(20)", key: "", comment: "成本中心编码" },
        { name: "cc_name", type: "VARCHAR(100)", key: "", comment: "名称：生产部/研发部/销售部等" },
        { name: "department", type: "VARCHAR(100)", key: "", comment: "所属部门" },
        { name: "cc_type", type: "VARCHAR(50)", key: "", comment: "类型：生产/研发/销售/管理" },
      ],
    },
    {
      name: "dim_date",
      desc: "时间维度表",
      rows: 9,
      columns: [
        { name: "date_id", type: "INTEGER", key: "PK, 自增", comment: "日期主键" },
        { name: "date", type: "VARCHAR(20)", key: "", comment: "日期字符串" },
        { name: "year", type: "INTEGER", key: "", comment: "年份" },
        { name: "quarter", type: "INTEGER", key: "", comment: "季度" },
        { name: "month", type: "INTEGER", key: "", comment: "月份" },
        { name: "is_month_end", type: "BOOLEAN", key: "", comment: "是否月末" },
      ],
    },
    {
      name: "fact_ledger",
      desc: "总账事实表（星型模型核心）",
      rows: 168,
      columns: [
        { name: "ledger_id", type: "INTEGER", key: "PK, 自增", comment: "分录主键" },
        { name: "date_id", type: "INTEGER", key: "FK → dim_date", comment: "日期外键" },
        { name: "account_id", type: "INTEGER", key: "FK → dim_account", comment: "科目外键" },
        { name: "cc_id", type: "INTEGER", key: "FK → dim_cost_center", comment: "成本中心外键" },
        { name: "voucher_no", type: "VARCHAR(50)", key: "", comment: "凭证号" },
        { name: "debit_amount", type: "NUMERIC(18,2)", key: "", comment: "借方金额" },
        { name: "credit_amount", type: "NUMERIC(18,2)", key: "", comment: "贷方金额" },
        { name: "summary", type: "TEXT", key: "", comment: "摘要" },
        { name: "period", type: "VARCHAR(10)", key: "", comment: "会计期间 YYYY-MM" },
      ],
    },
  ],
};

const tableColumns = [
  { title: "字段名", dataIndex: "name", key: "name", width: 140 },
  {
    title: "类型",
    dataIndex: "type",
    key: "type",
    width: 140,
    render: (t: string) => <Tag>{t}</Tag>,
  },
  {
    title: "约束",
    dataIndex: "key",
    key: "key",
    width: 160,
    render: (k: string) =>
      k ? (
        <Tag color={k.startsWith("PK") ? "blue" : "green"}>{k}</Tag>
      ) : null,
  },
  { title: "说明", dataIndex: "comment", key: "comment" },
];

export default function DataSourcePage() {
  return (
    <div>
      <Card
        title={
          <Space>
            <DatabaseOutlined />
            数据库概览
          </Space>
        }
        style={{ marginBottom: 24 }}
      >
        <Descriptions bordered size="small" column={3}>
          <Descriptions.Item label="数据库引擎">
            <Tag color="blue">PostgreSQL</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="数据模型">
            <Tag color="purple">星型模型（财务总账）</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="事实表行数">168 条</Descriptions.Item>
          <Descriptions.Item label="维度表数">3 张</Descriptions.Item>
          <Descriptions.Item label="数据范围">2025年 1-12月</Descriptions.Item>
          <Descriptions.Item label="科目覆盖">
            资产 / 负债 / 权益 / 收入 / 费用
          </Descriptions.Item>
          <Descriptions.Item label="成本中心">
            生产部 / 研发部 / 销售部 / 市场部 / 财务部 / 人事行政部
          </Descriptions.Item>
          <Descriptions.Item label="会计准则">
            中国企业会计准则（CAS），借贷必相等
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {schemaData.tables.map((t) => (
        <Card
          key={t.name}
          title={
            <Space>
              <TableOutlined />
              {t.name}
              <Tag>{t.desc}</Tag>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {t.rows} 行
              </Typography.Text>
            </Space>
          }
          style={{ marginBottom: 16 }}
        >
          <Table
            dataSource={t.columns}
            columns={tableColumns}
            rowKey="name"
            pagination={false}
            size="small"
          />
        </Card>
      ))}
    </div>
  );
}
