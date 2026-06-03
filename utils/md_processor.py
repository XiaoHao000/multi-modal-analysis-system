"""
Markdown / 纯文本处理器: .md / .txt 文本读取。

流程:
  文件(base64) → 解码为 UTF-8 文本 → 返回纯文本字符串
"""

import base64
from typing import List

from utils.logger import logger


def _decode_to_text(data_b64: str) -> str:
    raw = base64.b64decode(data_b64)
    # 尝试 UTF-8，失败则用 GBK
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("gbk")
        except Exception:
            return raw.decode("utf-8", errors="replace")


async def process_md_files(files: List[dict], user_query: str = "") -> str:
    """处理 Markdown / 纯文本文件：读取为字符串。

    Args:
        files: 文件列表 [{"mime_type": "text/markdown", "data": "base64...", "filename": "readme.md"}]
        user_query: 用户原始问题（保留接口一致，当前未使用）

    Returns:
        md_text: Markdown 原文
    """
    if not files:
        return ""

    all_text = []

    for entry in files:
        data = entry.get("data", "")
        filename = entry.get("filename", "unknown")
        if not data:
            continue

        try:
            text = _decode_to_text(data)
        except Exception as e:
            logger.warning(f"Markdown 解码失败 ({filename}): {e}")
            continue

        all_text.append(f"--- {filename} ---\n{text}")
        logger.info(f"Markdown 处理完成: {filename} → {len(text)} 字")

    return "\n\n".join(all_text)
