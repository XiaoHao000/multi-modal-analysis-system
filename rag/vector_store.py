import math
from abc import ABC, abstractmethod
from typing import List, Optional

from pymilvus import MilvusClient, DataType

from config import Config
from utils.llm_factory import get_embeddings
from utils.logger import logger


# 常见 OpenAI 嵌入模型维度（避免每次运行都调 API 探测）
_EMBEDDING_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "text-embedding-v3": 1024,
}


class IVectorStore(ABC):
    """向量存储抽象接口——可对接 Milvus / pgvector / Pinecone"""

    @abstractmethod
    def initialize_knowledge(self, documents: List[str], precomputed_embeddings: Optional[List[List[float]]] = None) -> None:
        """首次启动写入知识（幂等，已存在则跳过）"""

    @abstractmethod
    def retrieve(self, query: str, k: int = 3) -> List[str]:
        """检索 top-k 相关文档"""


class MilvusVectorStore(IVectorStore):
    """Milvus 实现（企业级向量数据库，必需依赖）"""

    def __init__(self, uri: str, collection_name: str):
        self.uri = uri
        self.collection_name = collection_name
        self._client = None

    @property
    def client(self) -> MilvusClient:
        if self._client is None:
            self._client = MilvusClient(uri=self.uri)
            logger.info(f"Milvus 已连接: {self.uri}")
        return self._client

    def _collection_exists(self) -> bool:
        return self.client.has_collection(self.collection_name)

    def _collection_has_data(self) -> bool:
        try:
            results = self.client.query(
                collection_name=self.collection_name,
                filter="id != ''",
                limit=1,
                output_fields=["id"],
            )
            return len(results) > 0
        except Exception:
            return False

    def _get_embedding_dim(self) -> int:
        model = Config.embedding_model
        if model in _EMBEDDING_DIMS:
            return _EMBEDDING_DIMS[model]
        embeddings = get_embeddings()
        test_emb = embeddings.embed_query("dim_test")
        dim = len(test_emb)
        _EMBEDDING_DIMS[model] = dim
        return dim

    def initialize_knowledge(self, documents: List[str], precomputed_embeddings: Optional[List[List[float]]] = None) -> None:
        if not documents:
            return

        if self._collection_exists() and self._collection_has_data():
            logger.info("知识库已存在，跳过写入")
            return

        dim = self._get_embedding_dim()

        if self._collection_exists():
            self.client.drop_collection(self.collection_name)

        schema = self.client.create_schema(
            auto_id=False,
            enable_dynamic_field=True,
        )
        schema.add_field(field_name="id", datatype=DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dim)
        schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=4096)

        self.client.create_collection(
            collection_name=self.collection_name,
            schema=schema,
            metric_type="COSINE",
        )

        if precomputed_embeddings is not None:
            embs = precomputed_embeddings
        else:
            try:
                embeddings_api = get_embeddings()
                embs = embeddings_api.embed_documents(documents)
            except Exception as e:
                logger.error(f"Embedding 失败，跳过初始化: {e}")
                return

        data = [
            {"id": f"doc_{i}", "vector": emb, "text": doc}
            for i, (doc, emb) in enumerate(zip(documents, embs))
        ]
        self.client.insert(collection_name=self.collection_name, data=data)

        index_params = self.client.prepare_index_params()
        index_params.add_index(field_name="vector", index_type="IVF_FLAT", metric_type="COSINE", params={"nlist": 32})
        self.client.create_index(collection_name=self.collection_name, index_params=index_params)
        self.client.load_collection(self.collection_name)

        logger.info(f"已写入 {len(documents)} 条业务知识 (dim={dim})")

    def retrieve(self, query: str, k: int = 3) -> List[str]:
        if not self._collection_exists():
            logger.warning("Milvus Collection 不存在，返回空")
            return []

        try:
            embeddings = get_embeddings()
            query_emb = embeddings.embed_query(query)
        except Exception as e:
            logger.error(f"Query embedding 失败: {e}")
            return []

        try:
            results = self.client.search(
                collection_name=self.collection_name,
                data=[query_emb],
                limit=k,
                output_fields=["id", "text"],
            )
        except Exception as e:
            logger.error(f"Milvus 检索异常: {e}")
            return []

        if not results or not results[0]:
            return []

        docs = [r.get("entity", {}).get("text", "") for r in results[0]]
        return [d for d in docs if d]


def create_vector_store() -> MilvusVectorStore:
    """创建 Milvus 向量存储实例（企业级：Milvus 必需，无降级）。"""
    store = MilvusVectorStore(Config.milvus_uri, Config.milvus_collection_name)
    _ = store.client  # 探测连通性，不可达则启动失败
    logger.info("向量存储: Milvus")
    return store
