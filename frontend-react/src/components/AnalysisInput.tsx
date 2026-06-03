import { useState, useEffect } from "react";
import { Input, Button, Upload, Space, Tag, message, Tooltip, Badge } from "antd";
import {
  SendOutlined,
  DeleteOutlined,
  PictureOutlined,
  FileAddOutlined,
  AudioOutlined,
  AudioMutedOutlined,
  FilePdfOutlined,
  FileWordOutlined,
  FileMarkdownOutlined,
  FileExcelOutlined,
} from "@ant-design/icons";
import type { UploadFile, RcFile } from "antd/es/upload";
import { useVoiceRecorder } from "../hooks/useVoiceRecorder";
import QueryPresets from "./QueryPresets";

interface Props {
  loading: boolean;
  onSubmit: (query: string, imagesBase64: string[], files: { mime_type: string; data: string; filename: string }[]) => void;
}

const ALL_FILE_ACCEPT = ".pdf,.docx,.doc,.md,.txt,.markdown,.xlsx,.xls,.csv,.wav,.mp3,.m4a,.ogg,.flac,.aac,.opus,.webm";

function guessMimeType(fileName: string): string {
  const ext = fileName.split(".").pop()?.toLowerCase() ?? "";
  if (["png", "jpg", "jpeg", "gif", "webp", "bmp"].includes(ext)) return "image/chart";
  if (["pdf"].includes(ext)) return "application/pdf";
  if (["docx"].includes(ext)) return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  if (["doc"].includes(ext)) return "application/msword";
  if (["md", "markdown", "txt"].includes(ext)) return "text/markdown";
  if (["xlsx"].includes(ext)) return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  if (["xls", "csv"].includes(ext)) return "application/vnd.ms-excel";
  if (["wav", "mp3", "m4a", "ogg", "flac", "aac", "opus", "webm"].includes(ext)) return "audio/wav";
  return "application/octet-stream";
}

const FILE_TAG_CONFIG: Record<string, { icon: React.ReactNode; color: string }> = {
  pdf: { icon: <FilePdfOutlined />, color: "red" },
  docx: { icon: <FileWordOutlined />, color: "blue" },
  doc: { icon: <FileWordOutlined />, color: "blue" },
  md: { icon: <FileMarkdownOutlined />, color: "purple" },
  markdown: { icon: <FileMarkdownOutlined />, color: "purple" },
  txt: { icon: <FileMarkdownOutlined />, color: "purple" },
  xlsx: { icon: <FileExcelOutlined />, color: "green" },
  xls: { icon: <FileExcelOutlined />, color: "green" },
  csv: { icon: <FileExcelOutlined />, color: "green" },
  wav: { icon: <AudioOutlined />, color: "orange" },
  mp3: { icon: <AudioOutlined />, color: "orange" },
  m4a: { icon: <AudioOutlined />, color: "orange" },
  ogg: { icon: <AudioOutlined />, color: "orange" },
  flac: { icon: <AudioOutlined />, color: "orange" },
  aac: { icon: <AudioOutlined />, color: "orange" },
  opus: { icon: <AudioOutlined />, color: "orange" },
  webm: { icon: <AudioOutlined />, color: "orange" },
  png: { icon: <PictureOutlined />, color: "blue" },
  jpg: { icon: <PictureOutlined />, color: "blue" },
  jpeg: { icon: <PictureOutlined />, color: "blue" },
  gif: { icon: <PictureOutlined />, color: "blue" },
  webp: { icon: <PictureOutlined />, color: "blue" },
  bmp: { icon: <PictureOutlined />, color: "blue" },
};

