from typing import List

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from config import Config
from utils.logger import logger
from utils.exceptions import LLMError

# DashScope 原生 embedding 端点（兼容模式不支持 embeddings）
_DASHSCOPE_EMBED_URL = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"


class DashScopeEmbeddings:
    """DashScope 原生 Text Embedding API 封装，兼容 LangChain 接口。

    DashScope 的兼容模式 /v1/embeddings 不支持 OpenAI 格式的 input 字段，
    必须使用原生端点。
    """

    def __init__(self, model: str, api_key: str, request_timeout: int = 30, max_retries: int = 2):
        self.model = model
        self._api_key = api_key
        self._timeout = request_timeout
        self._max_retries = max_retries

    def _call_api(self, texts: List[str], text_type: str) -> List[List[float]]:
        import httpx
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(
                _DASHSCOPE_EMBED_URL,
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                json={
                    "model": self.model,
                    "input": {"texts": texts},
                    "parameters": {"text_type": text_type},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return [e["embedding"] for e in data["output"]["embeddings"]]

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return self._call_api(texts, text_type="document")

    def embed_query(self, text: str) -> List[float]:
        return self._call_api([text], text_type="query")[0]


def get_text_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=Config.llm_model,
        api_key=Config.api_key,
        base_url=Config.base_url,
        temperature=0.1,
        max_tokens=Config.llm_max_tokens,
        request_timeout=30,
        max_retries=2,
    )


def get_vl_llm() -> ChatOpenAI:
    if not Config.vl_model:
        raise LLMError("VL_MODEL 未配置")
    return ChatOpenAI(
        model=Config.vl_model,
        api_key=Config.api_key,
        base_url=Config.base_url,
        temperature=0.1,
        max_tokens=Config.llm_max_tokens,
        request_timeout=60,
        max_retries=1,
    )


def _is_dashscope() -> bool:
    return "dashscope" in Config.base_url


def get_embeddings():
    if _is_dashscope():
        return DashScopeEmbeddings(
            model=Config.embedding_model,
            api_key=Config.api_key,
            request_timeout=30,
            max_retries=2,
        )
    return OpenAIEmbeddings(
        model=Config.embedding_model,
        api_key=Config.api_key,
        base_url=Config.base_url,
        request_timeout=30,
        max_retries=2,
    )
