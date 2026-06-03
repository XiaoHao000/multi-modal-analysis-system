import { useState } from "react";
import { Typography, Form, Input, Button, Space, App, theme } from "antd";
import {
  UserOutlined,
  LockOutlined,
  ThunderboltOutlined,
  FileImageOutlined,
  BarChartOutlined,
} from "@ant-design/icons";
import { useAuth } from "../contexts/AuthContext";

const FEATURES = [
  { icon: <FileImageOutlined />, label: "多模态分析" },
  { icon: <BarChartOutlined />, label: "智能可视化" },
  { icon: <ThunderboltOutlined />, label: "RAG + NL2SQL" },
];

export default function LoginPage() {
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const { message } = App.useApp();
  const { token: themeToken } = theme.useToken();

  const handleLogin = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      await login(values.username, values.password);
      message.success("登录成功");
    } catch (err) {
      message.error(err instanceof Error ? err.message : "登录失败，请检查用户名和密码");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "linear-gradient(135deg, #0f172a 0%, #1e1b4b 40%, #312e81 100%)",
        position: "relative",
        overflow: "hidden",
      }}
    >
      {/* 装饰背景 */}
      <div
        style={{
          position: "absolute",
          top: -180,
          right: -180,
          width: 500,
          height: 500,
          borderRadius: "50%",
          background: "rgba(79,70,229,0.12)",
          pointerEvents: "none",
        }}
      />
      <div
        style={{
          position: "absolute",
          bottom: -100,
          left: -100,
          width: 350,
          height: 350,
          borderRadius: "50%",
          background: "rgba(124,58,237,0.08)",
          pointerEvents: "none",
        }}
      />

      {/* 登录卡片 */}
      <div
        style={{
          position: "relative",
          zIndex: 1,
          width: 420,
          background: "#fff",
          borderRadius: 16,
          padding: "48px 40px 36px",
          boxShadow: "0 24px 80px rgba(0,0,0,0.25)",
        }}
      >
        {/* Logo + 标题 */}
        <div style={{ textAlign: "center", marginBottom: 36 }}>
          <div
            style={{
              width: 52,
              height: 52,
              borderRadius: 14,
              background: "linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%)",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 24,
              marginBottom: 16,
            }}
          >
            📊
          </div>
          <Typography.Title level={3} style={{ margin: "0 0 4px" }}>
            DataInsight
          </Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            多模态数据智能分析平台
          </Typography.Text>
        </div>

        <Form
          layout="vertical"
          size="large"
          onFinish={handleLogin}
          autoComplete="off"
        >
          <Form.Item
            name="username"
            rules={[{ required: true, message: "请输入用户名" }]}
          >
            <Input
              prefix={<UserOutlined style={{ color: themeToken.colorTextQuaternary }} />}
              placeholder="用户名"
            />
          </Form.Item>

          <Form.Item
            name="password"
            rules={[{ required: true, message: "请输入密码" }]}
          >
            <Input.Password
              prefix={<LockOutlined style={{ color: themeToken.colorTextQuaternary }} />}
              placeholder="密码"
            />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0 }}>
            <Button type="primary" htmlType="submit" loading={loading} block>
              登 录
            </Button>
          </Form.Item>
        </Form>

        {/* 底部特性 */}
        <div style={{ marginTop: 28, display: "flex", justifyContent: "center", gap: 24 }}>
          {FEATURES.map((f, i) => (
            <Space key={i} size={4} style={{ fontSize: 12, color: "#94a3b8" }}>
              {f.icon}
              {f.label}
            </Space>
          ))}
        </div>

        <Typography.Text
          type="secondary"
          style={{
            fontSize: 11,
            display: "block",
            textAlign: "center",
            marginTop: 20,
          }}
        >
          DataInsight v2.0 · LangGraph + FastAPI + React
        </Typography.Text>
      </div>
    </div>
  );
}
