# AGENTS.md


## Project Overview

astrbot_plugin_parser is an AstrBot plugin that detects links in chat messages and parses them into structured media results (video, images, audio, files) from 14 platforms: Bilibili, Douyin, Weibo, Xiaohongshu, Xiaoheihe, Zhihu, Kuaishou, AcFun, YouTube, TikTok, Instagram, Twitter/X, NCM (NetEase Cloud Music), and NGA.

Python 3.10+. No build step — this is a pure Python AstrBot plugin loaded at runtime by the AstrBot framework.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run all tests (must run from repo root so `core.xxx` imports resolve)
python -m pytest tests/

# Run a single test file
python -m pytest tests/test_cookie.py

# Run a single test by name
python -m pytest tests/test_cookie.py -k "test_netscape_cookie_file_input"
```

Tests monkeypatch/stub all `astrbot.*` modules, so no AstrBot installation is required to run tests.

## Architecture

### Message Processing Pipeline (`main.py`)

```
Message received
  → Whitelist/blacklist check
  → Chain/Json card URL extraction
  → Skip @-other-bot messages
  → Keyword + regex match against all enabled parsers
  → EmojiLikeArbiter (CQHTTP only, bot-vs-bot dedup via QQ emoji likes)
  → Debouncer (link-level + resource-level dedup within time window)
  → Parser.parse() → ParseResult
  → MessageSender.send_parse_result()
      → Build send plan (light/heavy split, card render decision, merge threshold)
      → Optionally render PIL card
      → Download media (async tasks, awaited at send time)
      → Build AstrBot message components (Video/Image/Record/File/Plain/Nodes)
      → Merge into forwarded nodes if segment count >= forward_threshold
      → Send to session
```

### Parser Registry (`core/parsers/base.py`)

Parsers are registered automatically via `__init_subclass__`. Any class that inherits `BaseParser` is appended to `BaseParser._registry` at import time.

Each parser method is decorated with `@handle(keyword, regex_pattern)`:
```python
class SomeParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="someplatform", display_name="某平台")

    @handle("some.keyword", r"some\.com/video/(?P<vid>\w+)")
    async def _parse_video(self, searched: Match[str]) -> ParseResult:
        ...
```

`@handle` attaches `(keyword, compiled_regex)` pairs to the method. At plugin init, `main.py` iterates all registered parser classes, builds a global `keyword → parser_instance` map and a sorted `keyword+regex` list (longest keyword first to avoid short-keyword hijacking).

To add a new platform: create `core/parsers/<platform>.py`, subclass `BaseParser`, set `platform`, add `@handle`-decorated methods, and add the import in `core/parsers/__init__.py`. Add a matching entry to `_conf_schema.json` and `default_template.json` so the platform can be enabled/disabled via config.

### Data Model (`core/data.py`)

- `ParseResult`: top-level result — platform, author, title, text, timestamp, url, contents list, send_groups, extra dict, repost (nested ParseResult). Has a `get_resource_id()` blake2b fingerprint used for resource-level dedup.
- `MediaContent` subclasses: `VideoContent`, `ImageContent`, `AudioContent`, `FileContent`, `DynamicContent`, `GraphicsContent`, `TextContent`. Each holds a `path_task: Path | Task[Path]` — an asyncio Task that resolves to a local file path once the download completes.
- `SendGroup`: allows parsers to override the default send strategy by grouping contents and setting `force_merge` / `render_card` per group.

### Config System (`core/config.py`)

`ConfigNode` turns raw `dict` config into typed Python objects using class-level type annotations as schema. Writes propagate back to the underlying `AstrBotConfig` dict. Nested nodes are lazily created and cached.

`ParserConfig` (`ConfigNodeContainer`) wraps the `parsers_template` list and exposes each parser's config as an attribute (e.g. `config.parser.bilibili.enable`, `config.parser.douyin.cookies`).

`PluginConfig` extends `ConfigNode` and adds derived paths (`cache_dir`, `cookie_dir`, `data_dir`) and runtime fields. Config schema for the AstrBot WebUI is defined in `_conf_schema.json`.

### Sender Strategy (`core/sender.py`)

`MessageSender` does not understand platform semantics. It:
1. Splits contents into `light` (images, text, graphics) and `heavy` (video, audio, file, dynamic).
2. Decides whether to render a preview card (only for single-heavy-content cases unless overridden).
3. Decides whether to merge all segments into a forwarded `Nodes` message based on `forward_threshold`.
4. Awaits download tasks and converts to AstrBot message components.
5. Falls back to plain text if all media downloads fail.

Parsers can override this by populating `ParseResult.send_groups`.

### Renderer (`core/render.py`)

PIL-based card renderer. Converts a `ParseResult` into a PNG card with: avatar + header, title, cover with video play button overlay, image grid (1/2/3-column layouts with +N indicator), text with emoji support via `apilmoji`, graphics sections, and recursively rendered repost content. Fonts are loaded once at plugin init (`HYSongYunLangHeiW-1.ttf`). Platform logos are composited from `core/resources/logos/`.

### Downloader (`core/download.py`)

`Downloader` provides two download modes:
- **Stream download** (`streamd`): chunked aiohttp download with size limit, retry, and tqdm progress. Used for direct media URLs.
- **yt-dlp download** (`ytdlp_download_video`, `ytdlp_download_audio`): delegates to yt-dlp for platforms that require it (YouTube, TikTok, Instagram).

All download methods return `Task[Path]` via the `@auto_task` decorator — downloads start immediately in the background and are awaited when `MediaContent.get_path()` is called.

### Other Components

- `core/arbiter.py`: `EmojiLikeArbiter` — stateless multi-bot arbitration using QQ emoji likes to ensure only one bot parses a given message. Protocol is deterministic (same inputs → same winner across all bots).
- `core/debounce.py`: time-window dedup per session, on both raw link text and parsed resource_id.
- `core/clean.py`: `CacheCleaner` — APScheduler cron job that periodically wipes `cache_dir`.
- `core/cookie.py`: `CookieJar` — parses both HTTP header-format cookies (`key=value; ...`) and Netscape cookie file format, persists to a Mozilla cookie file for yt-dlp compatibility.

## Conventions

- Parser files live in `core/parsers/<platform>.py` (or `core/parsers/<platform>/` for complex platforms with multiple content types like Bilibili).
- All parser modules must be imported in `core/parsers/__init__.py` to trigger auto-registration.
- `_conf_schema.json` defines the WebUI config schema; `default_template.json` provides default parser template entries. Both must be updated when adding a new platform.
- `core/constants.py` holds shared HTTP headers and user-agent strings.
- `core/utils.py` holds file name generation, AV merge (ffmpeg), limited-size dict, and JSON URL extraction helpers.
- Log prefix convention: `[parser]` or `[<component>]` for debug/warning messages via `astrbot.api.logger`.
