import base64
import io
import ssl
import time
import weakref
import asyncio
from typing import TYPE_CHECKING, Any

import orjson
import urllib3
from PIL import Image

from src.app.plugin_system.api.log_api import get_logger

if TYPE_CHECKING:
    from ...plugin import NapcatAdapter

logger = get_logger("napcat_adapter")

# 简单的缓存实现，通过 kernel.storage 实现磁盘持久化存储
_CACHE_LOADED = False
_CACHE: dict[str, dict[str, dict[str, Any]]] = {
    "group_info": {},
    "group_detail_info": {},
    "member_info": {},
    "stranger_info": {},
    "self_info": {},
}

# 各类信息的 TTL 缓存过期时间设置
GROUP_INFO_TTL = 300  # 5 min
GROUP_DETAIL_TTL = 300
MEMBER_INFO_TTL = 180
STRANGER_INFO_TTL = 300
SELF_INFO_TTL = 300

_adapter_ref: weakref.ReferenceType["NapcatAdapter"] | None = None


def register_adapter(adapter: "NapcatAdapter") -> None:
    """注册 NapcatAdapter 实例，以便 utils 模块可以获取 WebSocket"""
    global _adapter_ref
    _adapter_ref = weakref.ref(adapter)
    logger.debug("Napcat adapter registered in utils for websocket access")


async def _ensure_cache_loaded() -> None:
    """确保缓存已从磁盘加载"""
    global _CACHE_LOADED
    if _CACHE_LOADED:
        return

    # 先加载数据，避免在持有 _CACHE_LOCK 时进行异步 IO (json_store 内部有自己的锁)
    # 这可以防止与系统中其他同样使用 json_store 的组件产生循环死锁
    from src.kernel.storage import json_store

    try:
        data = await json_store.load("napcat_cache")
    except Exception as e:
        logger.debug(f"Failed to load napcat cache: {e}")
        data = None

    if _CACHE_LOADED:
        return

    if isinstance(data, dict):
        for key, section in _CACHE.items():
            cached_section = data.get(key)
            if isinstance(cached_section, dict):
                section.update(cached_section)

    _CACHE_LOADED = True


async def _save_cache_to_disk() -> None:
    """保存缓存到磁盘"""
    from src.kernel.storage import json_store

    try:
        await json_store.save("napcat_cache", _CACHE)
    except Exception as e:
        logger.debug(f"Write napcat cache failed: {e}")


async def _get_cached(section: str, key: str, ttl: int) -> Any | None:
    await _ensure_cache_loaded()
    now = time.time()
    entry = _CACHE.get(section, {}).get(key)
    if not entry:
        return None
    ts = entry.get("ts", 0)
    if ts and now - ts <= ttl:
        return entry.get("data")
    _CACHE.get(section, {}).pop(key, None)

    try:
        await _save_cache_to_disk()
    except Exception:
        pass
    return None


async def _set_cached(section: str, key: str, data: Any) -> None:
    await _ensure_cache_loaded()
    _CACHE.setdefault(section, {})[key] = {"data": data, "ts": time.time()}

    try:
        await _save_cache_to_disk()
    except Exception:
        logger.debug("Write napcat cache failed")


def _get_adapter(adapter: "NapcatAdapter | None" = None) -> "NapcatAdapter":
    target = adapter
    if target is None and _adapter_ref:
        target = _adapter_ref()
    if target is None:
        raise RuntimeError(
            "NapcatAdapter 未注册，请确保已调用 utils.register_adapter 注册"
        )
    return target


async def _call_adapter_api(
    action: str,
    params: dict[str, Any],
    adapter: "NapcatAdapter | None" = None,
    timeout: float = 30.0,
) -> dict[str, Any] | None:
    """统一通过 adapter 发送和接收 API 调用"""
    try:
        target = _get_adapter(adapter)
        # 确保 WS 已连接
        target.get_ws_connection()
    except Exception as e:  # pragma: no cover - 难以在单元测试中查看
        logger.error(f"WebSocket 未准备好，无法调用 API: {e}")
        return None

    try:
        return await target.send_napcat_api(action, params, timeout=timeout)
    except Exception as e:
        logger.error(f"{action} 调用失败: {e}")
        return None


