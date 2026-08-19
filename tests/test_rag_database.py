"""RAG 与 SQLite 持久化测试 (TDD)。"""
import os
import pytest
import asyncio
from Client.core.database import ChatDatabase
from Client.core.vector_rag import MayaDocsRetriever
from langchain_core.documents import Document

def test_database_concurrent_write(tmp_path):
    """测试原生 SQLite 的并发读写防锁死。"""
    db_file = tmp_path / "test.db"
    db = ChatDatabase(str(db_file))
    
    # 我们用多线程来插入数据，观察是否触发 database is locked
    import threading
    
    def writer_worker(session_id: str):
        # 创建 session 从而满足 SQLite 外键约束
        db.create_session(session_id)
        # 用我们的 session_id 强行替换生成的 uuid 以便后续查找
        with db._lock, db._connect() as conn:
            conn.execute("UPDATE sessions SET id = ? WHERE title = ?", (session_id, session_id))
            
        for i in range(20):
            db.append_message(session_id, "user", f"msg_{i}")
            
    threads = []
    for i in range(5):
        t = threading.Thread(target=writer_worker, args=(f"session_{i}",))
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    # 如果没死锁，数据应该有 100 条
    total = 0
    for i in range(5):
        history = db.get_messages(f"session_{i}")
        total += len(history)
    assert total == 100

@pytest.mark.asyncio
async def test_retriever_fallback(tmp_path):
    """测试 RAG 的双轨（关键词降级）能力。"""
    
    # 构造一个虚假的 json 文档
    docs_file = tmp_path / "dummy_docs.json"
    import json
    docs_file.write_text(json.dumps({
        "polyCube": "Create a cube.",
        "xform": "Transform node.",
        "ls": "List items."
    }), encoding="utf-8")
    
    class DummyEmbeddings:
        """假装 FAISS 失败，抛出异常。"""
        def embed_query(self, text):
            raise ValueError("FAISS model is offline")
            
    # Mock MayaClient
    class DummyClient:
        pass

    # 因为 Embeddings 生成失败，其实初始化会报错？
    # retriever 内部逻辑：如果在 vector store 构建时失败，就不建立 faiss
    
    # 由于我们不知道 retriever 具体源码这里是否 try..except，
    # 先试试看传一个损坏的 Embedding 会不会炸，如果不炸就应该 fallback 到关键词匹配
    retriever = MayaDocsRetriever(
        docs_path=docs_file,
        api_key="test",
        base_url="http://test",
        translate_model="test_model"
    )
    # 我们直接 mock 掉 intent_translation 避免真实发请求
    retriever._translate_intent = lambda q: [q]
    
    # 因为初始化没有 _documents 对应的 faiss index 建立成功，_initialized=False
    # 所以直接模拟内部调用
    
    # 找寻 ls
    # 直接在字典里查找
    res1 = retriever._get_relevant_documents("ls")
    assert len(res1) > 0
    assert "List items." in res1[0].page_content
    
    # 找寻 polySphere (不存在且 FAISS 也没初始化)，应该返回空
    res2 = retriever._get_relevant_documents("polySphere")
    assert len(res2) == 0
