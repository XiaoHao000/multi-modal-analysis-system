import { ConfigProvider, App as AntApp } from "antd";
import zhCN from "antd/locale/zh_CN";
import AnalysisPage from "./pages/AnalysisPage";
import ErrorBoundary from "./components/ErrorBoundary";
import { AuthProvider } from "./contexts/AuthContext";

function AppContent() {
  // v2.1: 去掉登录页面，直接进入分析功能页
  return <AnalysisPage />;
}

export default function App() {
  return (
    <ErrorBoundary>
      <ConfigProvider
        locale={zhCN}
        theme={{
          token: {
            colorPrimary: "#4f46e5",
            colorSuccess: "#10b981",
            colorWarning: "#f59e0b",
            colorError: "#ef4444",
            colorInfo: "#3b82f6",
            borderRadius: 8,
            fontFamily:
              "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
          },
          components: {
            Layout: {
              headerBg: "#ffffff",
              siderBg: "#0f172a",
              triggerBg: "#1e293b",
            },
            Card: {
              borderRadiusLG: 12,
              paddingLG: 24,
            },
            Button: {
              borderRadius: 6,
              controlHeightLG: 44,
            },
            Input: {
              borderRadius: 8,
              controlHeightLG: 44,
            },
            Menu: {
              darkItemBg: "#0f172a",
              darkItemSelectedBg: "#4f46e5",
              itemBorderRadius: 8,
            },
            Table: {
              headerBg: "#f8fafc",
              headerColor: "#475569",
            },
          },
        }}
      >
        <AntApp>
          <AuthProvider>
            <AppContent />
          </AuthProvider>
        </AntApp>
      </ConfigProvider>
    </ErrorBoundary>
  );
}
