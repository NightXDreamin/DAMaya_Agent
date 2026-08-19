"""Server Web 并发安全性测试 (TDD)。"""
import asyncio
import pytest

# 虽然我们很难直接启一个完整的 FastAPI 实例测试（因为牵涉太多真实网络），
# 但是我们可以提取核心的 Async Queue 的读写模型来进行测试。
# 验证 WebSocket 代理的回调在使用 Queue 时的消费安全性。

class FakeWebSocketCallbacks:
    """模拟 server_web 中的 WebSocketAgentCallbacks 的核心 Queue 逻辑。"""
    def __init__(self):
        self._queue = asyncio.Queue()
        self._collected = []
        
    def on_text_chunk(self, text: str):
        self._queue.put_nowait({"type": "text", "content": text})
        
    def on_complete(self):
        self._queue.put_nowait({"type": "complete"})
        
    async def consumer(self):
        while True:
            msg = await self._queue.get()
            self._collected.append(msg)
            if msg["type"] == "complete":
                break

@pytest.mark.asyncio
async def test_websocket_queue_concurrency():
    """测试多任务同时给队列下发 Chunk 时的消费者同步安全性。"""
    callbacks = FakeWebSocketCallbacks()
    
    # 消费者任务
    consumer_task = asyncio.create_task(callbacks.consumer())
    
    # 并发产生消息（模拟极端的流式吐字和思考同时到达）
    async def producer_worker(prefix: str):
        for i in range(50):
            callbacks.on_text_chunk(f"{prefix}_{i}")
            await asyncio.sleep(0.001) # 让出控制权形成竞争
            
    # 让三个任务同时生产
    await asyncio.gather(
        producer_worker("A"),
        producer_worker("B"),
        producer_worker("C")
    )
    
    # 发送完成标志
    callbacks.on_complete()
    await consumer_task
    
    # 验证是否收齐了所有消息（150条文本 + 1条完成标志 = 151）
    assert len(callbacks._collected) == 151
    text_count = sum(1 for msg in callbacks._collected if msg["type"] == "text")
    assert text_count == 150
