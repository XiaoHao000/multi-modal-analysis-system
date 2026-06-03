"""
Unit tests for multi-modal modules:
  - modality_router (classify_file, route_and_process)
  - fusion (fuse_multimodal_results)
  - ocr_processor (process_table_images)
  - pdf_processor (process_pdf_files)
  - voice_processor (process_voice_files)
"""

import base64
import io
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════
# classify_file & MIME_TABLE
# ═══════════════════════════════════════════════════════════════

class TestClassifyFile:
    """MIME 类型分类测试"""

    def test_explicit_chart_mime(self):
        from utils.modality_router import classify_file
        assert classify_file({"mime_type": "image/chart", "data": ""}) == "chart_vl"

    def test_explicit_table_mime(self):
        from utils.modality_router import classify_file
        assert classify_file({"mime_type": "image/table", "data": ""}) == "table_ocr"

    def test_explicit_pdf_mime(self):
        from utils.modality_router import classify_file
        assert classify_file({"mime_type": "application/pdf", "data": ""}) == "pdf"

    def test_pdf_by_extension(self):
        from utils.modality_router import classify_file
        assert classify_file({"mime_type": "", "filename": "report.pdf"}) == "pdf"

    def test_audio_mime_prefix(self):
        from utils.modality_router import classify_file
        assert classify_file({"mime_type": "audio/wav", "data": ""}) == "voice"
        assert classify_file({"mime_type": "audio/mp3", "data": ""}) == "voice"

    def test_audio_by_extension(self):
        from utils.modality_router import classify_file
        for ext in ("wav", "mp3", "m4a", "ogg", "flac", "aac", "opus"):
            assert classify_file({"mime_type": "", "filename": f"audio.{ext}"}) == "voice"

    def test_image_by_extension_defaults_to_chart(self):
        from utils.modality_router import classify_file
        assert classify_file({"mime_type": "", "filename": "photo.png"}) == "chart_vl"
        assert classify_file({"mime_type": "", "filename": "img.jpg"}) == "chart_vl"

    def test_unknown_defaults_to_chart(self):
        from utils.modality_router import classify_file
        assert classify_file({"mime_type": "text/plain", "data": ""}) == "chart_vl"
        assert classify_file({}) == "chart_vl"


# ═══════════════════════════════════════════════════════════════
# route_and_process
# ═══════════════════════════════════════════════════════════════

class TestRouteAndProcess:
    """多模态路由器集成测试"""

    @pytest.mark.anyio
    async def test_empty_files_returns_empty(self):
        from utils.modality_router import route_and_process

        async def noop(*args, **kwargs):
            return "should-not-be-called"

        result = await route_and_process([], "test query", noop, noop, noop, noop)
        assert result == {
            "multimodal_insight": "",
            "ocr_table_data": [],
            "pdf_text": "",
            "pdf_charts": [],
            "voice_text": "",
        }

    @pytest.mark.anyio
    async def test_routes_chart_to_chart_vl(self):
        from utils.modality_router import route_and_process

        called = {}

        async def chart_fn(files, query):
            called["chart"] = (len(files), query)
            return "chart analysis result"

        async def table_fn(files, query):
            called["table"] = len(files)
            return []

        async def pdf_fn(files, query):
            called["pdf"] = len(files)
            return ("", [])

        async def voice_fn(files):
            called["voice"] = len(files)
            return ""

        files = [{"mime_type": "image/chart", "data": "fake_b64", "filename": "chart.png"}]
        result = await route_and_process(files, "分析趋势", chart_fn, table_fn, pdf_fn, voice_fn)

        assert called.get("chart") == (1, "分析趋势")
        assert result["multimodal_insight"] == "chart analysis result"
        assert result["ocr_table_data"] == []
        assert result["voice_text"] == ""

    @pytest.mark.anyio
    async def test_buckets_mixed_files(self):
        from utils.modality_router import route_and_process

        called = {}

        async def chart_fn(files, query):
            called["chart"] = len(files)
            return "chart"

        async def table_fn(files, query):
            called["table"] = len(files)
            return [{"col": "val"}]

        async def pdf_fn(files, query):
            called["pdf"] = len(files)
            return ("pdf text", ["chart1"])

        async def voice_fn(files):
            called["voice"] = len(files)
            return "hello world"

        files = [
            {"mime_type": "image/chart", "data": "a", "filename": "c1.png"},
            {"mime_type": "image/chart", "data": "b", "filename": "c2.png"},
            {"mime_type": "image/table", "data": "c", "filename": "t1.png"},
            {"mime_type": "application/pdf", "data": "d", "filename": "r1.pdf"},
            {"mime_type": "audio/wav", "data": "e", "filename": "v1.wav"},
        ]
        result = await route_and_process(files, "query", chart_fn, table_fn, pdf_fn, voice_fn)

        assert called["chart"] == 2
        assert called["table"] == 1
        assert called["pdf"] == 1
        assert called["voice"] == 1
        assert result["multimodal_insight"] == "chart"
        assert result["ocr_table_data"] == [{"col": "val"}]
        assert result["pdf_text"] == "pdf text"
        assert result["pdf_charts"] == ["chart1"]
        assert result["voice_text"] == "hello world"

    @pytest.mark.anyio
    async def test_graceful_degradation_on_processor_error(self):
        """单个处理器报错不应影响其他处理器"""
        from utils.modality_router import route_and_process

        async def chart_fn(files, query):
            raise RuntimeError("VLM 挂了")

        async def table_fn(files, query):
            return [{"ok": True}]

        async def pdf_fn(files, query):
            return ("", [])

        async def voice_fn(files):
            return ""

        files = [
            {"mime_type": "image/chart", "data": "a"},
            {"mime_type": "image/table", "data": "b"},
        ]
        result = await route_and_process(files, "query", chart_fn, table_fn, pdf_fn, voice_fn)

        # chart 失败 → 返回 ""
        assert result["multimodal_insight"] == ""
        # table 正常 → 返回数据
        assert result["ocr_table_data"] == [{"ok": True}]


