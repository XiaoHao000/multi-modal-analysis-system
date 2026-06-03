"""
表格图片结构化提取器：用 VL 模型的视觉理解能力识别表格 → 提取结构化数据。

流程:
  表格图片(base64) → VL 模型 + 专用 prompt → JSON 结构化表格 → list[dict]

注：命名为 "ocr_processor" 是沿袭项目惯例，实际不依赖 tesseract/paddleocr 等传统 OCR
引擎，而是利用 VL 模型（GPT-4V/Claude Vision）的端到端视觉理解能力做表格识别。
相比传统 OCR（需要版面分析→文字检测→识别→表格重建多段流水线），VL 方案只需一个
prompt，开发和维护成本极低，且对拍摄角度、光照等噪声有更好的鲁棒性。
"""

import json
import re
from typing import List

from utils.llm_factory import get_vl_llm
from utils.logger import logger
from config import Config

_TABLE_OCR_PROMPT = """你是一个专业的表格数据提取专家。请仔细分析上传的表格图片，提取其中的结构化数据。

要求:
1. 识别表格的列名（表头）
2. 提取每一行的数据
3. 如果表格中有数字，保留原始数值（包括单位）
4. 如果表格中有日期，统一格式为 YYYY-MM-DD
5. 识别表格的类型（销售表/统计表/财务报表等）

输出格式（严格 JSON）:
{
  "table_type": "销售表",
  "table_title": "2025年Q1销售统计",
  "columns": ["月份", "销售额", "销量", "区域"],
  "rows": [
    {"月份": "2025-01", "销售额": 150000, "销量": 1200, "区域": "华北"},
    {"月份": "2025-02", "销售额": 180000, "销量": 1350, "区域": "华北"}
  ],
  "summary": "该表格展示了2025年Q1各区域月度销售数据，共4列12行数据"
}
"""


async def process_table_images(images: List[dict], user_query: str) -> List[dict]:
    """处理表格图片，提取结构化数据。

    Args:
        images: 表格图片文件列表 [{"mime_type": "image/table", "data": "base64...", "filename": "sales.png"}]
        user_query: 用户原始问题，辅助理解表格上下文

    Returns:
        结构化表格数据列表，每个元素为一行数据的 dict
    """
    if not images:
        return []

    if not Config.vl_model:
        logger.warning("VL 模型未配置，跳过表格识别")
        return []

    try:
        llm = get_vl_llm()
    except Exception as e:
        logger.warning(f"VL 模型初始化失败，跳过表格识别: {e}")
        return []

    from langchain_core.messages import HumanMessage

    all_rows = []

    for img_entry in images:
        img_data = img_entry.get("data", "")
        if not img_data:
            continue

        prompt_text = _TABLE_OCR_PROMPT
        if user_query:
            prompt_text += f"\n\n用户的分析问题是：{user_query}\n请特别关注与用户问题相关的数据列。"

        content_blocks = [{"type": "text", "text": prompt_text}]
        content_blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{img_data}"},
        })

        try:
            response = await llm.ainvoke([HumanMessage(content=content_blocks)])
            text = response.content if hasattr(response, "content") else str(response)
            text = re.sub(r'^```json\s*|\s*```$', '', text.strip()).strip()

            parsed = json.loads(text)
            rows = parsed.get("rows", [])
            # 为每行数据附加表格元信息
            for row in rows:
                if isinstance(row, dict):
                    row["_table_type"] = parsed.get("table_type", "未知")
                    row["_table_title"] = parsed.get("table_title", "")
            all_rows.extend(rows)
            logger.info(f"表格 OCR 提取: {parsed.get('table_title', '')} → {len(rows)} 行")
        except json.JSONDecodeError as e:
            logger.warning(f"表格 OCR JSON 解析失败: {e}, 原始输出: {text[:200]}")
            continue
        except Exception as e:
            logger.warning(f"表格 OCR 处理失败: {e}")
            continue

    return all_rows
