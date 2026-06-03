from typing import Annotated, Literal, Optional
from pydantic import BaseModel, Field
from pydantic.functional_validators import AfterValidator


def _validate_file_count(v: list) -> list:
    if len(v) > 10:
        raise ValueError("最多上传 10 个文件")
    return v


def _validate_image_count(v: list[str]) -> list[str]:
    if len(v) > 5:
        raise ValueError("最多上传 5 张图片")
    return v


_VALID_MIME_TYPES = frozenset({
    "image/chart", "image/table", "application/pdf",
    "audio/wav", "audio/mp3", "audio/mpeg", "audio/webm",
    "audio/ogg", "audio/m4a", "audio/flac", "audio/aac", "audio/opus",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/msword",  # .doc
    "text/markdown",  # .md
    "text/plain",  # .txt
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    "application/vnd.ms-excel",  # .xls
})


def _validate_mime_type(v: str) -> str:
    if v not in _VALID_MIME_TYPES:
        raise ValueError(f"不支持的 MIME 类型: {v}，支持的类型: {sorted(_VALID_MIME_TYPES)}")
    return v


class FileEntry(BaseModel):
    """多模态文件条目"""
    mime_type: Annotated[str, AfterValidator(_validate_mime_type)] = Field(
        default="image/chart",
        description="MIME 类型: image/chart, image/table, application/pdf, audio/*, application/vnd...word, text/markdown, text/plain, application/vnd...excel",
    )
    data: str = Field(..., description="Base64 编码的文件内容")
    filename: str = Field(default="", description="原始文件名，用于扩展名推断")


class AnalyzeRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="用户分析问题")
    images: Annotated[list[str], AfterValidator(_validate_image_count)] = Field(
        default_factory=list,
        description="图表截图的 Base64 编码列表（最多 5 张，兼容旧接口）",
    )
    files: Annotated[list[FileEntry], AfterValidator(_validate_file_count)] = Field(
        default_factory=list,
        description="多模态文件列表: 表格图片/PDF/语音（最多 10 个）",
    )
    thread_id: Optional[str] = Field(
        default=None,
        description="会话 ID，前端维护。首次请求不传，后续请求带上以延续上下文",
    )
    conversation_history: list[dict] = Field(
        default_factory=list,
        description="前端维护的对话历史，每项含 user_query 和 analysis_text",
    )

    model_config = {"extra": "forbid"}


class AnalyzeResponse(BaseModel):
    success: bool
    analysis: str = Field(default="", description="Markdown 分析报告")
    charts: list[dict] = Field(default_factory=list, description="ECharts 配置列表")
    sql: str = Field(default="", description="生成的 SQL（调试用）")
    sql_result: list[dict] = Field(default_factory=list, description="查询结果数据")
    trace: list[str] = Field(default_factory=list, description="执行步骤")
    error: str = Field(default="", description="错误信息")
    thread_id: str = Field(default="", description="会话 ID，前端应保存并在后续请求中传递")

    model_config = {"extra": "forbid"}


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"
    database: str
    components: dict[str, str] = {}
    details: dict[str, str] = {}


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class UserContext(BaseModel):
    """登录响应中的用户上下文，前端缓存后用于 UI 展示和后续请求。"""
    username: str
    user_id: int
    tenant_id: int
    tenant_name: str = ""
    role: str = "analyst"  # admin | analyst | viewer


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: Optional[UserContext] = None


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LogoutRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)
