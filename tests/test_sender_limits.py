from __future__ import annotations

import asyncio
import importlib
import sys
import types
from types import SimpleNamespace

import pytest


@pytest.fixture
def sender_context(monkeypatch: pytest.MonkeyPatch):
    logger = SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )

    astrbot_pkg = types.ModuleType("astrbot")
    astrbot_pkg.__path__ = []
    api_module = types.ModuleType("astrbot.api")
    api_module.logger = logger
    monkeypatch.setitem(sys.modules, "astrbot", astrbot_pkg)
    monkeypatch.setitem(sys.modules, "astrbot.api", api_module)

    config_module = types.ModuleType("core.config")
    config_module.PluginConfig = object
    monkeypatch.setitem(sys.modules, "core.config", config_module)

    render_module = types.ModuleType("core.render")
    render_module.Renderer = object
    monkeypatch.setitem(sys.modules, "core.render", render_module)

    components_module = types.ModuleType("astrbot.core.message.components")

    class BaseMessageComponent:
        pass

    class Plain(BaseMessageComponent):
        def __init__(self, text: str):
            self.text = text

    class File(BaseMessageComponent):
        pass

    class Image(BaseMessageComponent):
        pass

    class Node(BaseMessageComponent):
        pass

    class Nodes(BaseMessageComponent):
        def __init__(self, nodes):
            self.nodes = nodes

    class Record(BaseMessageComponent):
        pass

    class Video(BaseMessageComponent):
        pass

    components_module.BaseMessageComponent = BaseMessageComponent
    components_module.File = File
    components_module.Image = Image
    components_module.Node = Node
    components_module.Nodes = Nodes
    components_module.Plain = Plain
    components_module.Record = Record
    components_module.Video = Video
    monkeypatch.setitem(
        sys.modules, "astrbot.core.message.components", components_module
    )

    event_module = types.ModuleType("astrbot.core.platform.astr_message_event")
    event_module.AstrMessageEvent = object
    monkeypatch.setitem(
        sys.modules, "astrbot.core.platform.astr_message_event", event_module
    )

    monkeypatch.delitem(sys.modules, "core.sender", raising=False)
    module = importlib.import_module("core.sender")
    return SimpleNamespace(module=module, components=components_module)


def test_heavy_media_duration_limit_has_specific_tip(sender_context):
    class DurationLimitedContent:
        async def get_path(self):
            raise sender_context.module.DurationLimitException

    sender = sender_context.module.MessageSender(
        SimpleNamespace(show_download_fail_tip=True),
        renderer=SimpleNamespace(render_card=lambda _result: None),
    )

    segments = asyncio.run(
        sender._build_segments(
            result=SimpleNamespace(),
            plan={
                "render_card": False,
                "force_merge": False,
                "light": [],
                "heavy": [DurationLimitedContent()],
            },
        )
    )

    assert len(segments) == 1
    assert isinstance(segments[0], sender_context.components.Plain)
    assert segments[0].text == "此项媒体超过时长限制"
