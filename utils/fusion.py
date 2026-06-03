"""
多模态融合器：将各模态独立结果合并为统一上下文，注入到后续节点。

融合策略:
  1. 各模态结果在 prompt 中按结构化段落排列
  2. 标注来源（图表解读/表格数据/PDF原文/语音转写）
  3. 空模态自动跳过
  4. 统一上下文字符串 → 注入到 NL2SQL 和报告生成两个节点
"""

from utils.logger import logger


def fuse_multimodal_results(
    multimodal_insight: str = "",
    ocr_table_data: list = None,
    pdf_text: str = "",
    pdf_charts: list = None,
    voice_text: str = "",
    word_text: str = "",
    md_text: str = "",
    excel_data: list = None,
    user_query: str = "",
) -> str:
    """将多模态分析结果融合为统一上下文。

    Args:
        multimodal_insight: 图表 VLM 解读文本
        ocr_table_data: OCR 提取的结构化表格数据
        pdf_text: PDF 提取的纯文本
        pdf_charts: PDF 内嵌图表的 base64 列表
        voice_text: 语音转写文本
        user_query: 用户原始问题（作为融合锚点）

    Returns:
        融合后的 Markdown 格式上下文字符串。如果所有模态都为空，返回空字符串。
    """
    ocr_table_data = ocr_table_data or []
    pdf_charts = pdf_charts or []
    excel_data = excel_data or []

    sections = []

    # 语音转写（截断 2000 字）
    if voice_text and voice_text.strip():
        truncated_voice = voice_text.strip()[:2000]
        if len(voice_text.strip()) > 2000:
            truncated_voice += f"\n... （原文共 {len(voice_text.strip())} 字，已截断）"
        sections.append(f"## 语音输入转写\n{truncated_voice}")

    # 图表解读（截断 2000 字）
    if multimodal_insight and multimodal_insight.strip():
        truncated_insight = multimodal_insight.strip()[:2000]
        if len(multimodal_insight.strip()) > 2000:
            truncated_insight += f"\n... （原文共 {len(multimodal_insight.strip())} 字，已截断）"
        sections.append(f"## 图表截图解读\n{truncated_insight}")

    # 表格数据（结构化，最多保留 100 行）
    if ocr_table_data:
        import json
        truncated_table = ocr_table_data[:100]
        sections.append(
            f"## 图片表格结构化数据\n"
            f"```json\n{json.dumps(truncated_table, ensure_ascii=False, indent=2)}\n```\n"
            f"（共 {len(ocr_table_data)} 行数据，可用于与数据库交叉验证"
            + (f"，已截断至前 100 行" if len(ocr_table_data) > 100 else "") + "）"
        )

    # PDF 文本
    if pdf_text and pdf_text.strip():
        # 截断过长的 PDF 文本（保留前 3000 字）
        truncated = pdf_text.strip()[:3000]
        if len(pdf_text.strip()) > 3000:
            truncated += f"\n... （原文共 {len(pdf_text.strip())} 字，已截断）"
        sections.append(f"## PDF 文档原文\n{truncated}")

    # PDF 内嵌图表标记
    if pdf_charts:
        sections.append(
            f"## PDF 内嵌图表\n"
            f"（PDF 文档中提取到 {len(pdf_charts)} 张嵌入图表，已交由图表解读模块分析）"
        )

    # Word 文档原文
    if word_text and word_text.strip():
        truncated = word_text.strip()[:3000]
        if len(word_text.strip()) > 3000:
            truncated += f"\n... （原文共 {len(word_text.strip())} 字，已截断）"
        sections.append(f"## Word 文档原文\n{truncated}")

    # Markdown / 纯文本原文
    if md_text and md_text.strip():
        truncated = md_text.strip()[:3000]
        if len(md_text.strip()) > 3000:
            truncated += f"\n... （原文共 {len(md_text.strip())} 字，已截断）"
        sections.append(f"## Markdown/文本文档原文\n{truncated}")

    # Excel 表格数据
    if excel_data:
        total_rows = sum(s.get("row_count", len(s.get("rows", []))) for s in excel_data)
        # 最多保留前 5 个 sheet，每个 sheet 前 100 行
        truncated_sheets = excel_data[:5]
        for sheet in truncated_sheets:
            rows = sheet.get("rows", [])
            if len(rows) > 100:
                sheet = dict(sheet)
                sheet["rows"] = rows[:100]
                sheet["_truncated"] = f"（共 {len(rows)} 行，已截断至前 100 行）"
        sections.append(
            f"## Excel 电子表格数据\n"
            f"```json\n{json.dumps(truncated_sheets, ensure_ascii=False, indent=2)}\n```\n"
            f"（共 {len(excel_data)} 个 sheet, {total_rows} 行数据）"
        )

    if not sections:
        return ""

    fused = "\n\n".join(sections)
    logger.info(
        f"多模态融合完成: 语音={'有' if voice_text else '无'} "
        f"图表={'有' if multimodal_insight else '无'} "
        f"表格={'有' if ocr_table_data else '无'} "
        f"PDF={'有' if pdf_text else '无'} "
        f"Word={'有' if word_text else '无'} "
        f"Markdown={'有' if md_text else '无'} "
        f"Excel={'有' if excel_data else '无'}"
    )
    return fused
