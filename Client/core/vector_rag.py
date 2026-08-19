"""LangChain 标准 RAG 模块 — 双轨检索（关键词 + FAISS 向量）。

将 Maya 命令文档转为 LangChain Document，通过 BaseRetriever 接口
为 Agent 提供标准化的上下文检索能力。

双轨策略:
  1. 关键词轨 — LLM 意图翻译 + 精确/模糊匹配（可靠基线）
  2. 向量轨 — OpenAIEmbeddings + FAISS 语义检索（面向未来扩展）
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Optional

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_openai import OpenAIEmbeddings
from openai import OpenAI
from pydantic import Field, PrivateAttr

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 文档加载
# ---------------------------------------------------------------------------


def _load_maya_docs(docs_path: Path) -> dict[str, Any]:
    """加载 Maya 命令文档 JSON。"""
    if not docs_path.exists():
        return {}
    with docs_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _docs_to_documents(docs: dict[str, Any]) -> list[Document]:
    """将 Maya 命令字典转为 LangChain Document 列表。"""
    documents: list[Document] = []
    for cmd_name, payload in docs.items():
        # 构建文档内容
        parts = [f"命令: {cmd_name}"]
        if isinstance(payload, dict):
            if "synopsis" in payload:
                parts.append(f"用法: {payload['synopsis']}")
            if "flags" in payload:
                parts.append(f"参数: {', '.join(payload['flags'])}")
            if "example" in payload:
                parts.append(f"示例: {payload['example']}")
        else:
            parts.append(str(payload))

        content = "\n".join(parts)
        documents.append(Document(
            page_content=content,
            metadata={"command": cmd_name, "raw": payload},
        ))
    return documents


# ---------------------------------------------------------------------------
# 双轨检索器
# ---------------------------------------------------------------------------


class MayaDocsRetriever(BaseRetriever):
    """Maya 命令文档双轨检索器。

    关键词轨：LLM 意图翻译 → 命令名精确/模糊匹配
    向量轨：FAISS 语义检索（当文档 > 0 时启用）
    """

    docs_path: Path = Field(description="Maya 命令文档 JSON 路径")
    api_key: str = Field(description="LLM/Embedding API Key")
    base_url: str = Field(description="API Base URL")
    translate_model: str = Field(description="意图翻译模型名")
    embedding_model: str = Field(default="text-embedding-v3", description="Embedding 模型名")
    top_k: int = Field(default=3, description="向量检索返回数量")

    # 私有属性
    _docs: dict[str, Any] = PrivateAttr(default_factory=dict)
    _documents: list[Document] = PrivateAttr(default_factory=list)
    _llm_client: Any = PrivateAttr(default=None)
    _embeddings: Any = PrivateAttr(default=None)
    _faiss_index: Any = PrivateAttr(default=None)
    _doc_embeddings: Any = PrivateAttr(default=None)
    _initialized: bool = PrivateAttr(default=False)

    def model_post_init(self, __context: Any) -> None:
        """延迟初始化。"""
        self._docs = _load_maya_docs(self.docs_path)
        self._documents = _docs_to_documents(self._docs)
        self._llm_client = OpenAI(api_key=self.api_key, base_url=self.base_url)

        # 初始化 Embedding + FAISS
        if self._documents:
            try:
                self._embeddings = OpenAIEmbeddings(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    model=self.embedding_model,
                )
                self._build_faiss_index()
                self._initialized = True
                logger.info("FAISS 向量索引已构建，文档数: %d", len(self._documents))
            except Exception as exc:
                logger.warning("FAISS 索引构建失败，将仅使用关键词检索: %s", exc)
                self._initialized = False

    def _build_faiss_index(self) -> None:
        """构建 FAISS 内存索引。

        惰性导入 faiss/numpy：二者为可选依赖，缺失时抛出 ImportError，
        由 ``model_post_init`` 捕获并降级为纯关键词检索。
        """
        import faiss
        import numpy as np

        texts = [doc.page_content for doc in self._documents]
        embeddings = self._embeddings.embed_documents(texts)
        matrix = np.array(embeddings, dtype=np.float32)
        dim = matrix.shape[1]
        self._faiss_index = faiss.IndexFlatL2(dim)
        self._faiss_index.add(matrix)
        self._doc_embeddings = matrix

    # --- 关键词轨 ---

    def _translate_intent(self, query: str) -> list[str]:
        """LLM 意图翻译：用户口语 → Maya 命令关键词。"""
        prompt = (
            "你是 Maya 术语标准化器。"
            "将用户口语请求转成 Maya 命令关键词数组，"
            '只输出 JSON 数组字符串，例如 ["matchTransform","parentConstraint"]。'
        )
        try:
            response = self._llm_client.chat.completions.create(
                model=self.translate_model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": query},
                ],
                temperature=0,
            )
            text = response.choices[0].message.content or "[]"
            data = json.loads(text)
            if isinstance(data, list):
                return [str(x) for x in data]
        except json.JSONDecodeError:
            return re.findall(r'"([^"]+)"', text)
        except Exception as exc:
            logger.warning("意图翻译失败: %s", exc)
        return []

    def _keyword_retrieve(self, keywords: list[str]) -> list[Document]:
        """关键词精确/模糊匹配。"""
        matched: list[Document] = []
        matched_cmds: set[str] = set()

        for key in keywords:
            # 精确匹配
            if key in self._docs:
                if key not in matched_cmds:
                    matched_cmds.add(key)
                    for doc in self._documents:
                        if doc.metadata.get("command") == key:
                            matched.append(doc)
                            break
                continue

            # 模糊匹配
            lowered = key.lower()
            for doc in self._documents:
                cmd = doc.metadata.get("command", "")
                if cmd.lower() == lowered and cmd not in matched_cmds:
                    matched_cmds.add(cmd)
                    matched.append(doc)
                    break

        return matched

    # --- 向量轨 ---

    def _vector_retrieve(self, query: str) -> list[Document]:
        """FAISS 向量语义检索。"""
        if not self._initialized or self._faiss_index is None:
            return []

        import numpy as np

        try:
            query_embedding = self._embeddings.embed_query(query)
            query_vec = np.array([query_embedding], dtype=np.float32)
            k = min(self.top_k, len(self._documents))
            distances, indices = self._faiss_index.search(query_vec, k)

            results: list[Document] = []
            for idx in indices[0]:
                if 0 <= idx < len(self._documents):
                    results.append(self._documents[idx])
            return results
        except Exception as exc:
            logger.warning("FAISS 向量检索失败: %s", exc)
            return []

    # --- BaseRetriever 接口 ---

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun | None = None,
    ) -> list[Document]:
        """双轨检索：关键词 + 向量，合并去重。"""
        # 关键词轨
        keywords = self._translate_intent(query)
        keyword_docs = self._keyword_retrieve(keywords)

        # 向量轨
        vector_docs = self._vector_retrieve(query)

        # 合并去重
        seen_cmds: set[str] = set()
        merged: list[Document] = []

        for doc in keyword_docs:
            cmd = doc.metadata.get("command", "")
            if cmd not in seen_cmds:
                seen_cmds.add(cmd)
                merged.append(doc)

        for doc in vector_docs:
            cmd = doc.metadata.get("command", "")
            if cmd not in seen_cmds:
                seen_cmds.add(cmd)
                merged.append(doc)

        return merged


# ---------------------------------------------------------------------------
# 上下文构建辅助
# ---------------------------------------------------------------------------


def build_injected_context(
    retriever: MayaDocsRetriever,
    user_query: str,
    scene_context_provider: Callable[[], dict[str, Any]] | None = None,
) -> str:
    """构建注入 Agent 的上下文字符串（兼容旧接口）。"""
    # 场景上下文
    scene_context: dict[str, Any] = {}
    if scene_context_provider:
        try:
            scene_context = scene_context_provider()
        except Exception as exc:
            logger.warning("场景上下文获取失败: %s", exc)

    # 文档检索
    docs = retriever.invoke(user_query)

    # 提取关键词（从文档 metadata）
    keywords = [doc.metadata.get("command", "") for doc in docs]

    # 构建文档内容
    docs_dict: dict[str, Any] = {}
    for doc in docs:
        cmd = doc.metadata.get("command", "")
        raw = doc.metadata.get("raw", {})
        if cmd:
            docs_dict[cmd] = raw

    return json.dumps(
        {
            "scene_context": scene_context,
            "intent_keywords": keywords,
            "docs": docs_dict,
        },
        ensure_ascii=False,
        indent=2,
    )
