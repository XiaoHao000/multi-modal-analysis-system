import { Card, Table, Space, Tag, Button, Typography, Popconfirm } from "antd";
import {
  HistoryOutlined,
  EyeOutlined,
  DeleteOutlined,
} from "@ant-design/icons";
import type { AnalyzeResponse } from "../types";

interface HistoryItem {
  query: string;
  result: AnalyzeResponse;
  sql: string;
  time: string;
}

interface Props {
  history: HistoryItem[];
  onSelect: (item: HistoryItem) => void;
  onClear: () => void;
}

export default function HistoryPage({ history, onSelect, onClear }: Props) {
  const columns = [
    {
      title: "查询时间",
      dataIndex: "time",
      key: "time",
      width: 160,
      render: (t: string) => (
        <Typography.Text style={{ fontSize: 12 }}>{t}</Typography.Text>
      ),
    },
    {
      title: "问题",
      dataIndex: "query",
      key: "query",
      ellipsis: true,
    },
    {
      title: "状态",
      key: "status",
      width: 100,
      render: (_: unknown, r: HistoryItem) =>
        r.result.success ? (
          <Tag color="success">成功</Tag>
        ) : (
          <Tag color="error">失败</Tag>
        ),
    },
    {
      title: "操作",
      key: "action",
      width: 120,
      render: (_: unknown, r: HistoryItem) => (
        <Space>
          <Button
            type="link"
            size="small"
            icon={<EyeOutlined />}
            onClick={() => onSelect(r)}
          >
            查看
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title={
        <Space>
          <HistoryOutlined />
          历史分析记录
        </Space>
      }
      extra={
        history.length > 0 && (
          <Popconfirm
            title="确定清空所有历史记录？"
            onConfirm={onClear}
            okText="确定"
            cancelText="取消"
          >
            <Button size="small" icon={<DeleteOutlined />} danger>
              清空全部
            </Button>
          </Popconfirm>
        )
      }
    >
      {history.length === 0 ? (
        <div style={{ textAlign: "center", padding: 48 }}>
          <Typography.Text type="secondary">
            暂无历史记录，去分析看板提交一次分析吧
          </Typography.Text>
        </div>
      ) : (
        <Table
          dataSource={history}
          columns={columns}
          rowKey="time"
          size="middle"
          pagination={{ pageSize: 10 }}
        />
      )}
    </Card>
  );
}
