import axios from "axios";
import type {
  AnalyzeRequest,
  AnalyzeResponse,
  HealthResponse,
  LoginRequest,
  LoginResponse,
} from "../types";

const api = axios.create({
  baseURL: "/api",
  timeout: 120_000,
  headers: { "Content-Type": "application/json" },
});

const REFRESH_TOKEN_KEY = "refresh_token";

function generateRequestId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

// 请求拦截：自动附加 Bearer token + X-Request-ID
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("auth_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  config.headers["X-Request-ID"] = generateRequestId();
  return config;
});

let isRefreshing = false;
let failedQueue: Array<{ resolve: (token: string) => void; reject: (err: Error) => void }> = [];

function processQueue(error: Error | null, token: string | null) {
  failedQueue.forEach((p) => {
    if (error || !token) {
      p.reject(error ?? new Error("refresh failed"));
    } else {
      p.resolve(token);
    }
  });
  failedQueue = [];
}

// 响应拦截：401 自动尝试 refresh token 轮转
api.interceptors.response.use(
  (res) => res,
  async (error) => {
    const originalRequest = error.config;

    if (error.response?.status === 401 && !originalRequest._retry) {
      const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY);

      if (!refreshToken) {
        // 没有 refresh token，直接踢出
        localStorage.removeItem("auth_token");
        localStorage.removeItem(REFRESH_TOKEN_KEY);
        window.location.reload();
        return Promise.reject(error);
      }

      if (isRefreshing) {
        // 已有刷新请求在进行中，排队等待
        return new Promise<string>((resolve, reject) => {
          failedQueue.push({ resolve, reject });
        })
          .then((newToken) => {
            originalRequest.headers.Authorization = `Bearer ${newToken}`;
            return api(originalRequest);
          })
          .catch(() => {
            localStorage.removeItem("auth_token");
            localStorage.removeItem(REFRESH_TOKEN_KEY);
            window.location.reload();
            return Promise.reject(error);
          });
      }

      originalRequest._retry = true;
      isRefreshing = true;

      try {
        const { data } = await axios.post<LoginResponse>("/v1/auth/refresh", {
          refresh_token: refreshToken,
        });

        localStorage.setItem("auth_token", data.access_token);
        if (data.refresh_token) {
          localStorage.setItem(REFRESH_TOKEN_KEY, data.refresh_token);
        }

        processQueue(null, data.access_token);

        originalRequest.headers.Authorization = `Bearer ${data.access_token}`;
        return api(originalRequest);
      } catch (refreshError) {
        processQueue(refreshError as Error, null);
        localStorage.removeItem("auth_token");
        localStorage.removeItem(REFRESH_TOKEN_KEY);
        window.location.reload();
        return Promise.reject(refreshError);
      } finally {
        isRefreshing = false;
      }
    }

    if (error.response) {
      const msg = error.response.data?.detail ?? error.response.statusText;
      return Promise.reject(new Error(msg));
    }
    if (error.request) {
      return Promise.reject(new Error("无法连接到服务器，请检查后端是否启动"));
    }
    return Promise.reject(error);
  }
);

export async function login(req: LoginRequest): Promise<LoginResponse> {
  const { data } = await api.post<LoginResponse>("/login", req);
  return data;
}

export async function submitAnalysis(req: AnalyzeRequest): Promise<AnalyzeResponse> {
  const body = {
    query: req.query,
    images: req.images ?? [],
    files: req.files ?? [],
    thread_id: req.thread_id ?? undefined,
    conversation_history: req.conversation_history ?? [],
  };
  const { data } = await api.post<AnalyzeResponse>("/analyze", body);
  return data;
}

export async function checkHealth(): Promise<HealthResponse> {
  const { data } = await api.get<HealthResponse>("/health", { baseURL: "/" });
  return data;
}

// SSE 流式分析
export async function submitAnalysisStream(
  req: AnalyzeRequest,
  onProgress: (step: string, data: Record<string, unknown>) => void,
  onDone: (result: AnalyzeResponse) => void,
  onError: (error: string) => void
): Promise<void> {
  const token = localStorage.getItem("auth_token");
  const response = await fetch("/api/analyze/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Request-ID": generateRequestId(),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      query: req.query,
      images: req.images ?? [],
      files: req.files ?? [],
      thread_id: req.thread_id ?? undefined,
      conversation_history: req.conversation_history ?? [],
    }),
  });

  if (!response.ok) {
    if (response.status === 401) {
      localStorage.removeItem("auth_token");
      window.location.reload();
      return;
    }
    const text = await response.text();
    onError(text);
    return;
  }

  const reader = response.body?.getReader();
  if (!reader) {
    onError("无法读取响应流");
    return;
  }

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        const dataStr = line.slice(6);
        if (dataStr === "[DONE]") return;
        try {
          const parsed = JSON.parse(dataStr);
          if (parsed.event === "progress") {
            onProgress(parsed.data.step, parsed.data);
          } else if (parsed.event === "done") {
            onDone(parsed.data as AnalyzeResponse);
          } else if (parsed.event === "error") {
            onError(parsed.data.detail);
          }
        } catch {
          // 跳过无法解析的行
        }
      }
    }
  }
}