class SSLAdapter(urllib3.PoolManager):
    def __init__(self, *args, **kwargs):
        context = ssl.create_default_context()
        context.set_ciphers("DEFAULT@SECLEVEL=1")
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        kwargs["ssl_context"] = context
        super().__init__(*args, **kwargs)


async def get_respose(
    action: str,
    params: dict[str, Any],
    adapter: "NapcatAdapter | None" = None,
    timeout: float = 30.0,
):
    return await _call_adapter_api(action, params, adapter=adapter, timeout=timeout)

async def get_group_info(
    group_id: int,
    *,
    use_cache: bool = True,
    force_refresh: bool = False,
    adapter: "NapcatAdapter | None" = None,
) -> dict | None:
    """
    获取群组基本信息

    返回值可能是None，需要调用方检查空值
    """
    logger.debug("获取群组基本信息中")
    cache_key = str(group_id)
    if use_cache and not force_refresh:
        cached = await _get_cached("group_info", cache_key, GROUP_INFO_TTL)
        if cached is not None:
            return cached

    socket_response = await _call_adapter_api(
        "get_group_info",
        {"group_id": group_id},
        adapter=adapter,
    )
    data = socket_response.get("data") if socket_response else None
    if data is not None and use_cache:
        await _set_cached("group_info", cache_key, data)
    return data


async def get_group_detail_info(
    group_id: int,
    *,
    use_cache: bool = True,
    force_refresh: bool = False,
    adapter: "NapcatAdapter | None" = None,
) -> dict | None:
    """
    获取群组详细信息

    返回值可能是None，需要调用方检查空值
    """
    logger.debug("获取群组详细信息中")
    cache_key = str(group_id)
    if use_cache and not force_refresh:
        cached = await _get_cached("group_detail_info", cache_key, GROUP_DETAIL_TTL)
        if cached is not None:
            return cached

    socket_response = await _call_adapter_api(
        "get_group_detail_info",
        {"group_id": group_id},
        adapter=adapter,
    )
    data = socket_response.get("data") if socket_response else None
    if data is not None and use_cache:
        await _set_cached("group_detail_info", cache_key, data)
    return data


async def get_member_info(
    group_id: int,
    user_id: int,
    *,
    use_cache: bool = True,
    force_refresh: bool = False,
    adapter: "NapcatAdapter | None" = None,
) -> dict | None:
    """
    获取群组成员信息

    返回值可能是None，需要调用方检查空值
    """
    logger.debug("获取群组成员信息中")
    cache_key = f"{group_id}:{user_id}"
    if use_cache and not force_refresh:
        cached = await _get_cached("member_info", cache_key, MEMBER_INFO_TTL)
        if cached is not None:
            return cached

    socket_response = await _call_adapter_api(
        "get_group_member_info",
        {"group_id": group_id, "user_id": user_id, "no_cache": True},
        adapter=adapter,
    )
    data = socket_response.get("data") if socket_response else None
    if data is not None and use_cache:
        await _set_cached("member_info", cache_key, data)
    return data


async def get_image_base64(url: str) -> str:
    # sourcery skip: raise-specific-error
    """下载图片/视频并返回Base64"""
    logger.debug(f"下载图片: {url}")

    def _download_sync() -> str:
        http = SSLAdapter()
        response = http.request("GET", url, timeout=10)
        if response.status != 200:
            raise Exception(f"HTTP Error: {response.status}")
        image_bytes = response.data
        return base64.b64encode(image_bytes).decode("utf-8")

    try:
        return await asyncio.to_thread(_download_sync)
    except Exception as e:
        logger.error(f"图片下载失败: {e!s}")
        raise


def convert_image_to_gif(image_base64: str) -> str:
    # sourcery skip: extract-method
    """
    将Base64编码的图片转换为GIF格式
    Parameters:
        image_base64: str: Base64编码的图片数据
    Returns:
        str: Base64编码的GIF图片数据
    """
    logger.debug("转换图片为GIF格式")
    try:
        image_bytes = base64.b64decode(image_base64)
        image = Image.open(io.BytesIO(image_bytes))
        output_buffer = io.BytesIO()
        image.save(output_buffer, format="GIF")
        output_buffer.seek(0)
        return base64.b64encode(output_buffer.read()).decode("utf-8")
    except Exception as e:
        logger.error(f"图片转换为GIF失败: {e!s}")
        return image_base64


