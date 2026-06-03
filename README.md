# 多模态财务分析系统 — Multi-Modal Financial Analysis System

基于 LangGraph Supervisor + 5 Specialist Agent 的企业级多模态财务分析系统，支持文字/图表/PDF/语音任意组合输入，NL2SQL 自纠正 + Star Schema 财务数据仓库。

## 🚀 快速开始

### 环境要求

- Python 3.11+
- PostgreSQL 16
- Milvus 2.4
- Node.js 18+

### Docker Compose（推荐）

```bash
git clone https://github.com/XiaoHao000/multi-modal-analysis-system.git
cd multi-modal-analysis-system

cp .env.example .env
# 编辑 .env，填入 API_KEY 和数据库连接信息

docker-compose up -d

# 初始化种子数据
docker exec -it multimodal-api python database/seed_data.py

# 初始化知识库向量
docker exec -it multimodal-api python -c "from rag.knowledge_base import KnowledgeBase; kb = KnowledgeBase(); kb.init()"
```

启动后访问前端页面，或通过 API 调用：

```bash
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"query": "恒通制造 2025年Q3 各成本中心费用分布", "tenant_id": "hengtong"}'
```

### 本地开发

```bash
pip install -r requirements.txt
cp .env.example .env

python database/seed_data.py
python app.py                    # 后端 API → port 8000

cd frontend-react
npm install
npm run dev                      # 前端 → port 5173
```

---

## 🏗 架构

```
React 前端 → FastAPI / SSE Streaming
                ↓
         LangGraph Supervisor（动态路由）
         ┌───┬───┬───┬───┐
         ↓   ↓   ↓   ↓   ↓
      Intent Modal SQL Analysis Report
                ↓
         MCP Server × 4（数据库 / 向量库 / 多模态 / 安全）
```

## 💡 核心特性

- **Supervisor 动态路由**：5 个 Specialist Agent 各司其职，文字/图表/PDF/语音输入统一入口
- **NL2SQL ReAct 自纠正**：LLM 生成 SQL → 四道安全防线验证 → 失败自动重试修正
- **Star Schema 财务总账**：1 Fact + 3 Dim（科目/成本中心/日期），中国企业会计准则
- **多租户隔离**：Prompt 强制路由 + 代码自动注入 tenant_id，数据完全隔离
- **多模态并行解析**：OCR / PDF / Word / Excel / PPT / 语音并发处理，自动识别格式
- **演示安全**：Redis 每日额度控制 + 速率限制 + 输入安全清洗

## 🛠 技术栈

| 层级 | 技术 |
|---|---|
| 编排 | LangGraph Supervisor + ReAct |
| 检索 | Milvus + BGE-M3 + BGE-Reranker |
| 数据库 | PostgreSQL + SQLAlchemy Async |
| 后端 | FastAPI + SSE Streaming |
| 前端 | React 18 + Vite + Ant Design + ECharts |
| 语音 | DashScope Paraformer（实时流式） |
| 部署 | Docker Compose + GitHub Actions CI |

## 📄 License

MIT
