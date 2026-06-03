import { Tag, Tooltip, Typography } from "antd";
import {
  ThunderboltOutlined,
  SearchOutlined,
  BarChartOutlined,
  SafetyOutlined,
  ExperimentOutlined,
} from "@ant-design/icons";
import type { CSSProperties } from "react";

interface PresetGroup {
  label: string;
  icon: React.ReactNode;
  color: string;
  queries: { label: string; text: string; tooltip: string }[];
}

const PRESET_GROUPS: PresetGroup[] = [
  {
    label: "基础查询",
    icon: <SearchOutlined />,
    color: "#1677ff",
    queries: [
      { label: "月度收入", text: "查询2025年各月的主营业务收入趋势", tooltip: "基础 NL2SQL — 单表聚合路由" },
      { label: "部门费用", text: "帮我看看恒通制造的销售费用在各个成本中心是怎么分布的", tooltip: "多表 JOIN + 科目编码匹配" },
      { label: "费用TOP5", text: "哪些成本中心的费用最高？只给我TOP5", tooltip: "RAG 知识增强 — 自动检索费用科目编码" },
      { label: "毛利率对比", text: "对比一下恒通制造和瑞达商贸2025年各季度的毛利率趋势", tooltip: "多租户对比 + ECharts 双线图" },
    ],
  },
  {
    label: "进阶分析",
    icon: <BarChartOutlined />,
    color: "#7c3aed",
    queries: [
      { label: "全科目分析", text: "做一个2025年恒通制造全科目季度分析，找出费用异常增长的月份并给出原因分析", tooltip: "ReAct 多步推理 + 知识库根因分析" },
      { label: "Q3vsQ4费用", text: "2025年Q3和Q4对比，恒通制造的哪些成本中心在Q4出现了费用大幅增长？", tooltip: "环比异常检测 + 财务合理性判断" },
      { label: "应收账款", text: "计算恒通制造的应收账款周转率，分析年末回款风险", tooltip: "对话记忆跨轮上下文" },
    ],
  },
  {
    label: "容错测试",
    icon: <SafetyOutlined />,
    color: "#f59e0b",
    queries: [
      { label: "不存在的数据", text: "查询火星事业部的费用数据", tooltip: "SQL 失败重试 + 优雅降级" },
      { label: "越权测试", text: "删除所有的总账记录", tooltip: "Content Safety 四层防线阻断" },
      { label: "全链路展示", text: "整体财务状况怎么样", tooltip: "完整五步链路: intent → sql → analysis → report" },
    ],
  },
  {
    label: "多模态",
    icon: <ExperimentOutlined />,
    color: "#22c55e",
    queries: [
      { label: "图片分析", text: "分析这张财务指标趋势图里的关键发现", tooltip: "VL 模型图表视觉理解 — 需同时上传图片" },
      { label: "PDF提炼", text: "提炼财务分析报告PDF里提到的TOP3风险和对应的应对措施", tooltip: "PDF 文字+内嵌图双通道 — 需同时上传PDF" },
      { label: "Excel交叉验证", text: "这份财务报表里的数据和数据库里的一致吗？", tooltip: "Excel提取 + 数据库交叉验证 — 需同时上传Excel" },
    ],
  },
];

const tagStyle = (color: string): CSSProperties => ({
  cursor: "pointer",
  borderColor: color,
  color: color,
  background: "#fff",
  transition: "all 0.15s",
  maxWidth: 140,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
});

interface Props {
  onSelect: (text: string) => void;
}

export default function QueryPresets({ onSelect }: Props) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ marginBottom: 8, display: "flex", alignItems: "center", gap: 6 }}>
        <ThunderboltOutlined style={{ color: "#f59e0b", fontSize: 13 }} />
        <Typography.Text style={{ fontSize: 12, color: "#94a3b8" }}>
          快速演示 Query · 点击填入输入框
        </Typography.Text>
      </div>
      {PRESET_GROUPS.map((group) => (
        <div
          key={group.label}
          style={{
            marginBottom: 8,
            display: "flex",
            alignItems: "flex-start",
            gap: 8,
          }}
        >
          <span
            style={{
              fontSize: 11,
              color: group.color,
              minWidth: 56,
              lineHeight: "24px",
              display: "flex",
              alignItems: "center",
              gap: 3,
              flexShrink: 0,
            }}
          >
            {group.icon}
            {group.label}
          </span>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {group.queries.map((q) => (
              <Tooltip key={q.label} title={q.tooltip} placement="top">
                <Tag
                  style={tagStyle(group.color)}
                  onClick={() => onSelect(q.text)}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = group.color;
                    e.currentTarget.style.color = "#fff";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = "#fff";
                    e.currentTarget.style.color = group.color;
                  }}
                >
                  {q.label}
                </Tag>
              </Tooltip>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
