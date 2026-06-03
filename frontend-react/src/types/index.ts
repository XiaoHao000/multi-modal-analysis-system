export interface FileEntry {
  mime_type: string;
  data: string;
  filename: string;
}

export interface ConversationEntry {
  user_query: string;
  analysis_text: string;
}

export interface AnalyzeRequest {
  query: string;
  images?: string[];
  files?: FileEntry[];
  thread_id?: string;
  conversation_history?: ConversationEntry[];
}

export interface AnalyzeResponse {
  success: boolean;
  analysis: string;
  charts: EChartsOption[];
  sql: string;
  sql_result: Record<string, unknown>[];
  trace: string[];
  error: string;
  thread_id: string;
}

export interface EChartsOption {
  tooltip?: Record<string, unknown>;
  legend?: Record<string, unknown>;
  xAxis?: Record<string, unknown>;
  yAxis?: Record<string, unknown>;
  series: SeriesItem[];
  [key: string]: unknown;
}

export interface SeriesItem {
  type: string;
  name?: string;
  data: unknown[];
  radius?: string;
  [key: string]: unknown;
}

export interface HealthResponse {
  status: string;
  version: string;
  database: string;
  components: Record<string, string>;
  details: Record<string, string>;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface UserContext {
  username: string;
  user_id: number;
  tenant_id: number;
  tenant_name: string;
  role: string;
}

export interface LoginResponse {
  access_token: string;
  refresh_token?: string;
  token_type: string;
  user?: UserContext;
}
