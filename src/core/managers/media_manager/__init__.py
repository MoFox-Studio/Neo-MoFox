"""媒体管理器子包。

将原本 1700+ 行的 ``media_manager.py`` 按职责拆分为独立组件，对外通过
:class:`~src.core.managers.media_manager.manager.MediaManager` 门面聚合。

子模块：
- :mod:`._utils`：纯函数工具（base64 / MIME / GIF 关键帧 / 哈希）
- :mod:`._config`：模型配置、提示词、文件夹与清理参数加载、识别跳过控制
- :mod:`._cleanup`：定时清理调度与执行（pending 目录 + 分类目录）
- :mod:`._file_store`：媒体落盘与跨目录移动
- :mod:`._repository`：Images / Voices / Videos 表读写
- :mod:`._cache`：ImageDescriptions / VoiceDescriptions / VideoDescriptions 缓存
- :mod:`._engines`：内置 VLM / ASR 引擎
- :mod:`._event_handlers`：``ON_MEDIA_RECOGNIZE`` 事件默认回调与注册
- :mod:`._recognition`：识别编排（缓存 → 落盘 → 入库 → 事件链 → 回写）
- :mod:`.manager`：``MediaManager`` 门面与单例访问函数
"""

from src.core.managers.media_manager.manager import (
    MediaManager,
    get_media_manager,
    initialize_media_manager,
)
from src.core.managers.media_manager.utils import compute_media_hash

__all__ = [
    "MediaManager",
    "get_media_manager",
    "initialize_media_manager",
    "compute_media_hash",
]