# ═══════════════════════════════════════════════════════════════
# fuse_multimodal_results
# ═══════════════════════════════════════════════════════════════

class TestFusion:
    """多模态结果融合测试"""

    def test_all_empty_returns_empty(self):
        from utils.fusion import fuse_multimodal_results
        assert fuse_multimodal_results() == ""

    def test_only_voice(self):
        from utils.fusion import fuse_multimodal_results
        result = fuse_multimodal_results(voice_text="用户语音输入内容")
        assert "## 语音输入转写" in result
        assert "用户语音输入内容" in result

    def test_only_chart_insight(self):
        from utils.fusion import fuse_multimodal_results
        result = fuse_multimodal_results(multimodal_insight="销售额呈上升趋势")
        assert "## 图表截图解读" in result
        assert "销售额呈上升趋势" in result

    def test_only_table_data(self):
        from utils.fusion import fuse_multimodal_results
        data = [{"月份": "2025-01", "销售额": 150000}]
        result = fuse_multimodal_results(ocr_table_data=data)
        assert "## 图片表格结构化数据" in result
        assert "2025-01" in result
        assert "共 1 行数据" in result

    def test_only_pdf_text(self):
        from utils.fusion import fuse_multimodal_results
        result = fuse_multimodal_results(pdf_text="PDF 文档内容")
        assert "## PDF 文档原文" in result
        assert "PDF 文档内容" in result

    def test_pdf_text_truncation(self):
        from utils.fusion import fuse_multimodal_results
        long_text = "A" * 3500
        result = fuse_multimodal_results(pdf_text=long_text)
        assert len(result) < 3500 + 200  # should be truncated
        assert "已截断" in result

    def test_pdf_charts_count(self):
        from utils.fusion import fuse_multimodal_results
        result = fuse_multimodal_results(pdf_charts=["c1", "c2", "c3"])
        assert "3 张嵌入图表" in result

    def test_all_modalities_fused(self):
        from utils.fusion import fuse_multimodal_results
        result = fuse_multimodal_results(
            multimodal_insight="图表解读",
            ocr_table_data=[{"a": 1}],
            pdf_text="PDF原文",
            pdf_charts=["chart1"],
            voice_text="语音转写",
        )
        assert "语音输入转写" in result
        assert "图表截图解读" in result
        assert "图片表格结构化数据" in result
        assert "PDF 文档原文" in result
        assert "PDF 内嵌图表" in result
        # 顺序: 语音 → 图表 → 表格 → PDF文本 → PDF图表
        voice_pos = result.index("语音输入转写")
        chart_pos = result.index("图表截图解读")
        table_pos = result.index("图片表格结构化数据")
        pdf_pos = result.index("PDF 文档原文")
        assert voice_pos < chart_pos < table_pos < pdf_pos

    def test_ocr_table_data_none_handled(self):
        from utils.fusion import fuse_multimodal_results
        result = fuse_multimodal_results(ocr_table_data=None)
        assert result == ""


