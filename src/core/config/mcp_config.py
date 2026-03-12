"""MCP 配置定义。

定义 MCP (Model Context Protocol) 相关的配置项。
"""

from typing import Any

from src.kernel.config import ConfigBase, Field, SectionBase, config_section


class MCPConfig(ConfigBase):
    """MCP 配置类。

    定义 MCP 服务的所有配置，包括 Stdio 和 SSE 服务。
    """

    @config_section("mcp")
    class MCPSection(SectionBase):
        """MCP 基础配置节。"""

        enabled: bool = Field(
            default=True,
            description="是否启用 MCP 功能"
        )
        
        # Stdio servers: 字典结构
        # key: server name (e.g. "filesystem")
        # value: { "command": "npx", "args": [...], "env": {...} }
        stdio_servers: dict[str, dict[str, Any]] = Field(
            default_factory=dict,
            description="基于 Stdio 的 MCP 服务器配置。Key为服务名，Value包含 command, args, env。"
        )

        # SSE servers: 字典结构
        # key: server name
        # value: url string
        sse_servers: dict[str, str] = Field(
            default_factory=dict,
            description="基于 SSE 的 MCP 服务器配置。Key为服务名，Value为URL。"
        )

    mcp: MCPSection = Field(default_factory=MCPSection)


# 全局配置实例（延迟初始化）
_global_mcp_config: MCPConfig | None = None


def get_mcp_config() -> MCPConfig:
    """获取全局 MCP 配置实例

    Returns:
        MCPConfig: 配置实例

    Raises:
        RuntimeError: 如果配置未初始化
    """
    global _global_mcp_config
    if _global_mcp_config is None:
        raise RuntimeError(
            "MCP config not initialized. "
            "Call init_mcp_config() first."
        )
    return _global_mcp_config


def init_mcp_config(config_path: str) -> MCPConfig:
    """初始化 MCP 配置

    Args:
        config_path: 配置文件路径，为 None 时使用默认配置

    Returns:
        MCPConfig: 配置实例
    """
    global _global_mcp_config

    from pathlib import Path

    path = Path(config_path)

    # 确保配置文件存在
    if not path.exists():
        # 确保父目录存在
        path.parent.mkdir(parents=True, exist_ok=True)

        # 创建默认配置文件
        default_config = MCPConfig.default()
        _global_mcp_config = MCPConfig.model_validate(default_config)

        # 保存默认配置到文件
        from src.kernel.config.core import _render_toml_with_signature
        toml_content = _render_toml_with_signature(MCPConfig, default_config)
        path.write_text(toml_content, encoding="utf-8")

    # 从文件加载配置
    _global_mcp_config = MCPConfig.load(config_path)

    return _global_mcp_config
