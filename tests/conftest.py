"""テスト用の共通部品(API を呼ばないダミークライアント)。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from ai_employee.profile import build_profile
from ai_employee.workspace import Workspace


def text_delta(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=text)
    )


def thinking_delta(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_delta",
        delta=SimpleNamespace(type="thinking_delta", thinking=text),
    )


def message(
    content: list[dict[str, Any]],
    stop_reason: str = "end_turn",
    stop_details: Any = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        content=content, stop_reason=stop_reason, stop_details=stop_details
    )


def say(text: str, stop_reason: str = "end_turn") -> SimpleNamespace:
    return message([{"type": "text", "text": text}], stop_reason)


def call_tool(name: str, arguments: dict[str, Any], tool_id: str = "tu_1"):
    return message(
        [{"type": "tool_use", "id": tool_id, "name": name, "input": arguments}],
        stop_reason="tool_use",
    )


class _FakeStream:
    def __init__(self, final: SimpleNamespace) -> None:
        self._final = final
        self.closed = False

    def __enter__(self) -> "_FakeStream":
        return self

    def __exit__(self, *exc: object) -> bool:
        self.closed = True
        return False

    def __iter__(self):
        for block in self._final.content:
            if block.get("type") == "text":
                yield text_delta(block["text"])
            elif block.get("type") == "thinking":
                yield thinking_delta(block.get("thinking", ""))

    def get_final_message(self) -> SimpleNamespace:
        return self._final


class FakeMessages:
    """台本どおりに応答を返す `client.beta.messages` の代役。"""

    def __init__(self, script: list[SimpleNamespace]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.streams: list[_FakeStream] = []

    def stream(self, **kwargs: Any) -> _FakeStream:
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("台本より多く API が呼ばれました")
        stream = _FakeStream(self.script.pop(0))
        self.streams.append(stream)
        return stream


class FakeClient:
    def __init__(self, script: list[SimpleNamespace]) -> None:
        self.messages = FakeMessages(script)
        self.beta = SimpleNamespace(messages=self.messages)


@pytest.fixture
def workspace(tmp_path) -> Workspace:
    ws = Workspace("tester", tmp_path)
    ws.save_profile(build_profile("tester", "テスト社員", "assistant"))
    return ws


@pytest.fixture
def profile(workspace):
    return workspace.load_profile()