export default function AnalysisInput({ loading, onSubmit }: Props) {
  const [query, setQuery] = useState("");
  const [imageFiles, setImageFiles] = useState<UploadFile[]>([]);
  const [attachedFiles, setAttachedFiles] = useState<UploadFile[]>([]);

  const handleTranscriptFinal = (text: string) => {
    setQuery((prev) => {
      const trimmed = prev.trim();
      return trimmed ? `${trimmed}\n${text}` : text;
    });
    message.success("语音转写完成，已填入输入框");
  };

  const { isRecording, transcript, error: recordError, startRecording, stopRecording } =
    useVoiceRecorder(handleTranscriptFinal);

  useEffect(() => {
    if (recordError) message.error(recordError);
  }, [recordError]);

  const handleSend = () => {
    const trimmed = query.trim();
    if (!trimmed) {
      message.warning("请输入分析问题");
      return;
    }
    const images: string[] = imageFiles
      .filter((f) => f.thumbUrl)
      .map((f) => f.thumbUrl!.split(",")[1] ?? f.thumbUrl!);

    const files = attachedFiles
      .filter((f) => f.thumbUrl)
      .map((f) => ({
        mime_type: f.originFileObj
          ? guessMimeType(f.originFileObj.name)
          : "application/octet-stream",
        data: f.thumbUrl!.split(",")[1] ?? f.thumbUrl!,
        filename: f.name,
      }));

    onSubmit(trimmed, images, files);
  };

  const readAsDataUrl = (file: File, targetList: "image" | "attach") => {
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = reader.result as string;
      const newFile: UploadFile = {
        uid: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        name: file.name,
        status: "done",
        thumbUrl: dataUrl,
        originFileObj: file as RcFile,
      };
      if (targetList === "image") {
        setImageFiles((prev) => [...prev, newFile]);
      } else {
        setAttachedFiles((prev) => [...prev, newFile]);
      }
    };
    reader.readAsDataURL(file);
    return false;
  };

  const clearAll = () => {
    setImageFiles([]);
    setAttachedFiles([]);
  };

  const renderFileTag = (file: UploadFile) => {
    const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
    const cfg = FILE_TAG_CONFIG[ext] ?? { icon: <FileAddOutlined />, color: "default" };
    return <Tag icon={cfg.icon} color={cfg.color}>{file.name}</Tag>;
  };

  return (
    <div style={{ maxWidth: 720, margin: "0 auto" }}>
      <QueryPresets onSelect={(text) => setQuery(text)} />
      <Input.TextArea
        value={isRecording && transcript ? transcript : query}
        onChange={(e) => {
          if (!isRecording) setQuery(e.target.value);
        }}
        placeholder={
          isRecording
            ? "正在聆听中..."
            : "例如：Q3 哪个品类的毛利率最高？环比增长如何？  |  点击麦克风按钮实时语音输入"
        }
        autoSize={{ minRows: 2, maxRows: 4 }}
        style={{ marginBottom: 12 }}
        onPressEnter={(e) => {
          if (!e.shiftKey) {
            e.preventDefault();
            handleSend();
          }
        }}
      />

      {(imageFiles.length > 0 || attachedFiles.length > 0) && (
        <div style={{ marginBottom: 8, display: "flex", flexWrap: "wrap", gap: 4 }}>
          {imageFiles.map((f) => renderFileTag(f))}
          {attachedFiles.map((f) => renderFileTag(f))}
        </div>
      )}

      <Space style={{ width: "100%", justifyContent: "space-between" }}>
        <Space>
          {/* 截图上传 — 图表/表格视觉分析 */}
          <Upload
            beforeUpload={(file) => {
              readAsDataUrl(file, "image");
              return false;
            }}
            fileList={imageFiles}
            showUploadList={false}
            maxCount={5}
            accept="image/png,image/jpeg,image/gif,image/webp"
            onChange={({ fileList: newList }) => {
              if (!isRecording) setImageFiles(newList);
            }}
            onRemove={(file) => {
              setImageFiles((prev) => prev.filter((f) => f.uid !== file.uid));
            }}
          >
            <Tooltip title="上传图表截图（PNG/JPEG，最多5张，用于视觉分析）">
              <Button icon={<PictureOutlined />} size="small">截图</Button>
            </Tooltip>
          </Upload>

          {/* 文件上传 — 统一入口，支持 PDF/Word/MD/Excel/音频 */}
          <Upload
            beforeUpload={(file) => {
              readAsDataUrl(file, "attach");
              return false;
            }}
            fileList={attachedFiles}
            showUploadList={false}
            maxCount={10}
            accept={ALL_FILE_ACCEPT}
            onChange={({ fileList: newList }) => {
              if (!isRecording) setAttachedFiles(newList);
            }}
            onRemove={(file) => {
              setAttachedFiles((prev) => prev.filter((f) => f.uid !== file.uid));
            }}
          >
            <Tooltip title="上传文件（PDF/Word/Markdown/Excel/音频，最多10个，自动识别类型并提取内容）">
              <Button icon={<FileAddOutlined />} size="small">文件</Button>
            </Tooltip>
          </Upload>

          {/* 实时录音 */}
          <Badge status={isRecording ? "processing" : "default"} dot={isRecording}>
            <Tooltip title={isRecording ? "点击停止录音" : "实时录音转文字（需浏览器授权麦克风）"}>
              <Button
                icon={isRecording ? <AudioMutedOutlined /> : <AudioOutlined />}
                size="small"
                danger={isRecording}
                onClick={isRecording ? stopRecording : startRecording}
              >
                {isRecording ? "停止" : "录音"}
              </Button>
            </Tooltip>
          </Badge>
        </Space>

        <Space>
          {(imageFiles.length > 0 || attachedFiles.length > 0) && (
            <Button icon={<DeleteOutlined />} onClick={clearAll} disabled={loading}>清空</Button>
          )}
          <Button type="primary" icon={<SendOutlined />} onClick={handleSend} loading={loading} size="large">分析</Button>
        </Space>
      </Space>
    </div>
  );
}
