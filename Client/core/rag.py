from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI


class DualTrackRAG:
    def __init__(
        self,
        docs_path: Path,
        translate_api_key: str,
        translate_base_url: str,
        translate_model: str,
        selection_context_provider: Callable[[], dict[str, Any]],
    ):
        self.docs_path = docs_path
        self.translate_model = translate_model
        self.selection_context_provider = selection_context_provider
        self._docs = self._load_docs(docs_path)
        self._client = OpenAI(api_key=translate_api_key, base_url=translate_base_url)

    @staticmethod
    def _load_docs(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def collect_scene_context(self) -> dict[str, Any]:
        return self.selection_context_provider()

    def translate_intent(self, user_query: str) -> list[str]:
        prompt = (
            "你是 Maya 术语标准化器。"
            "将用户口语请求转成 Maya 命令关键词数组，"
            "只输出 JSON 数组字符串，例如 [\"matchTransform\",\"parentConstraint\"]。"
        )
        response = self._client.chat.completions.create(
            model=self.translate_model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_query},
            ],
            temperature=0,
        )
        text = response.choices[0].message.content or "[]"

        try:
            data = json.loads(text)
            if isinstance(data, list):
                return [str(x) for x in data]
        except json.JSONDecodeError:
            pass

        # 回退：提取引号内容
        return re.findall(r'"([^"]+)"', text)

    def retrieve_docs(self, keywords: list[str]) -> dict[str, Any]:
        matched: dict[str, Any] = {}
        for key in keywords:
            if key in self._docs:
                matched[key] = self._docs[key]
            else:
                lowered = key.lower()
                for cmd, payload in self._docs.items():
                    if cmd.lower() == lowered:
                        matched[cmd] = payload
                        break
        return matched

    def build_injected_context(self, user_query: str) -> str:
        scene_context = self.collect_scene_context()
        keywords = self.translate_intent(user_query)
        docs = self.retrieve_docs(keywords)

        return json.dumps(
            {
                "scene_context": scene_context,
                "intent_keywords": keywords,
                "docs": docs,
            },
            ensure_ascii=False,
            indent=2,
        )