# ═══════════════════════════════════════════════════════════════
# ocr_processor
# ═══════════════════════════════════════════════════════════════

class TestOCRProcessor:
    """表格 OCR 处理器测试"""

    @pytest.mark.anyio
    async def test_empty_images_returns_empty(self):
        from utils.ocr_processor import process_table_images
        result = await process_table_images([], "")
        assert result == []

    @pytest.mark.anyio
    async def test_no_vl_model_configured_returns_empty(self):
        from utils.ocr_processor import process_table_images
        with patch("utils.ocr_processor.Config") as mock_cfg:
            mock_cfg.vl_model = None
            mock_cfg.ocr_model = None
            result = await process_table_images(
                [{"mime_type": "image/table", "data": "fake"}], ""
            )
            assert result == []

    @pytest.mark.anyio
    async def test_extracts_table_rows(self):
        from utils.ocr_processor import process_table_images

        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "table_type": "销售表",
            "table_title": "2025年Q1销售",
            "columns": ["月份", "销售额"],
            "rows": [
                {"月份": "2025-01", "销售额": 150000},
                {"月份": "2025-02", "销售额": 180000},
            ],
            "summary": "Q1销售数据",
        })

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch("utils.ocr_processor.get_vl_llm", return_value=mock_llm), \
             patch("utils.ocr_processor.Config") as mock_cfg:
            mock_cfg.vl_model = "qwen-vl-plus"
            mock_cfg.ocr_model = None

            result = await process_table_images(
                [{"mime_type": "image/table", "data": "fake_base64"}],
                "分析销售趋势",
            )

            assert len(result) == 2
            assert result[0]["月份"] == "2025-01"
            assert result[0]["销售额"] == 150000
            assert result[0]["_table_type"] == "销售表"
            assert result[0]["_table_title"] == "2025年Q1销售"

    @pytest.mark.anyio
    async def test_json_parse_error_skipped(self):
        from utils.ocr_processor import process_table_images

        mock_response = MagicMock()
        mock_response.content = "不是 JSON"

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch("utils.ocr_processor.get_vl_llm", return_value=mock_llm), \
             patch("utils.ocr_processor.Config") as mock_cfg:
            mock_cfg.vl_model = "qwen-vl-plus"
            mock_cfg.ocr_model = None

            result = await process_table_images(
                [{"mime_type": "image/table", "data": "fake"}], ""
            )
            assert result == []


# ═══════════════════════════════════════════════════════════════
# pdf_processor
# ═══════════════════════════════════════════════════════════════

class TestPDFProcessor:
    """PDF 处理器测试"""

    @pytest.mark.anyio
    async def test_empty_files_returns_empty(self):
        from utils.pdf_processor import process_pdf_files
        result = await process_pdf_files([], "")
        assert result == ("", [])

    @pytest.mark.anyio
    async def test_invalid_base64_skipped(self):
        from utils.pdf_processor import process_pdf_files
        result = await process_pdf_files(
            [{"mime_type": "application/pdf", "data": "!!!not-valid-base64!!!"}], ""
        )
        assert result == ("", [])

    @pytest.mark.anyio
    async def test_pymupdf_extraction(self):
        """测试 PyMuPDF 提取文本和图片"""
        from utils.pdf_processor import process_pdf_files

        # 创建一个最简单的 PDF
        pdf_bytes = _make_minimal_pdf()

        result = await process_pdf_files(
            [{
                "mime_type": "application/pdf",
                "data": base64.b64encode(pdf_bytes).decode(),
                "filename": "test.pdf",
            }],
            "",
        )
        text, charts = result
        assert isinstance(text, str)
        assert isinstance(charts, list)

    @pytest.mark.anyio
    async def test_data_key_empty_skipped(self):
        from utils.pdf_processor import process_pdf_files
        result = await process_pdf_files(
            [{"mime_type": "application/pdf", "data": ""}], ""
        )
        assert result == ("", [])


