"""
语音处理器：DashScope Paraformer 实时语音转写。

架构（v3.0 — 国内演示）:
  - 实时流式: 前端 AudioContext → PCM 16kHz int16 → WebSocket → 后端缓冲
    → DashScope Recognition(callback) → 逐句返回转写结果
  - 文件上传: base64 音频 → 解码 → DashScope Recognition → 转写文本

Deepgram / Whisper 依赖已移除。Paraformer 中文识别准确率优于 Whisper，
同一 DashScope API Key 零额外开通。
"""

import asyncio
import base64
import json
import os
import tempfile
from http import HTTPStatus
from typing import List, AsyncGenerator

import dashscope
from dashscope.audio.asr import Recognition, RecognitionResult

from config import Config
from utils.logger import logger


# ── DashScope ASR 核心 ──

def _extract_recognition_text(result: RecognitionResult) -> str:
    """从 DashScope RecognitionResult 提取转写文本。

    兼容多种返回格式：get_sentence() 方法、output.sentence、output.sentences。
    """
    # 方式 1: get_sentence() 方法（SDK 推荐）
    try:
        sentence = result.get_sentence()
    except Exception:
        sentence = None

    if sentence is not None:
        if isinstance(sentence, list):
            parts = []
            for item in sentence:
                if isinstance(item, dict):
                    parts.append(str(item.get("text", "")))
                elif isinstance(item, str):
                    parts.append(item)
            return "".join(parts).strip()
        if isinstance(sentence, dict):
            return (sentence.get("text") or "").strip()
        if isinstance(sentence, str):
            return sentence.strip()

    # 方式 2: output 字典
    try:
        output = result.output
        if output and isinstance(output, dict):
            s = output.get("sentence") or output.get("sentences")
            if s:
                if isinstance(s, dict):
                    return (s.get("text") or "").strip()
                if isinstance(s, list):
                    return "".join(
                        item.get("text", "") if isinstance(item, dict) else str(item)
                        for item in s
                    ).strip()
                return str(s).strip()
    except Exception:
        pass

    return ""