async def get_self_info(
    *,
    use_cache: bool = True,
    force_refresh: bool = False,
    adapter: "NapcatAdapter | None" = None,
) -> dict | None:
    """
    获取机器人信息
    """
    logger.debug("获取机器人信息中")
    cache_key = "self"
    if use_cache and not force_refresh:
        cached = await _get_cached("self_info", cache_key, SELF_INFO_TTL)
        if cached is not None:
            return cached

    response = await _call_adapter_api("get_login_info", {}, adapter=adapter)
    data = response.get("data") if response else None
    if data is not None and use_cache:
        await _set_cached("self_info", cache_key, data)
    return data


def get_image_format(raw_data: str) -> str:
    """
    从Base64编码的数据中确定图片的格式类型
    Parameters:
        raw_data: str: Base64编码的图片数据
    Returns:
        format: str: 图片的格式类型，如 'jpeg', 'png', 'gif'等
    """
    image_bytes = base64.b64decode(raw_data)
    return Image.open(io.BytesIO(image_bytes)).format.lower()


async def get_stranger_info(
    user_id: int,
    *,
    use_cache: bool = True,
    force_refresh: bool = False,
    adapter: "NapcatAdapter | None" = None,
) -> dict | None:
    """
    获取陌生人信息
    """
    logger.debug("获取陌生人信息中")
    cache_key = str(user_id)
    if use_cache and not force_refresh:
        cached = await _get_cached("stranger_info", cache_key, STRANGER_INFO_TTL)
        if cached is not None:
            return cached

    response = await _call_adapter_api(
        "get_stranger_info", {"user_id": user_id}, adapter=adapter
    )
    data = response.get("data") if response else None
    if data is not None and use_cache:
        await _set_cached("stranger_info", cache_key, data)
    return data


async def get_message_detail(
    message_id: str | int,
    *,
    adapter: "NapcatAdapter | None" = None,
) -> dict | None:
    """
    获取消息详情，仅作为参考
    """
    logger.debug("获取消息详情中")
    response = await _call_adapter_api(
        "get_msg",
        {"message_id": message_id},
        adapter=adapter,
        timeout=30,
    )
    return response.get("data") if response else None


async def get_record_detail(
    file: str,
    file_id: str | None = None,
    *,
    adapter: "NapcatAdapter | None" = None,
) -> dict | None:
    """
    获取语音信息详情
    """
    logger.debug("获取语音信息详情中")
    response = await _call_adapter_api(
        "get_record",
        {"file": file, "file_id": file_id, "out_format": "wav"},
        adapter=adapter,
        timeout=30,
    )
    return response.get("data") if response else None


async def get_forward_message(
    raw_message: dict, *, adapter: "NapcatAdapter | None" = None
) -> dict[str, Any] | None:
    forward_message_data: dict = raw_message.get("data", {})
    if not forward_message_data:
        logger.warning("转发消息内容为空")
        return None
    forward_message_id = forward_message_data.get("id")

    try:
        response = await _call_adapter_api(
            "get_forward_msg",
            {"message_id": forward_message_id},
            timeout=10.0,
            adapter=adapter,
        )
        if response is None:
            logger.error("获取转发消息失败，返回值为空")
            return None
    except TimeoutError:
        logger.error("获取转发消息超时")
        return None
    except Exception as e:
        logger.error(f"获取转发消息失败: {e!s}")
        return None
    logger.debug(
        f"转发消息原始格式：{orjson.dumps(response).decode('utf-8')[:80]}..."
        if len(orjson.dumps(response).decode("utf-8")) > 80
        else orjson.dumps(response).decode("utf-8")
    )
    response_data: dict = response.get("data")
    if not response_data:
        logger.warning("转发消息内容为空或获取失败")
        return None
    return response_data.get("messages")
