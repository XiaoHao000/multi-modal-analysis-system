import { useState, useRef, useCallback } from "react";

interface UseVoiceRecorderReturn {
  isRecording: boolean;
  transcript: string;
  error: string;
  startRecording: () => void;
  stopRecording: () => void;
}

/**
 * 实时语音录音 + WebSocket 转写 Hook（v2.0 — DashScope Paraformer）。
 *
 * 流程: AudioContext → PCM 16kHz int16 → WebSocket 发送音频帧 →
 *       后端 DashScope Recognition(callback) → 逐句回传转写文本
 *
 * 与 v1.0 (MediaRecorder + Deepgram) 的区别:
 *   - 前端直接出 PCM 16kHz，后端零转码
 *   - 后端 Recognition callback 逐句回传，前端实时显示
 *   - 不依赖 ffmpeg / pydub / Deepgram
 */
export function useVoiceRecorder(
  onTranscriptFinal: (text: string) => void
): UseVoiceRecorderReturn {
  const [isRecording, setIsRecording] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [error, setError] = useState("");

  const wsRef = useRef<WebSocket | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const startingRef = useRef(false); // 防双击并发

  const startRecording = useCallback(async () => {
    // 防止双击触发并发录音
    if (startingRef.current || wsRef.current) return;
    startingRef.current = true;
    setError("");
    setTranscript("");

    try {
      // 1. 获取麦克风权限
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
      streamRef.current = stream;

      // 2. 创建 AudioContext（目标采样率 16000Hz，与 DashScope 一致）
      const audioCtx = new AudioContext({ sampleRate: 16000 });
      audioCtxRef.current = audioCtx;

      // 3. WebSocket 连接（token 走查询参数，浏览器 WebSocket 不支持自定义头）
      const token = localStorage.getItem("auth_token") || "";
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      const wsUrl = `${protocol}://${window.location.host}/ws/asr?token=${encodeURIComponent(token)}`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;
      ws.binaryType = "arraybuffer";

      // 4. 标志：WebSocket 是否成功打开（用于 onerror/onclose 判断是否需要清理）
      let wsOpened = false;

      ws.onopen = () => {
        wsOpened = true;
        // 连接成功后开始采集 PCM 音频帧
        const source = audioCtx.createMediaStreamSource(stream);
        // ScriptProcessorNode: 虽然 deprecated，但兼容性最好
        // AudioWorklet 需要 HTTPS + 独立 JS 文件，演示环境不够可靠
        const bufferSize = 4096; // ~256ms per frame at 16kHz
        const processor = audioCtx.createScriptProcessor(bufferSize, 1, 1);

        processor.onaudioprocess = (e) => {
          if (ws.readyState !== WebSocket.OPEN) return;
          const input = e.inputBuffer.getChannelData(0); // Float32Array [-1, 1]
          const pcm16 = new Int16Array(input.length);
          for (let i = 0; i < input.length; i++) {
            // Float32 [-1,1] → Int16 [-32768, 32767]
            const s = Math.max(-1, Math.min(1, input[i]));
            pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
          }
          ws.send(pcm16.buffer);
        };

        source.connect(processor);
        processor.connect(audioCtx.destination); // 不输出声音，但必须 connect
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);

          // 转写状态消息
          if (msg.type === "status") {
            if (msg.message === "transcribing") {
              setTranscript("[正在转写...]");
            }
            return;
          }

          if (msg.type === "done") return;

          if (msg.type === "error") {
            setError(msg.detail);
            return;
          }

          // 转写结果：逐句追加
          if (msg.transcript) {
            setTranscript((prev) => {
              const clean = prev.replace(/\[正在转写...\]/g, "").trim();
              if (!msg.is_final) {
                return msg.transcript;
              }
              // 最终结果：追加到已有文本
              return clean ? `${clean}${msg.transcript}` : msg.transcript;
            });
          }
        } catch {
          // 忽略非 JSON 消息
        }
      };

      ws.onerror = () => {
        if (!wsOpened) {
          // WebSocket 连接失败：清理音频资源，避免泄漏
          audioCtx.close().catch(() => {});
          audioCtxRef.current = null;
          stream.getTracks().forEach((t) => t.stop());
          streamRef.current = null;
          wsRef.current = null;
          setIsRecording(false);
        }
        setError("语音服务连接失败");
      };

      ws.onclose = () => {
        if (!wsOpened) {
          // 未成功打开就关闭：清理资源
          try { audioCtx.close().catch(() => {}); } catch {}
          audioCtxRef.current = null;
          stream.getTracks().forEach((t) => t.stop());
          streamRef.current = null;
        }
        wsRef.current = null;
        setIsRecording(false);
      };

      setIsRecording(true);
      startingRef.current = false;
    } catch (err: unknown) {
      startingRef.current = false;
      const msg =
        err instanceof DOMException && err.name === "NotAllowedError"
          ? "麦克风权限被拒绝，请在浏览器设置中允许访问麦克风"
          : `录音启动失败: ${err instanceof Error ? err.message : String(err)}`;
      setError(msg);
      console.error("录音启动失败:", err);
    }
  }, []);

  const stopRecording = useCallback(() => {
    // 捕获当前引用，防止后续异步操作影响新连接
    const currentWs = wsRef.current;
    const currentStream = streamRef.current;
    const currentAudioCtx = audioCtxRef.current;

    // 停止 AudioContext
    if (currentAudioCtx && currentAudioCtx.state !== "closed") {
      currentAudioCtx.close().catch(() => {});
    }
    audioCtxRef.current = null;

    // 停止麦克风流
    currentStream?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;

    // 发送停止信号，等后端转写完成后关闭
    if (currentWs?.readyState === WebSocket.OPEN) {
      currentWs.send(JSON.stringify({ type: "stop" }));
      // 给后端足够时间转写（最长 15s），收到 done 后会自动关闭
      // 使用捕获的 currentWs 而非 wsRef.current，避免错误关闭新连接
      const capturedWs = currentWs;
      setTimeout(() => {
        if (capturedWs.readyState !== WebSocket.CLOSED) {
          capturedWs.close();
        }
      }, 15000);
    }

    // 清空 wsRef，让下次 startRecording 创建新连接
    wsRef.current = null;
    setIsRecording(false);

    // 等一小段延迟让最后的消息到达，然后回调最终文本
    setTimeout(() => {
      setTranscript((current) => {
        const trimmed = current
          .replace(/\[正在转写...\]/g, "")
          .trim();
        if (trimmed) {
          onTranscriptFinal(trimmed);
        }
        return current;
      });
    }, 500);
  }, [onTranscriptFinal]);

  return { isRecording, transcript, error, startRecording, stopRecording };
}
