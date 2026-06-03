import { useState } from "react";
import {
  Typography,
  Table,
  Collapse,
  Tag,
  Empty,
  Alert,
  Skeleton,
  Card,
  Row,
  Col,
  Statistic,
  Steps,
  Space,
  List,
  Button,
  Tooltip,
} from "antd";
import {
  AimOutlined,
  CodeOutlined,
  PlayCircleOutlined,
  BarChartOutlined,
  FileTextOutlined,
  FileImageOutlined,
  CopyOutlined,
  ClockCircleOutlined,
} from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import EChartsView from "./EChartsView";
import type { AnalyzeResponse } from "../types";

interface HistoryItem {
  query: string;
  result: AnalyzeResponse;
  sql: string;
  time: string;
}

interface Props {
  result: AnalyzeResponse | null;
  sql: string;
  progressSteps?: string[];
  history?: HistoryItem[];
  onSelectHistory?: (item: HistoryItem) => void;
}

const STEP_ICONS: Record<string, React.ReactNode> = {
  supervisor_router: <AimOutlined />,
  intent_agent: <AimOutlined />,
  modality_agent: <FileImageOutlined />,
  sql_agent: <CodeOutlined />,
  analysis_agent: <BarChartOutlined />,
  report_agent: <FileTextOutlined />,
};

const STEP_TITLES: Record<string, string> = {
  supervisor_router: "Supervisor 决策",
  intent_agent: "意图解析",
  modality_agent: "多模态处理(RAG+VL+OCR+PDF+语音)",
  sql_agent: "SQL 生成与执行",
  analysis_agent: "数据下钻分析",
  report_agent: "报告生成",
};

function getProgressCurrent(progressSteps: string[]): number {
  if (!progressSteps.length) return -1;
  const last = progressSteps[progressSteps.length - 1];
  const keys = Object.keys(STEP_TITLES);
  // 优先精确匹配（新 Agent 节点名含下划线，不会与标题混淆）
  const exactIdx = keys.indexOf(last);
  if (exactIdx !== -1) return exactIdx;
  // 回退到子串匹配（兼容旧 Pipeline 节点名和 supervisor router 追加的字符串）
  for (let i = 0; i < keys.length; i++) {
    if (last.includes(keys[i])) return i;
  }
  return progressSteps.length;
}

function computeStats(sqlResult: Record<string, unknown>[]) {
  if (!sqlResult.length) return null;
  const keys = Object.keys(sqlResult[0]);
  const numericKeys = keys.filter(
    (k) => typeof sqlResult[0][k] === "number"
  );
  const totalRow = sqlResult.length;
  const firstMetric = numericKeys[0];
  let total = 0;
  if (firstMetric) {
    total = sqlResult.reduce(
      (sum, row) => sum + (Number(row[firstMetric]) || 0),
      0
    );
  }
  return { rows: totalRow, total, metricName: firstMetric, categories: new Set(sqlResult.map((r) => String(r[keys[0]]))).size };
}