def _transcribe_dashscope(audio_bytes: bytes, source_format: str = "pcm") -> str:
    """调用 DashScope Paraformer 转写音频。

    Args:
        audio_bytes: PCM 16kHz int16 裸数据（默认）或其他格式的原始音频字节
        source_format: 音频格式，默认 "pcm"

    Returns:
        转写文本，失败返回空字符串
    """
    api_key = Config.api_key
    if not api_key:
        logger.error("API_KEY 未配置，无法调用 DashScope ASR")
        return ""

    dashscope.api_key = api_key

    with tempfile.NamedTemporaryFile(suffix=f".{source_format}", delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name

    try:
        recognition = Recognition(
            model=Config.dashscope_asr_model,
            format=source_format,
            sample_rate=Config.dashscope_asr_sample_rate,
            callback=None,
        )
        result = recognition.call(tmp_path)

        if result.status_code != HTTPStatus.OK:
            logger.error(
                f"DashScope ASR 失败: status={result.status_code} message={result.message}"
            )
            return ""

        text = _extract_recognition_text(result)
        if text:
            logger.info(f"DashScope ASR 完成: {len(text)} 字 → {text[:80]}...")
        else:
            logger.warning("DashScope ASR 返回空文本")
        return text
    except Exception as e:
        logger.error(f"DashScope ASR 调用异常: {e}")
        return ""
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── 批量文件处理（多模态管道路径 B）──

async def process_voice_files(files: List[dict]) -> str:
    """处理上传的语音文件，转写为文本（DashScope Paraformer）。

    Args:
        files: 语音文件列表 [{"mime_type": "audio/webm", "data": "base64...", "filename": "query.webm"}]

    Returns:
        转写后的文本，多个文件用换行拼接
    """
    if not files:
        return ""

    transcripts = []

    for audio_entry in files:
        audio_b64 = audio_entry.get("data", "")
        filename = audio_entry.get("filename", "audio.webm")

        if not audio_b64:
            continue

        try:
            audio_bytes = base64.b64decode(audio_b64)
        except Exception as e:
            logger.warning(f"音频 base64 解码失败 ({filename}): {e}")
            continue

        # 根据文件扩展名推断格式
        ext = os.path.splitext(filename)[1].lower() if filename else ""
        fmt_map = {".wav": "wav", ".pcm": "pcm", ".mp3": "mp3", ".webm": "webm"}
        source_format = fmt_map.get(ext, "pcm")

        text = _transcribe_dashscope(audio_bytes, source_format)
        if text:
            transcripts.append(text)
            logger.info(f"语音转写完成: {filename} → {len(text)} 字")

    return "\n".join(transcripts)


# ── 实时流式转写（WebSocket 路径 A）──

async def stream_transcribe_dashscope(
    audio_chunks: AsyncGenerator[bytes, None],
) -> AsyncGenerator[str, None]:
    """实时语音转写：收集前端 PCM 音频块 → DashScope Recognition(callback) → 逐句回传。

    前端发送 PCM 16kHz int16 裸数据，后端累积到临时文件，
    录音结束后调用 DashScope Recognition API，callback 每识别一句就 yield 给前端。

    Args:
        audio_chunks: 前端 WebSocket 传来的 PCM 16kHz int16 裸数据

    Yields:
        JSON 字符串: {"transcript": "...", "is_final": true}  识别结果
        JSON 字符串: {"type": "status", "message": "transcribing"}  转写中
        JSON 字符串: {"type": "error", "detail": "..."}  错误
    """
    # Phase 1: 收集 PCM 数据
    pcm_buffer = bytearray()
    try:
        async for chunk in audio_chunks:
            if chunk:
                pcm_buffer.extend(chunk)
    except Exception as e:
        logger.warning(f"音频接收中断: {e}")
        yield json.dumps({"type": "error", "detail": f"音频接收失败: {e}"}, ensure_ascii=False)
        return

    if len(pcm_buffer) == 0:
        yield json.dumps({"type": "error", "detail": "未收到音频数据"}, ensure_ascii=False)
        return

    logger.info(f"收到 PCM 音频: {len(pcm_buffer)} bytes ({len(pcm_buffer)/16000/2:.1f}s)，开始 DashScope 转写")

    # Phase 2: 通知前端开始转写
    yield json.dumps({"type": "status", "message": "transcribing"}, ensure_ascii=False)

    # Phase 3: 写入临时文件，调用 DashScope Recognition（带 callback 逐句回传）
    api_key = Config.api_key
    if not api_key:
        yield json.dumps({"type": "error", "detail": "API_KEY 未配置"}, ensure_ascii=False)
        return

    dashscope.api_key = api_key

    with tempfile.NamedTemporaryFile(suffix=".pcm", delete=False) as f:
        f.write(bytes(pcm_buffer))
        tmp_path = f.name

    # 用 asyncio.Queue 在线程间传递 callback 结果
    result_queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_result(result: RecognitionResult):
        """DashScope callback：每识别出一句话就触发，将结果放入 async queue。"""
        if result.status_code == HTTPStatus.OK:
            text = _extract_recognition_text(result)
            if text:
                loop.call_soon_threadsafe(
                    result_queue.put_nowait,
                    {"transcript": text, "is_final": True},
                )

    try:
        recognition = Recognition(
            model=Config.dashscope_asr_model,
            format="pcm",
            sample_rate=Config.dashscope_asr_sample_rate,
            callback=on_result,
        )
        # Recognition.call() 是同步阻塞的，一次调用完成所有识别
        # callback 在每个句子识别完成时被调用（线程内）
        # 返回值为完整识别结果（仅在无 callback 或 callback 不处理时作为兜底）
        sync_result = await loop.run_in_executor(None, recognition.call, tmp_path)

        # Phase 4: 收集并 yield 所有 callback 结果
        while not result_queue.empty():
            item = result_queue.get_nowait()
            yield json.dumps(item, ensure_ascii=False)

        # 兜底：如果 callback 未触发（如短音频无逐句切分），用同步返回值
        if result_queue.empty():
            text = _extract_recognition_text(sync_result)
            if text:
                yield json.dumps({"transcript": text, "is_final": True}, ensure_ascii=False)

    except Exception as e:
        logger.error(f"DashScope Recognition 异常: {e}")
        yield json.dumps({"type": "error", "detail": f"语音转写失败: {e}"}, ensure_ascii=False)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    logger.info("DashScope 实时转写完成")
