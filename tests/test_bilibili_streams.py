from __future__ import annotations

import asyncio
import importlib
import sys
import types
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def bilibili_context(monkeypatch: pytest.MonkeyPatch):
    repo_root = Path(__file__).resolve().parents[1]

    core_pkg = types.ModuleType("core")
    core_pkg.__path__ = [str(repo_root / "core")]
    parsers_pkg = types.ModuleType("core.parsers")
    parsers_pkg.__path__ = [str(repo_root / "core" / "parsers")]
    monkeypatch.setitem(sys.modules, "core", core_pkg)
    monkeypatch.setitem(sys.modules, "core.parsers", parsers_pkg)

    logger = SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
    )
    astrbot_pkg = types.ModuleType("astrbot")
    astrbot_pkg.__path__ = []
    api_module = types.ModuleType("astrbot.api")
    api_module.logger = logger
    monkeypatch.setitem(sys.modules, "astrbot", astrbot_pkg)
    monkeypatch.setitem(sys.modules, "astrbot.api", api_module)

    msgspec_module = types.ModuleType("msgspec")
    msgspec_module.convert = lambda data, _: data
    monkeypatch.setitem(sys.modules, "msgspec", msgspec_module)

    class VideoQuality(Enum):
        _360P = 16
        _480P = 32
        _720P = 64
        _1080P = 80
        HDR = 125
        DOLBY = 126
        _8K = 127

    class VideoCodecs(Enum):
        AV1 = "av01"
        AVC = "avc"
        HEV = "hev"

    @dataclass
    class VideoStreamDownloadURL:
        url: str
        video_quality: VideoQuality
        video_codecs: VideoCodecs | None

    @dataclass
    class AudioStreamDownloadURL:
        url: str
        audio_quality: object

    class Video:
        pass

    video_module = types.ModuleType("bilibili_api.video")
    video_module.Video = Video
    video_module.VideoCodecs = VideoCodecs
    video_module.VideoQuality = VideoQuality
    video_module.VideoStreamDownloadURL = VideoStreamDownloadURL
    video_module.AudioStreamDownloadURL = AudioStreamDownloadURL
    video_module.VideoDownloadURLDataDetecter = object

    bilibili_api_module = types.ModuleType("bilibili_api")
    bilibili_api_module.request_settings = SimpleNamespace(
        set=lambda *args, **kwargs: None
    )
    bilibili_api_module.select_client = lambda *args, **kwargs: None
    opus_module = types.ModuleType("bilibili_api.opus")
    opus_module.Opus = object

    monkeypatch.setitem(sys.modules, "bilibili_api", bilibili_api_module)
    monkeypatch.setitem(sys.modules, "bilibili_api.video", video_module)
    monkeypatch.setitem(sys.modules, "bilibili_api.opus", opus_module)

    config_module = types.ModuleType("core.config")
    config_module.ParserItem = object
    config_module.PluginConfig = object
    monkeypatch.setitem(sys.modules, "core.config", config_module)

    exception_module = importlib.import_module("core.exception")
    base_module = types.ModuleType("core.parsers.base")

    class BaseParser:
        def __init__(self, config, downloader):
            self.headers = {}
            self.cfg = config
            self.downloader = downloader

        @property
        def proxy(self):
            return getattr(self.cfg, "proxy", None)

    base_module.BaseParser = BaseParser
    base_module.Downloader = object
    base_module.ParseException = exception_module.ParseException
    base_module.handle = lambda *_args, **_kwargs: lambda func: func
    monkeypatch.setitem(sys.modules, "core.parsers.base", base_module)

    login_module = types.ModuleType("core.parsers.bilibili.login")

    class BilibiliLogin:
        def __init__(self, _config):
            self._credential = None

    login_module.BilibiliLogin = BilibiliLogin
    monkeypatch.setitem(sys.modules, "core.parsers.bilibili.login", login_module)

    monkeypatch.delitem(sys.modules, "core.parsers.bilibili", raising=False)
    module = importlib.import_module("core.parsers.bilibili")
    return SimpleNamespace(module=module, video=video_module)


