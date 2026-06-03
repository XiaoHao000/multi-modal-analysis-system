"""
多模态路由器：检测文件类型 → 分发给对应处理器 → 并行执行 → 收集结果。

支持的模态:
  - image/chart      图表截图 → VLM 解读
  - image/table      表格图片 → OCR + 结构化提取
  - application/pdf  PDF 文档 → 文本 + 图表提取
  - audio/*          语音 → ASR 转写
  - application/vnd...word  Word 文档 → 文本提取
  - text/markdown    Markdown/纯文本 → 直接读取
  - text/plain       纯文本 → 直接读取
  - application/vnd...excel  Excel 表格 → 结构化提取

扩展: 在 MIME_TABLE 中新增一行即可注册新的处理器。
"""

from typing import Dict, List

from utils.logger import logger

# ── MIME 类型 → 处理器路由表 ──
MIME_TABLE = {
    "image/chart": "chart_vl",       # 图表截图 → VLM
    "image/table": "table_ocr",      # 表格图片 → OCR
    "application/pdf": "pdf",        # PDF 文档
    "audio/": "voice",               # 语音（前缀匹配）
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "word",
    "application/msword": "word",    # Word 文档 (.doc/.docx)
    "text/markdown": "md",           # Markdown 文档
    "text/plain": "md",              # 纯文本
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "excel",
    "application/vnd.ms-excel": "excel",  # Excel 表格 (.xlsx/.xls)
}


# 表格相关关键词（用于启发式分类图片为 chart/table）
_TABLE_KEYWORDS = [
    "表格", "提取", "导出", "清单", "明细", "表头", "单元格", "行数据", "列数据",
    "table", "extract", "csv", "excel", "tabular",
]


def classify_file(file_entry: dict, user_query: str = "") -> str:
    """根据 mime_type、filename、用户问题分类文件。

    file_entry: {"mime_type": "image/png", "data": "base64...", "filename": "chart.png"}
    返回: 处理器名 (chart_vl / table_ocr / pdf / voice)
    """
    mime = file_entry.get("mime_type", "")
    filename = file_entry.get("filename", "")

    # 显式标记优先
    if mime == "image/chart":
        # 根据用户问题二次判断：提到了表格相关词 → 可能上传的是表格截图
        if _has_table_intent(user_query):
            return "table_ocr"
        return "chart_vl"
    if mime == "image/table":
        return "table_ocr"
    if mime == "application/pdf" or (filename and filename.lower().endswith(".pdf")):
        return "pdf"
    if mime.startswith("audio/") or mime.startswith("video/"):
        return "voice"

    # Word
    if mime.startswith("application/vnd") and ("word" in mime or "msword" in mime):
        return "word"
    if mime == "application/msword":
        return "word"
    # Markdown / 纯文本
    if mime in ("text/markdown", "text/plain"):
        return "md"
    # Excel
    if mime.startswith("application/vnd") and ("excel" in mime or "spreadsheet" in mime):
        return "excel"

    # 根据文件扩展名推断
    if filename:
        ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        if ext in ("pdf",):
            return "pdf"
        if ext in ("wav", "mp3", "m4a", "ogg", "flac", "aac", "opus"):
            return "voice"
        if ext in ("png", "jpg", "jpeg", "gif", "bmp", "webp"):
            if _has_table_intent(user_query):
                return "table_ocr"
            return "chart_vl"
        if ext in ("docx", "doc"):
            return "word"
        if ext in ("md", "txt", "markdown"):
            return "md"
        if ext in ("xlsx", "xls", "csv"):
            return "excel"

    return "chart_vl"  # 兜底


def _has_table_intent(user_query: str) -> bool:
    """检查用户问题是否涉及表格提取意图。"""
    if not user_query:
        return False
    query_lower = user_query.lower()
    return any(kw in query_lower for kw in _TABLE_KEYWORDS)


