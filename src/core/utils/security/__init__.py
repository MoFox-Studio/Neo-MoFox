"""安全验证模块

提供 FastAPI 依赖项，用于验证 X-API-Key 请求头。
插件开发者可通过 VerifiedDep 保护需要认证的端点。

用法示例：
    from src.core.utils.security import VerifiedDep

    @router.get("/protected", dependencies=[VerifiedDep])
    async def protected_endpoint():
        return {"message": "ok"}

    # 或者显式注入
    @router.get("/protected")
    async def protected_endpoint(_: str = VerifiedDep):
        return {"message": "ok"}
"""

from fastapi import Depends, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN

from src.kernel.logger import get_logger
from src.core.config.core_config import get_core_config

logger = get_logger("security")

_API_KEY_HEADER_NAME = "X-API-Key"
_api_key_header_auth = APIKeyHeader(name=_API_KEY_HEADER_NAME, auto_error=True)


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
        logger.warning(f"无效的 API 密钥（前4位）: {api_key[:4]}****")
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="无效的 API 密钥",
        )

    return api_key


# 可直接用于 FastAPI 依赖的可重用对象
# 用法: @router.get("/protected", dependencies=[VerifiedDep])
VerifiedDep = Depends(get_api_key)

__all__ = [
    "get_api_key",
    "VerifiedDep",
]