def build_parser(ctx, tmp_path: Path, codec_list=None):
    cookie_dir = tmp_path / "cookies"
    cookie_dir.mkdir(exist_ok=True)
    parser_cfg = SimpleNamespace(
        name="bilibili",
        video_quality="_720P",
        video_codec_list=codec_list if codec_list is not None else ["AVC"],
        cookies="",
    )
    config = SimpleNamespace(
        parser=SimpleNamespace(bilibili=parser_cfg),
        cache_dir=tmp_path,
        cookie_dir=cookie_dir,
        max_duration=600,
        proxy=None,
    )
    return ctx.module.BilibiliParser(config, SimpleNamespace())


def test_download_headers_include_configured_cookie(bilibili_context, tmp_path: Path):
    parser = build_parser(bilibili_context, tmp_path)
    parser.mycfg.cookies = "SESSDATA=abc; bili_jct=def"
    parser.cookiejar._load_from_cookies_str(parser.mycfg.cookies)

    headers = asyncio.run(parser._download_headers())

    assert headers["Cookie"] == "SESSDATA=abc; bili_jct=def"


def test_detect_best_streams_attribute_error_falls_back_with_none_codec(
    bilibili_context, tmp_path: Path
):
    parser = build_parser(bilibili_context, tmp_path)
    video = bilibili_context.video

    streams = [
        video.VideoStreamDownloadURL(
            "https://example.test/480.mp4",
            video.VideoQuality._480P,
            video.VideoCodecs.AVC,
        ),
        video.VideoStreamDownloadURL(
            "https://example.test/720-unknown.mp4",
            video.VideoQuality._720P,
            None,
        ),
        video.AudioStreamDownloadURL(
            "https://example.test/audio.m4s",
            SimpleNamespace(value=30280),
        ),
    ]

    class Detector:
        def detect_best_streams(self, **_kwargs):
            raise AttributeError("'NoneType' object has no attribute 'value'")

        def detect(self, **kwargs):
            self.detect_kwargs = kwargs
            return streams

    detector = Detector()

    selected = parser._detect_best_streams_compat(
        detector,
        video.VideoStreamDownloadURL,
        video.AudioStreamDownloadURL,
    )

    assert detector.detect_kwargs["codecs"] == parser.video_codecs
    assert selected[0].url == "https://example.test/720-unknown.mp4"
    assert selected[1].url == "https://example.test/audio.m4s"


def test_stream_fallback_prefers_configured_codec_for_equal_quality(
    bilibili_context, tmp_path: Path
):
    parser = build_parser(bilibili_context, tmp_path, codec_list=["AVC", "AV1"])
    video = bilibili_context.video

    selected = parser._select_best_streams_compat(
        [
            video.VideoStreamDownloadURL(
                "https://example.test/720-unknown.mp4",
                video.VideoQuality._720P,
                None,
            ),
            video.VideoStreamDownloadURL(
                "https://example.test/720-avc.mp4",
                video.VideoQuality._720P,
                video.VideoCodecs.AVC,
            ),
        ],
        video.VideoStreamDownloadURL,
        video.AudioStreamDownloadURL,
    )

    assert selected[0].url == "https://example.test/720-avc.mp4"
    assert selected[1] is None


def test_download_video_wraps_unexpected_errors_as_download_exception(
    bilibili_context, tmp_path: Path
):
    parser = build_parser(bilibili_context, tmp_path)
    output_path = tmp_path / "video.mp4"

    async def extract_download_urls(**_kwargs):
        raise AttributeError("'NoneType' object has no attribute 'value'")

    parser.extract_download_urls = extract_download_urls

    with pytest.raises(bilibili_context.module.DownloadException) as exc_info:
        asyncio.run(parser._download_video(object(), 0, output_path, duration=1))

    assert isinstance(exc_info.value.__cause__, AttributeError)