def _make_minimal_pdf() -> bytes:
    """Generate a minimal valid PDF for testing."""
    # Minimal PDF with one page
    content = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Resources<<>>>>endobj
xref
0 4
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
trailer<</Size 4/Root 1 0 R>>
startxref
206
%%EOF"""
    # Add BT/ET text block so PyMuPDF can extract text
    text_block = b"""BT
/F1 12 Tf
100 700 Td
(Hello PDF Test) Tj
ET
"""
    # Rebuild with text content in page stream
    pdf = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Resources<</Font<</F1<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>>>>>/Contents 4 0 R>>endobj
4 0 obj<</Length """ + str(len(text_block)).encode() + b""">>
stream
""" + text_block + b"""
endstream
endobj
xref
0 5
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000250 00000 n
trailer<</Size 5/Root 1 0 R>>
startxref
340
%%EOF"""
    return pdf


# ═══════════════════════════════════════════════════════════════
# voice_processor
# ═══════════════════════════════════════════════════════════════

class TestVoiceProcessor:
    """语音处理器测试"""

    @pytest.mark.anyio
    async def test_empty_files_returns_empty(self):
        from utils.voice_processor import process_voice_files
        result = await process_voice_files([])
        assert result == ""

    @pytest.mark.anyio
    async def test_empty_data_skipped(self):
        from utils.voice_processor import process_voice_files
        result = await process_voice_files(
            [{"mime_type": "audio/wav", "data": "", "filename": "empty.wav"}]
        )
        assert result == ""

    @pytest.mark.anyio
    async def test_invalid_base64_skipped(self):
        from utils.voice_processor import process_voice_files
        result = await process_voice_files(
            [{"mime_type": "audio/wav", "data": "!!!bad!!!", "filename": "bad.wav"}]
        )
        assert result == ""

    @pytest.mark.anyio
    async def test_dashscope_asr_empty_api_key_returns_empty(self):
        """DashScope ASR: API_KEY 未配置时应返回空字符串"""
        from utils.voice_processor import process_voice_files

        wav_bytes = _make_minimal_wav()

        with patch("utils.voice_processor.Config") as mock_cfg:
            mock_cfg.api_key = ""
            mock_cfg.dashscope_asr_model = "paraformer-realtime-v2"
            mock_cfg.dashscope_asr_sample_rate = 16000

            result = await process_voice_files(
                [{
                    "mime_type": "audio/wav",
                    "data": base64.b64encode(wav_bytes).decode(),
                    "filename": "test.wav",
                }]
            )
            assert result == ""

    @pytest.mark.anyio
    async def test_dashscope_asr_api_error_returns_empty(self):
        """DashScope ASR: API 调用异常时应返回空字符串（不抛异常）"""
        from utils.voice_processor import process_voice_files

        wav_bytes = _make_minimal_wav()

        with patch("utils.voice_processor.Config") as mock_cfg:
            mock_cfg.api_key = "sk-test-key"
            mock_cfg.dashscope_asr_model = "paraformer-realtime-v2"
            mock_cfg.dashscope_asr_sample_rate = 16000

            with patch("utils.voice_processor._transcribe_dashscope",
                       return_value="") as mock_transcribe:
                result = await process_voice_files(
                    [{
                        "mime_type": "audio/wav",
                        "data": base64.b64encode(wav_bytes).decode(),
                        "filename": "test.wav",
                    }]
                )
                assert result == ""
                mock_transcribe.assert_called_once()


def _make_minimal_wav() -> bytes:
    """Generate a minimal WAV file with 0.1s of silence."""
    import struct
    sample_rate = 16000
    num_samples = int(0.1 * sample_rate)
    data_size = num_samples * 2  # 16-bit mono
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,        # chunk size
        1,         # PCM
        1,         # mono
        sample_rate,
        sample_rate * 2,
        2,         # block align
        16,        # bits per sample
        b"data",
        data_size,
    )
    return header + b"\x00" * data_size
