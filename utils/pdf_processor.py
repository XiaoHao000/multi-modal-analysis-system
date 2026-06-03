"""
PDF 文档处理器：提取文本 + 内嵌图表。

流程:
  PDF 文件(base64) → PyMuPDF 解析
    ├── 文本提取 → 纯文本字符串
    └── 图片提取 → base64 列表 → 喂给 VLM 做图表解读

优雅降级: PyMuPDF 不可用时，仅提取文本（使用 pdfplumber fallback）。
"""

import base64
import io
from typing import List, Tuple

from utils.logger import logger


def _decode_pdf(pdf_data_b64: str) -> bytes:
    """将 base64 编码的 PDF 数据解码为字节流。"""
    return base64.b64decode(pdf_data_b64)


def _extract_with_pymupdf(pdf_bytes: bytes) -> Tuple[str, List[str]]:
    """使用 PyMuPDF (fitz) 提取文本和图片。"""
    import fitz  # PyMuPDF

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    all_text = []
    all_images = []

    for page_num, page in enumerate(doc):
        # 提取文本
        text = page.get_text()
        if text:
            all_text.append(f"--- 第 {page_num + 1} 页 ---\n{text}")

        # 提取图片
        image_list = page.get_images(full=True)
        for img_index, img_info in enumerate(image_list):
            xref = img_info[0]
            base_image = doc.extract_image(xref)
            img_bytes = base_image["image"]
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")
            all_images.append(img_b64)

    doc.close()
    return "\n\n".join(all_text), all_images


def _extract_text_fallback(pdf_bytes: bytes) -> str:
    """纯文本 fallback（无需 PyMuPDF）。"""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            texts = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    texts.append(text)
            return "\n\n".join(texts)
    except ImportError:
        logger.warning("pdfplumber 未安装，PDF 文本提取不可用")
        return ""
    except Exception as e:
        logger.warning(f"PDF fallback 提取失败: {e}")
        return ""


async def process_pdf_files(files: List[dict], user_query: str) -> Tuple[str, List[str]]:
    """处理 PDF 文件：提取文本和嵌入图表。

    Args:
        files: PDF 文件列表 [{"mime_type": "application/pdf", "data": "base64...", "filename": "report.pdf"}]
        user_query: 用户原始问题

    Returns:
        (pdf_text: str, pdf_charts: list[str])
    """
    if not files:
        return ("", [])

    all_text = []
    all_charts = []

    for pdf_entry in files:
        pdf_data = pdf_entry.get("data", "")
        if not pdf_data:
            continue

        try:
            pdf_bytes = _decode_pdf(pdf_data)
        except Exception as e:
            logger.warning(f"PDF base64 解码失败: {e}")
            continue

        # 尝试 PyMuPDF
        try:
            text, charts = _extract_with_pymupdf(pdf_bytes)
        except ImportError:
            logger.info("PyMuPDF 未安装，使用 pdfplumber fallback（仅文本）")
            text = _extract_text_fallback(pdf_bytes)
            charts = []
        except Exception as e:
            logger.warning(f"PyMuPDF 提取失败，尝试 fallback: {e}")
            text = _extract_text_fallback(pdf_bytes)
            charts = []

        all_text.append(text)
        all_charts.extend(charts)
        logger.info(
            f"PDF 处理完成: {pdf_entry.get('filename', 'unknown')} "
            f"→ 文本 {len(text)} 字, 图片 {len(charts)} 张"
        )

    return ("\n\n".join(all_text), all_charts)
