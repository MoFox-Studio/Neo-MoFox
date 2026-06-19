"""安全验证模块

提供 FastAPI 依赖项，用于验证 X-API-Key 请求头。
插件开发者可通过 VerifiedDep 保护 HTTP 端点，
通过 verify_websocket_token 校验 WebSocket 连接。

用法示例：
    from src.core.utils.security import VerifiedDep, verify_websocket_token

    # HTTP 端点
    @router.get("/protected", dependencies=[VerifiedDep])
    async def protected_endpoint():
        return {"message": "ok"}

    # WebSocket 端点
    @router.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        if not await verify_websocket_token(websocket):
            return
        await websocket.accept()
        ...
"""

from fastapi import Depends, HTTPException, Security, WebSocket
from fastapi.security.api_key import APIKeyHeader
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN

from src.kernel.logger import get_logger
from src.core.config.core_config import get_core_config

logger = get_logger("security")

_API_KEY_HEADER_NAME = "X-API-Key"
_api_key_header_auth = APIKeyHeader(name=_API_KEY_HEADER_NAME, auto_error=True)

# WebSocket 关闭码：策略违规（用于鉴权失败）
_WS_CLOSE_POLICY_VIOLATION = 1008


def _validate_api_key(api_key: str) -> None:
    """校验 API 密钥的核心逻辑（不依赖 FastAPI 注入）。

    Args:
        api_key: 待校验的 API 密钥字符串。

    Raises:
        HTTPException 401: 服务未配置任何有效密钥或配置未初始化。
        HTTPException 403: 提供的密钥无效。
    """
    try:
        config = get_core_config()
        valid_keys = config.http_router.api_keys
    except RuntimeError:
        logger.error("Core 配置未初始化，无法进行 API 密钥验证")
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="服务配置未初始化，无法验证 API 密钥",
        )

    if not valid_keys:
        logger.warning("API 密钥认证已启用，但 http_router.api_keys 为空，所有请求将被拒绝。")
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="服务未配置 API 密钥，请在 config/core.toml 的 [http_router] 节中设置 api_keys",
        )

    if api_key not in valid_keys:
        prefix = api_key[:4] if api_key else ""
        logger.warning(f"无效的 API 密钥（前4位）: {prefix}****")
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="无效的 API 密钥",
        )


async def get_api_key(api_key: str = Security(_api_key_header_auth)) -> str:
    """FastAPI 依赖项：验证 X-API-Key 请求头。

    从请求头中提取 X-API-Key，并校验其是否存在于
    ``http_router.api_keys`` 配置列表中。

    当 ``api_keys`` 列表为空时，所有请求都将被拒绝（服务未正确配置）。

    Args:
        api_key: 由 FastAPI 自动从请求头提取的 API 密钥。

    Returns:
        str: 验证通过的 API 密钥。

    Raises:
        HTTPException 401: 服务未配置任何有效密钥。
        HTTPException 403: 提供的密钥无效。
    """
    _validate_api_key(api_key)
    return api_key


async def verify_websocket_token(
    websocket: WebSocket,
    token_query_param: str = "token",
) -> bool:
    """校验 WebSocket 连接的鉴权令牌。

    从 WebSocket 查询参数中提取令牌，并使用与 HTTP 端点一致的
    ``http_router.api_keys`` 校验逻辑进行验证。校验失败时会自动
    通过 ``websocket.close`` 以策略违规（1008）关闭连接。

    调用方应在 ``websocket.accept()`` 之前调用本函数，并根据
    返回值决定是否继续处理连接。

    Args:
        websocket: 待校验的 WebSocket 连接对象。
        token_query_param: 查询参数名称，默认为 ``token``。

    Returns:
        bool: 校验通过返回 True；校验失败返回 False，并已关闭连接。
    """
    token = websocket.query_params.get(token_query_param)
    if token is None:
        await websocket.close(code=_WS_CLOSE_POLICY_VIOLATION, reason="缺少认证令牌")
        return False

    try:
        _validate_api_key(token)
    except HTTPException as exc:
        await websocket.close(code=_WS_CLOSE_POLICY_VIOLATION, reason=str(exc.detail))
        return False
    return True


# 可直接用于 FastAPI 依赖的可重用对象
# 用法: @router.get("/protected", dependencies=[VerifiedDep])
VerifiedDep = Depends(get_api_key)

__all__ = [
    "get_api_key",
    "verify_websocket_token",
    "VerifiedDep",
]
