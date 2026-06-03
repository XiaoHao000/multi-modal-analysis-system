import { useState, useCallback, useEffect, useRef } from "react";
import {
  Layout,
  Typography,
  App,
  Switch,
  Space,
  Menu,
  Button,
  Avatar,
  Dropdown,
  theme,
} from "antd";
import {
  DashboardOutlined,
  HistoryOutlined,
  DatabaseOutlined,
  SettingOutlined,
  LogoutOutlined,
  UserOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import AnalysisInput from "../components/AnalysisInput";
import ResultPanel from "../components/ResultPanel";
import HistoryPage from "./HistoryPage";
import DataSourcePage from "./DataSourcePage";
import SettingsPage from "./SettingsPage";
import { submitAnalysis, submitAnalysisStream } from "../api/client";
import { useAuth } from "../contexts/AuthContext";
import type { AnalyzeResponse, ConversationEntry } from "../types";

const { Header, Sider, Content } = Layout;

export default function AnalysisPage() {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AnalyzeResponse | null>(null);
  const [currentSql, setCurrentSql] = useState("");
  const [useStreaming, setUseStreaming] = useState(true);
  const [progressSteps, setProgressSteps] = useState<string[]>([]);
  const [, setPartialResult] = useState<Partial<AnalyzeResponse>>({});
  const [collapsed, setCollapsed] = useState(false);
  // 多轮对话：thread_id + conversation_history
  const THREAD_KEY = "analysis_thread_id";
  const [threadId, setThreadId] = useState<string>(() => {
    try { return sessionStorage.getItem(THREAD_KEY) || ""; } catch { return ""; }
  });
  const [conversationHistory, setConversationHistory] = useState<ConversationEntry[]>([]);
  const HISTORY_KEY = "analysis_history";
  const [history, setHistory] = useState<
    { query: string; result: AnalyzeResponse; sql: string; time: string }[]
  >(() => {
    try {
      return JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
    } catch {
      return [];
    }
  });
  const [currentPage, setCurrentPage] = useState("dashboard");
  const { message } = App.useApp();
  const messageRef = useRef(message);
  messageRef.current = message;
  const { username, logout } = useAuth();
  const { token: themeToken } = theme.useToken();

  useEffect(() => {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
  }, [history]);

  const handleSubmit = useCallback(
    async (query: string, images: string[], files: { mime_type: string; data: string; filename: string }[]) => {
      setLoading(true);
      setResult(null);
      setProgressSteps([]);
      setPartialResult({});
      setCurrentSql("");
      try {
        let data: AnalyzeResponse;
        const reqPayload = {
          query,
          images,
          files,
          thread_id: threadId || undefined,
          conversation_history: conversationHistory,
        };
        if (useStreaming) {
          data = await new Promise<AnalyzeResponse>((resolve, reject) => {
            submitAnalysisStream(
              reqPayload,
              (step, data) => {
                setProgressSteps((prev) => [...prev, step]);
                // 渐进展示中间结果：SQL 生成后立即显示，分析文本逐步展示
                setPartialResult((prev) => {
                  const next = { ...prev };
                  if (data.generated_sql) next.sql = data.generated_sql as string;
                  if (data.sql_result) next.sql_result = data.sql_result as Record<string, unknown>[];
                  if (data.analysis_text) next.analysis = data.analysis_text as string;
                  if (data.final_report) next.analysis = data.final_report as string;
                  return next;
                });
                if (data.generated_sql) setCurrentSql(data.generated_sql as string);
              },
              resolve,
              (error) => {
                messageRef.current.error(error);
                reject(new Error(error));
              }
            );
          });
        } else {
          data = await submitAnalysis(reqPayload);
        }
        // 首次响应拿到 thread_id 后保存，后续请求复用
        if (data.thread_id && !threadId) {
          setThreadId(data.thread_id);
          try { sessionStorage.setItem(THREAD_KEY, data.thread_id); } catch { /* noop */ }
        }
        // 追加本轮对话到历史（保留最近 10 轮）
        setConversationHistory((prev) => [
          ...prev,
          { user_query: query, analysis_text: data.analysis },
        ].slice(-10));
        setResult(data);
        setCurrentSql(data.sql);
        setHistory((prev) => [
          {
            query,
            result: data,
            sql: data.sql,
            time: new Date().toLocaleString("zh-CN"),
          },
          ...prev.slice(0, 9),
        ]);
        setLoading(false);
      } catch (err) {
        messageRef.current.error(err instanceof Error ? err.message : "请求失败");
        setLoading(false);
      }
    },
    [useStreaming, threadId, conversationHistory]
  );

  const menuItems = [
    { key: "dashboard", icon: <DashboardOutlined />, label: "分析看板" },
    { key: "history", icon: <HistoryOutlined />, label: "历史记录" },
    { key: "datasource", icon: <DatabaseOutlined />, label: "数据源" },
    { key: "settings", icon: <SettingOutlined />, label: "系统设置" },
  ];

  const userMenuItems = [
    { key: "logout", icon: <LogoutOutlined />, label: "退出登录", danger: true },
  ];

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider
        trigger={null}
        collapsible
        collapsed={collapsed}
        width={260}
        style={{
          background: "#0f172a",
          borderRight: "1px solid rgba(255,255,255,0.06)",
        }}
      >
        {/* Logo 区域 */}
        <div
          style={{
            padding: collapsed ? "20px 16px" : "24px 20px",
            display: "flex",
            alignItems: "center",
            gap: 12,
            borderBottom: "1px solid rgba(255,255,255,0.08)",
          }}
        >
          <div
            style={{
              width: 40,
              height: 40,
              borderRadius: 10,
              background: "linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 20,
              flexShrink: 0,
            }}
          >
            📊
          </div>
          {!collapsed && (
            <div>
              <Typography.Title
                level={5}
                style={{ color: "#fff", margin: 0, fontSize: 15 }}
              >
                DataInsight
              </Typography.Title>
              <Typography.Text
                style={{ color: "rgba(255,255,255,0.45)", fontSize: 11 }}
              >
                多模态智能分析平台
              </Typography.Text>
            </div>
          )}
        </div>

        {/* 导航菜单 */}
        <Menu
          mode="inline"
          selectedKeys={[currentPage]}
          onClick={({ key }) => setCurrentPage(key)}
          items={menuItems}
          style={{
            background: "transparent",
            borderRight: 0,
            marginTop: 12,
            padding: "0 12px",
          }}
          theme="dark"
        />

        {/* 底部用户区 */}
        <div
          style={{
            position: "absolute",
            bottom: 0,
            left: 0,
            right: 0,
            padding: "16px 20px",
            borderTop: "1px solid rgba(255,255,255,0.08)",
          }}
        >
          <Dropdown
            menu={{
              items: userMenuItems,
              onClick: ({ key }) => {
                if (key === "logout") logout();
              },
            }}
            trigger={["click"]}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                cursor: "pointer",
                padding: "8px",
                borderRadius: 8,
                transition: "background 0.2s",
              }}
              onMouseEnter={(e) =>
                (e.currentTarget.style.background = "rgba(255,255,255,0.06)")
              }
              onMouseLeave={(e) =>
                (e.currentTarget.style.background = "transparent")
              }
            >
              <Avatar
                size={collapsed ? 32 : 36}
                icon={<UserOutlined />}
                style={{ background: "#4f46e5", flexShrink: 0 }}
              />
              {!collapsed && (
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ color: "#fff", fontSize: 13, fontWeight: 500 }}>
                    {username || "User"}
                  </div>
                </div>
              )}
            </div>
          </Dropdown>
        </div>
      </Sider>

      <Layout>
        {/* Header */}
        <Header
          style={{
            background: "#fff",
            padding: "0 32px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            borderBottom: "1px solid #f1f5f9",
            height: 64,
            boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
          }}
        >
          <Space>
            <Button
              type="text"
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setCollapsed(!collapsed)}
            />
            <Typography.Title level={5} style={{ margin: 0 }}>
              {currentPage === "dashboard" && "分析看板"}
              {currentPage === "history" && "历史记录"}
              {currentPage === "datasource" && "数据源管理"}
              {currentPage === "settings" && "系统设置"}
            </Typography.Title>
          </Space>

          <Space size="middle">
            <Space size={4}>
              <Typography.Text
                style={{ color: themeToken.colorTextSecondary, fontSize: 12 }}
              >
                SSE 流式
              </Typography.Text>
              <Switch
                size="small"
                checked={useStreaming}
                onChange={setUseStreaming}
              />
            </Space>
            <div
              style={{
                padding: "4px 12px",
                borderRadius: 20,
                background: "#f1f5f9",
                fontSize: 12,
                color: "#64748b",
                display: "flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              <ThunderboltOutlined />
              RAG + NL2SQL + VL
            </div>
          </Space>
        </Header>

        {/* Content */}
        <Content
          style={{
            padding: 24,
            background: "#f8fafc",
            minHeight: "calc(100vh - 64px)",
          }}
        >
          <div style={{ maxWidth: 1100, margin: "0 auto" }}>
            {currentPage === "dashboard" ? (
              <>
                <div
                  style={{
                    background: "#fff",
                    padding: 24,
                    borderRadius: 12,
                    boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
                    marginBottom: 24,
                    border: "1px solid #f1f5f9",
                  }}
                >
                  <AnalysisInput loading={loading} onSubmit={handleSubmit} />
                </div>

                <div
                  style={{
                    background: "#fff",
                    padding: 24,
                    borderRadius: 12,
                    boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
                    border: "1px solid #f1f5f9",
                    minHeight: 200,
                  }}
                >
                  <ResultPanel
                    result={loading ? null : result}
                    sql={currentSql}
                    progressSteps={loading ? progressSteps : []}
                    history={history}
                    onSelectHistory={(item) => {
                      setResult(item.result);
                      setCurrentSql(item.sql);
                    }}
                  />
                </div>
              </>
            ) : currentPage === "history" ? (
              <HistoryPage
                history={history}
                onSelect={(item) => {
                  setResult(item.result);
                  setCurrentSql(item.sql);
                  setCurrentPage("dashboard");
                }}
                onClear={() => setHistory([])}
              />
            ) : currentPage === "datasource" ? (
              <DataSourcePage />
            ) : (
              <SettingsPage
                useStreaming={useStreaming}
                onStreamingChange={setUseStreaming}
              />
            )}
          </div>
        </Content>
      </Layout>
    </Layout>
  );
}
