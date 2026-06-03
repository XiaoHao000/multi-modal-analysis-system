import { Card, Descriptions, Tag, Switch, Space, Typography, Divider } from "antd";
import {
  SettingOutlined,
  SafetyOutlined,
  ApiOutlined,
} from "@ant-design/icons";

interface Props {
  useStreaming: boolean;
  onStreamingChange: (v: boolean) => void;
}

export default function SettingsPage({ useStreaming, onStreamingChange }: Props) {
  return (
    <div>
      {/* 系统配置 */}
      <Card
        title={
          <Space>
            <SettingOutlined />
            系统配置
          </Space>
        }
        style={{ marginBottom: 24 }}
      >
        <Descriptions bordered size="small" column={2}>
          <Descriptions.Item label="LLM 模型">GPT-4o</Descriptions.Item>
          <Descriptions.Item label="Embedding 模型">
            text-embedding-3-small
          </Descriptions.Item>
          <Descriptions.Item label="向量数据库">
            <Tag color="green">Milvus 2.4</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="业务数据库">
            <Tag color="blue">PostgreSQL</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Agent 框架">LangGraph</Descriptions.Item>
          <Descriptions.Item label="SQL 重试次数">1</Descriptions.Item>
          <Descriptions.Item label="RAG 返回条数">3</Descriptions.Item>
          <Descriptions.Item label="SQL 默认 LIMIT">50</Descriptions.Item>
          <Descriptions.Item label="查询超时">10s</Descriptions.Item>
          <Descriptions.Item label="日志框架">Loguru</Descriptions.Item>
          <Descriptions.Item label="指标监控">
            <Tag color="orange">Prometheus</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="追踪">
            <Tag color="purple">LangSmith</Tag>
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {/* 安全配置 */}
      <Card
        title={
          <Space>
            <SafetyOutlined />
            安全与限制
          </Space>
        }
        style={{ marginBottom: 24 }}
      >
        <Descriptions bordered size="small" column={2}>
          <Descriptions.Item label="鉴权方式">
            Session Token（不透明 Token + Redis 存储）
          </Descriptions.Item>
          <Descriptions.Item label="密码存储">
            <Tag color="red">bcrypt 哈希</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="SQL 安全策略">
            SELECT 白名单 / 注释剥离防绕过
          </Descriptions.Item>
          <Descriptions.Item label="限流策略">
            30 req/min/IP（Redis ZSET 滑动窗口）
          </Descriptions.Item>
          <Descriptions.Item label="熔断策略">
            连续 5 次失败 → 开路 60s
          </Descriptions.Item>
          <Descriptions.Item label="CORS">
            仅允许配置的白名单来源
          </Descriptions.Item>
          <Descriptions.Item label="结果截断">
            传入 LLM 最多 500 行
          </Descriptions.Item>
          <Descriptions.Item label="容错策略">
            全系统零降级——基础设施故障即服务不可用（fail-fast）
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {/* 偏好设置 */}
      <Card
        title={
          <Space>
            <ApiOutlined />
            偏好设置
          </Space>
        }
      >
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "8px 0",
            }}
          >
            <div>
              <Typography.Text strong>SSE 流式推送</Typography.Text>
              <br />
              <Typography.Text type="secondary">
                开启后每个分析节点完成后实时推送进度，关闭后一次性返回结果
              </Typography.Text>
            </div>
            <Switch checked={useStreaming} onChange={onStreamingChange} />
          </div>

          <Divider style={{ margin: "4px 0" }} />

          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "8px 0",
            }}
          >
            <div>
              <Typography.Text strong>多模态分析</Typography.Text>
              <br />
              <Typography.Text type="secondary">
                上传图表截图/表格图片/PDF/语音文件，自动进行 VL / OCR / 文档解析
              </Typography.Text>
            </div>
            <Tag color="green">已启用</Tag>
          </div>

          <Divider style={{ margin: "4px 0" }} />

          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "8px 0",
            }}
          >
            <div>
              <Typography.Text strong>RAG 知识增强</Typography.Text>
              <br />
              <Typography.Text type="secondary">
                Milvus 向量检索 + 业务知识库，提升 NL2SQL 准确率
              </Typography.Text>
            </div>
            <Tag color="green">已启用</Tag>
          </div>
        </Space>
      </Card>
    </div>
  );
}
