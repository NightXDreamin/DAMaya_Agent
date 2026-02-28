from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Generator, Iterable, Optional

import tiktoken
from openai import OpenAI


@dataclass
class StreamEvent:
    type: str
    content: Optional[str] = None
    tool_call_delta: Optional[dict] = None
    finish_reason: Optional[str] = None


class LLMClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        chat_model: str,
        timeout: float = 120.0,
    ):
        self.chat_model = chat_model
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._encoding = tiktoken.get_encoding("cl100k_base")

    def estimate_tokens(self, messages: Iterable[dict]) -> int:
        total = 0
        for msg in messages:
            total += 4
            total += len(self._encoding.encode(msg.get("content", "") or ""))
            if "tool_calls" in msg:
                total += len(self._encoding.encode(json.dumps(msg["tool_calls"], ensure_ascii=False)))
        return total + 2

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.2,
    ) -> Generator[StreamEvent, None, None]:
        req: dict[str, Any] = {
            "model": self.chat_model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
        }
        if tools:
            req["tools"] = tools
            req["tool_choice"] = "auto"

        stream = self.client.chat.completions.create(**req)

        for chunk in stream:
            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            if delta.content:
                yield StreamEvent(type="text", content=delta.content)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    delta_payload = {
                        "index": tc.index,
                        "id": tc.id,
                        "type": tc.type,
                        "name": tc.function.name if tc.function else None,
                        "arguments": tc.function.arguments if tc.function else None,
                    }
                    yield StreamEvent(type="tool_call_delta", tool_call_delta=delta_payload)

            if choice.finish_reason:
                yield StreamEvent(type="done", finish_reason=choice.finish_reason)
