"""把 amane 宿主 SDK 打桩，好让 esl1.fd2ppv/plugin.py 能在本地 pytest 里导入。

插件在 amane 里跑时，SDK 由宿主注入；本地没有 amane，所以造一组最小替身，
只为跑通纯解析逻辑的单元测试。真机行为仍以 amane 里的 debug.log 为准。
"""

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PATH = ROOT / "esl1.fd2ppv" / "plugin.py"


def _install_amane_stubs() -> None:
    def module(name, **attrs):
        mod = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        sys.modules[name] = mod
        return mod

    class FailureReason:
        NO_USABLE_METADATA = "no_usable_metadata"

    class SourceError(Exception):
        def __init__(self, reason=None, detail="", **kw):
            super().__init__(detail or reason)
            self.reason = reason
            self.detail = detail
            self.http_status = None
            self.__dict__.update(kw)

    class RequestError(SourceError):
        pass

    class SourceCapability:
        FILM_METADATA = "film_metadata"

    class SourceDescriptor:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class MediaMetadata:
        # 与宿主一致：用于「按字段名过滤 kwargs」
        model_fields = {
            "number": None, "title": None, "release": None, "runtime": None,
            "studio": None, "tags": None, "actors": None, "plot": None,
            "poster_urls": None, "thumb_urls": None, "extrafanart": None,
            "source_url": None, "external_id": None,
        }

        def __init__(self, **kw):
            self.__dict__.update(kw)

    class SearchQuery:
        def __init__(self, number="", title="", **kw):
            self.number = number
            self.title = title
            self.__dict__.update(kw)

    class FetchOptions:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class WebClient:
        ...

    class PluginContext:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class FilmSourceProvider:
        def __init__(self, *args, **kw):
            pass

    class FilmSourcePlugin:
        @classmethod
        def descriptor(cls):
            raise NotImplementedError

    module("amane", __path__=[])
    module("amane.plugins", __path__=[])
    module("amane.plugins.api",
           FilmSourcePlugin=FilmSourcePlugin, FilmSourceProvider=FilmSourceProvider,
           PluginContext=PluginContext)
    module("amane.plugins.models",
           SourceCapability=SourceCapability, SourceDescriptor=SourceDescriptor)
    module("amane.net", __path__=[])
    module("amane.net.errors",
           FailureReason=FailureReason, SourceError=SourceError, RequestError=RequestError)
    module("amane.net.http", WebClient=WebClient)
    module("amane.crawlers", __path__=[])
    module("amane.crawlers.models",
           MediaMetadata=MediaMetadata, SearchQuery=SearchQuery, FetchOptions=FetchOptions)
    module("amane.plugin",
           FilmSourcePlugin=FilmSourcePlugin, FilmSourceProvider=FilmSourceProvider,
           PluginContext=PluginContext, MediaMetadata=MediaMetadata,
           SearchQuery=SearchQuery, FetchOptions=FetchOptions,
           SourceCapability=SourceCapability, SourceDescriptor=SourceDescriptor,
           SourceError=SourceError, RequestError=RequestError,
           FailureReason=FailureReason, WebClient=WebClient)


_install_amane_stubs()


def _load_plugin():
    """按路径加载插件（目录名含点号，不能当包名 import）。"""
    spec = importlib.util.spec_from_file_location("fd2ppv_plugin", PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fd2ppv_plugin"] = mod
    spec.loader.exec_module(mod)
    return mod


PLUGIN = _load_plugin()


def pytest_configure(config):
    """让测试模块直接用全局名 PLUGIN。"""
    import builtins
    builtins.PLUGIN = PLUGIN