async def route_and_process(
    files: List[dict],
    user_query: str,
    chart_vl_fn,
    table_ocr_fn,
    pdf_fn,
    voice_fn,
    word_fn=None,
    md_fn=None,
    excel_fn=None,
) -> Dict:
    """多模态路由器主入口：分类文件 → 并行处理 → 收集结果。

    Args:
        files: 上传文件列表
        user_query: 用户原始问题
        chart_vl_fn: 图表 VLM 处理函数 (files, query) → str
        table_ocr_fn: 表格 OCR 处理函数 (files, query) → list[dict]
        pdf_fn: PDF 处理函数 (files, query) → (text: str, charts: list[str])
        voice_fn: 语音处理函数 (files) → str
        word_fn: Word 处理函数 (files, query) → str
        md_fn: Markdown 处理函数 (files, query) → str
        excel_fn: Excel 处理函数 (files, query) → list[dict]

    Returns:
        {
            "multimodal_insight": str,
            "ocr_table_data": list[dict],
            "pdf_text": str,
            "pdf_charts": list[str],
            "voice_text": str,
            "word_text": str,
            "md_text": str,
            "excel_data": list[dict],
        }
    """
    import asyncio

    if not files:
        return {
            "multimodal_insight": "",
            "ocr_table_data": [],
            "pdf_text": "",
            "pdf_charts": [],
            "voice_text": "",
            "word_text": "",
            "md_text": "",
            "excel_data": [],
        }

    # 分类
    buckets = {"chart_vl": [], "table_ocr": [], "pdf": [], "voice": [], "word": [], "md": [], "excel": []}
    for f in files:
        category = classify_file(f, user_query)
        if category in buckets:
            buckets[category].append(f)
        else:
            buckets["chart_vl"].append(f)  # 未知类型当图表处理

    logger.info(
        f"多模态路由: chart={len(buckets['chart_vl'])} table={len(buckets['table_ocr'])} "
        f"pdf={len(buckets['pdf'])} voice={len(buckets['voice'])} "
        f"word={len(buckets['word'])} md={len(buckets['md'])} excel={len(buckets['excel'])}"
    )

    # 并行执行各处理器
    async def _safe_chart():
        if not buckets["chart_vl"]:
            return ""
        try:
            return await chart_vl_fn(buckets["chart_vl"], user_query)
        except Exception as e:
            logger.warning(f"图表 VLM 处理失败: {e}")
            return ""

    async def _safe_table():
        if not buckets["table_ocr"]:
            return []
        try:
            return await table_ocr_fn(buckets["table_ocr"], user_query)
        except Exception as e:
            logger.warning(f"表格 OCR 处理失败: {e}")
            return []

    async def _safe_pdf():
        if not buckets["pdf"]:
            return ("", [])
        try:
            return await pdf_fn(buckets["pdf"], user_query)
        except Exception as e:
            logger.warning(f"PDF 处理失败: {e}")
            return ("", [])

    async def _safe_voice():
        if not buckets["voice"]:
            return ""
        try:
            return await voice_fn(buckets["voice"])
        except Exception as e:
            logger.warning(f"语音处理失败: {e}")
            return ""

    async def _safe_word():
        if not buckets["word"] or word_fn is None:
            return ""
        try:
            return await word_fn(buckets["word"], user_query)
        except Exception as e:
            logger.warning(f"Word 处理失败: {e}")
            return ""

    async def _safe_md():
        if not buckets["md"] or md_fn is None:
            return ""
        try:
            return await md_fn(buckets["md"], user_query)
        except Exception as e:
            logger.warning(f"Markdown 处理失败: {e}")
            return ""

    async def _safe_excel():
        if not buckets["excel"] or excel_fn is None:
            return []
        try:
            return await excel_fn(buckets["excel"], user_query)
        except Exception as e:
            logger.warning(f"Excel 处理失败: {e}")
            return []

    results = await asyncio.gather(
        _safe_chart(), _safe_table(), _safe_pdf(), _safe_voice(),
        _safe_word(), _safe_md(), _safe_excel(),
    )

    chart_insight, table_data, (pdf_text, pdf_charts), voice_text, word_text, md_text, excel_data = results

    # 将 PDF 内嵌图表也交给 VL 分析
    if pdf_charts:
        try:
            pdf_chart_entries = [
                {"data": c, "filename": f"pdf_embed_{i}.png"}
                for i, c in enumerate(pdf_charts)
            ]
            pdf_chart_insight = await chart_vl_fn(pdf_chart_entries, user_query)
            if pdf_chart_insight:
                chart_insight = (
                    chart_insight + "\n\n## PDF 内嵌图表分析\n" + pdf_chart_insight
                    if chart_insight
                    else pdf_chart_insight
                )
            logger.info(f"PDF 内嵌图表 VL 分析完成: {len(pdf_charts)} 张 → {len(pdf_chart_insight)} 字")
        except Exception as e:
            logger.warning(f"PDF 内嵌图表 VL 分析失败: {e}")

    return {
        "multimodal_insight": chart_insight,
        "ocr_table_data": table_data,
        "pdf_text": pdf_text,
        "pdf_charts": pdf_charts,
        "voice_text": voice_text,
        "word_text": word_text,
        "md_text": md_text,
        "excel_data": excel_data,
    }