export default function ResultPanel({
  result,
  sql,
  progressSteps = [],
  history = [],
  onSelectHistory,
}: Props) {
  const [copied, setCopied] = useState(false);

  // Loading state — Skeleton
  if (!result) {
    if (progressSteps.length > 0) {
      const current = getProgressCurrent(progressSteps);
      return (
        <div>
          <Typography.Title level={4} style={{ marginBottom: 20 }}>
            分析进行中…
          </Typography.Title>
          <Steps
            direction="vertical"
            current={current}
            size="small"
            items={Object.entries(STEP_TITLES).map(([key, title]) => ({
              title,
              icon: STEP_ICONS[key],
            }))}
          />
          <Skeleton active paragraph={{ rows: 4 }} style={{ marginTop: 24 }} />
          <Row gutter={16} style={{ marginTop: 16 }}>
            {[1, 2, 3, 4].map((i) => (
              <Col span={6} key={i}>
                <Skeleton active paragraph={{ rows: 1 }} />
              </Col>
            ))}
          </Row>
        </div>
      );
    }

    // Empty with history
    return (
      <div>
        {history.length > 0 && onSelectHistory ? (
          <>
            <Row gutter={24}>
              <Col span={16}>
                <Empty
                  description={
                    <span>
                      输入问题并点击分析，AI 将为你解读数据
                      <br />
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        支持多模态：上传图表截图可获得更精准的分析
                      </Typography.Text>
                    </span>
                  }
                />
              </Col>
              <Col span={8}>
                <Card
                  size="small"
                  title={
                    <Space>
                      <ClockCircleOutlined />
                      最近分析
                    </Space>
                  }
                  style={{ maxHeight: 300, overflow: "auto" }}
                >
                  <List
                    size="small"
                    dataSource={history}
                    renderItem={(item) => (
                      <List.Item
                        style={{ cursor: "pointer", padding: "6px 0" }}
                        onClick={() => onSelectHistory(item)}
                      >
                        <Typography.Text
                          ellipsis
                          style={{ fontSize: 13 }}
                          title={item.query}
                        >
                          {item.query.slice(0, 25)}
                          {item.query.length > 25 ? "…" : ""}
                        </Typography.Text>
                        <Typography.Text
                          type="secondary"
                          style={{ fontSize: 11 }}
                        >
                          {item.time}
                        </Typography.Text>
                      </List.Item>
                    )}
                  />
                </Card>
              </Col>
            </Row>
          </>
        ) : (
          <Empty
            description={
              <span>
                输入问题并点击分析，AI 将为你解读数据
                <br />
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  试试：Q3 哪个品类毛利率最高？环比增长如何？
                </Typography.Text>
              </span>
            }
          />
        )}
      </div>
    );
  }

  // Error state
  if (!result.success) {
    return (
      <Alert
        type="error"
        message="分析失败"
        description={result.error || "未知错误"}
        showIcon
      />
    );
  }

  const stats = computeStats(result.sql_result);

  return (
    <div>
      {/* 统计概览卡片 */}
      {stats && (
        <Row gutter={16} style={{ marginBottom: 24 }}>
          <Col xs={24} sm={12} md={6}>
            <Card bordered={false} style={{ background: "#f8fafc" }}>
              <Statistic
                title="查询行数"
                value={stats.rows}
                suffix="行"
                valueStyle={{ color: "#4f46e5", fontSize: 24 }}
              />
            </Card>
          </Col>
          {stats.metricName && (
            <Col xs={24} sm={12} md={6}>
              <Card bordered={false} style={{ background: "#f8fafc" }}>
                <Statistic
                  title={`${stats.metricName} 合计`}
                  value={stats.total}
                  precision={0}
                  valueStyle={{ color: "#10b981", fontSize: 24 }}
                />
              </Card>
            </Col>
          )}
          <Col xs={24} sm={12} md={6}>
            <Card bordered={false} style={{ background: "#f8fafc" }}>
              <Statistic
                title="品类数"
                value={stats.categories}
                suffix="个"
                valueStyle={{ color: "#f59e0b", fontSize: 24 }}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} md={6}>
            <Card bordered={false} style={{ background: "#f8fafc" }}>
              <Statistic
                title="图表数量"
                value={result.charts.length}
                suffix="张"
                valueStyle={{ color: "#3b82f6", fontSize: 24 }}
              />
            </Card>
          </Col>
        </Row>
      )}

      {/* 分析报告 */}
      <Card
        title="分析报告"
        style={{ marginBottom: 16 }}
        styles={{ body: { padding: "16px 24px" } }}
      >
        <div
          className="markdown-content"
          style={{ lineHeight: 1.9, fontSize: 14 }}
        >
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {result.analysis}
          </ReactMarkdown>
        </div>
      </Card>

      {/* 图表 */}
      {result.charts.length > 0 && (
        <Card title="数据可视化" style={{ marginBottom: 16 }}>
          {result.charts.map((option, i) => (
            <div key={i} style={{ marginBottom: 12 }}>
              <EChartsView option={option} height={420} />
            </div>
          ))}
        </Card>
      )}

      {/* 执行追踪 + SQL + 原始数据 */}
      <Collapse
        style={{ marginTop: 16 }}
        items={[
          {
            key: "trace",
            label: (
              <Space>
                <BarChartOutlined />
                执行步骤追踪
              </Space>
            ),
            children: (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {result.trace.map((step, i) => (
                  <Tag key={i} color="blue" style={{ margin: 0 }}>
                    {step}
                  </Tag>
                ))}
              </div>
            ),
          },
          ...(sql
            ? [
                {
                  key: "sql",
                  label: (
                    <Space>
                      <CodeOutlined />
                      生成 SQL
                      <Tooltip title={copied ? "已复制" : "点击复制"}>
                        <Button
                          type="text"
                          size="small"
                          icon={<CopyOutlined />}
                          onClick={(e) => {
                            e.stopPropagation();
                            navigator.clipboard.writeText(sql);
                            setCopied(true);
                            setTimeout(() => setCopied(false), 2000);
                          }}
                        />
                      </Tooltip>
                    </Space>
                  ),
                  children: (
                    <pre
                      style={{
                        background: "#1e293b",
                        color: "#e2e8f0",
                        padding: 16,
                        borderRadius: 8,
                        overflow: "auto",
                        fontSize: 13,
                        lineHeight: 1.6,
                      }}
                    >
                      {sql}
                    </pre>
                  ),
                },
              ]
            : []),
          ...(result.sql_result.length > 0
            ? [
                {
                  key: "data",
                  label: (
                    <Space>
                      <PlayCircleOutlined />
                      查询数据（{result.sql_result.length} 行）
                    </Space>
                  ),
                  children: (
                    <Table
                      dataSource={result.sql_result.map((row, i) => ({
                        ...row,
                        _key: i,
                      }))}
                      columns={Object.keys(result.sql_result[0]).map((col) => ({
                        title: col,
                        dataIndex: col,
                        key: col,
                        ellipsis: true,
                      }))}
                      rowKey="_key"
                      size="small"
                      bordered
                      pagination={{ pageSize: 10, showSizeChanger: false }}
                      scroll={{ x: "max-content" }}
                    />
                  ),
                },
              ]
            : []),
        ]}
      />
    </div>
  );
}
