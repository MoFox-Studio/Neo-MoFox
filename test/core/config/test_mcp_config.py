"""测试 MCPConfig 配置模块。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.config.mcp_config import MCPConfig, get_mcp_config, init_mcp_config


class TestMCPSection:
    """测试 MCP 配置节。"""

    def test_default_mcp_section(self):
        """测试默认 MCP 配置。"""
        config = MCPConfig()

        assert config.mcp.enabled is True
        assert config.mcp.stdio_servers == {}
        assert config.mcp.sse_servers == {}

    def test_mcp_section_disabled(self):
        """测试禁用 MCP。"""
        config = MCPConfig.MCPSection(enabled=False)

        assert config.enabled is False

    def test_mcp_section_with_stdio_servers(self):
        """测试带 Stdio 服务器的配置。"""
        config = MCPConfig.MCPSection(
            stdio_servers={
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "path"],
                    "env": {"PATH": "/usr/bin"},
                },
                "git": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-git"],
                },
            }
        )

        assert len(config.stdio_servers) == 2
        assert "filesystem" in config.stdio_servers
        assert "git" in config.stdio_servers

    def test_mcp_section_with_sse_servers(self):
        """测试带 SSE 服务器的配置。"""
        config = MCPConfig.MCPSection(
            sse_servers={
                "lab": "https://api.example.com/sse/lab",
                "image": "https://api.example.com/sse/image",
            }
        )

        assert len(config.sse_servers) == 2
        assert config.sse_servers["lab"] == "https://api.example.com/sse/lab"
        assert config.sse_servers["image"] == "https://api.example.com/sse/image"

    def test_mcp_section_with_both_server_types(self):
        """测试同时使用两种服务器类型。"""
        config = MCPConfig.MCPSection(
            stdio_servers={
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                },
            },
            sse_servers={
                "lab": "https://api.example.com/sse",
            },
        )

        assert len(config.stdio_servers) == 1
        assert len(config.sse_servers) == 1


class TestMCPConfig:
    """测试 MCPConfig 主配置类。"""

    def test_create_default_config(self):
        """测试创建默认配置。"""
        config = MCPConfig()

        assert isinstance(config.mcp, MCPConfig.MCPSection)
        assert config.mcp.enabled is True

    def test_create_config_with_custom_settings(self):
        """测试创建自定义配置。"""
        config = MCPConfig(
            mcp=MCPConfig.MCPSection(
                enabled=False,
                stdio_servers={
                    "test": {
                        "command": "test-command",
                        "args": ["--arg1"],
                    },
                },
            )
        )

        assert config.mcp.enabled is False
        assert len(config.mcp.stdio_servers) == 1

    def test_access_stdio_servers(self):
        """测试访问 Stdio 服务器配置。"""
        config = MCPConfig(
            mcp=MCPConfig.MCPSection(
                stdio_servers={
                    "test": {
                        "command": "cmd",
                        "args": ["arg1"],
                        "env": {"KEY": "VALUE"},
                    },
                },
            )
        )

        test_server = config.mcp.stdio_servers["test"]
        assert test_server["command"] == "cmd"
        assert test_server["args"] == ["arg1"]
        assert test_server["env"]["KEY"] == "VALUE"

    def test_access_sse_servers(self):
        """测试访问 SSE 服务器配置。"""
        config = MCPConfig(
            mcp=MCPConfig.MCPSection(
                sse_servers={
                    "server1": "https://example.com/sse1",
                    "server2": "https://example.com/sse2",
                },
            )
        )

        assert config.mcp.sse_servers["server1"] == "https://example.com/sse1"
        assert config.mcp.sse_servers["server2"] == "https://example.com/sse2"


class TestGlobalMCPConfig:
    """测试全局 MCP 配置管理。"""

    def test_init_mcp_config_default(self, temp_dir: Path):
        """测试使用默认配置初始化。"""
        import src.core.config.mcp_config as mcp_config_module
        original_config = mcp_config_module._global_mcp_config
        mcp_config_module._global_mcp_config = None

        try:
            config_path = temp_dir / "mcp.toml"
            config = init_mcp_config(str(config_path))
            assert config is not None
            assert isinstance(config, MCPConfig)
        finally:
            mcp_config_module._global_mcp_config = original_config

    def test_init_mcp_config_from_file(self, temp_dir: Path):
        """测试从文件加载配置。"""
        import src.core.config.mcp_config as mcp_config_module
        original_config = mcp_config_module._global_mcp_config
        mcp_config_module._global_mcp_config = None

        try:
            config_file = temp_dir / "mcp.toml"
            config_file.write_text(
                """
[mcp]
enabled = true

[mcp.stdio_servers.filesystem]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem"]

[mcp.sse_servers]
lab = "https://api.example.com/sse"
"""
            )

            config = init_mcp_config(str(config_file))
            assert config.mcp.enabled is True
        finally:
            mcp_config_module._global_mcp_config = original_config

    def test_get_mcp_config_before_init_raises(self):
        """测试未初始化时获取配置抛出异常。"""
        import src.core.config.mcp_config as mcp_config_module
        original_config = mcp_config_module._global_mcp_config
        mcp_config_module._global_mcp_config = None

        try:
            with pytest.raises(RuntimeError, match="MCP config not initialized"):
                get_mcp_config()
        finally:
            mcp_config_module._global_mcp_config = original_config

    def test_get_mcp_config_after_init(self, temp_dir: Path):
        """测试初始化后获取配置。"""
        import src.core.config.mcp_config as mcp_config_module
        original_config = mcp_config_module._global_mcp_config
        mcp_config_module._global_mcp_config = None

        try:
            config_path = temp_dir / "mcp.toml"
            init_mcp_config(str(config_path))
            config = get_mcp_config()

            assert isinstance(config, MCPConfig)
        finally:
            mcp_config_module._global_mcp_config = original_config

    def test_init_mcp_config_multiple_times(self, temp_dir: Path):
        """测试多次初始化更新配置。"""
        import src.core.config.mcp_config as mcp_config_module
        original_config = mcp_config_module._global_mcp_config
        mcp_config_module._global_mcp_config = None

        try:
            config_path = temp_dir / "mcp.toml"
            config1 = init_mcp_config(str(config_path))
            config2 = init_mcp_config(str(config_path))

            # 第二次应该返回新创建的实例（因为重新初始化了）
            assert config2 is not None
            assert isinstance(config2, MCPConfig)
            # get_mcp_config 应该返回第二次初始化的实例
            config3 = get_mcp_config()
            assert config3 is config2
        finally:
            mcp_config_module._global_mcp_config = original_config


class TestMCPConfigScenarios:
    """测试 MCP 配置的实际使用场景。"""

    def test_disabled_mcp_scenario(self):
        """测试 MCP 禁用场景。"""
        config = MCPConfig(mcp=MCPConfig.MCPSection(enabled=False))

        assert config.mcp.enabled is False
        # 当禁用时，服务器配置应该为空或被忽略
        assert config.mcp.stdio_servers == {}
        assert config.mcp.sse_servers == {}

    def test_filesystem_server_scenario(self):
        """测试文件系统服务器配置场景。"""
        config = MCPConfig(
            mcp=MCPConfig.MCPSection(
                stdio_servers={
                    "filesystem": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allow"],
                        "env": {"NODE_ENV": "production"},
                    },
                },
            )
        )

        fs_config = config.mcp.stdio_servers["filesystem"]
        assert fs_config["command"] == "npx"
        assert "/path/to/allow" in fs_config["args"]
        assert fs_config["env"]["NODE_ENV"] == "production"

    def test_multiple_sse_servers_scenario(self):
        """测试多个 SSE 服务器场景。"""
        config = MCPConfig(
            mcp=MCPConfig.MCPSection(
                sse_servers={
                    "lab": "https://api.anthropic.com/sse",
                    "image": "https://api.openai.com/sse",
                    "code": "https://api.example.com/sse",
                },
            )
        )

        assert len(config.mcp.sse_servers) == 3
        assert "lab" in config.mcp.sse_servers
        assert "image" in config.mcp.sse_servers
        assert "code" in config.mcp.sse_servers

    def test_mixed_servers_scenario(self):
        """测试混合使用 Stdio 和 SSE 服务器的场景。"""
        config = MCPConfig(
            mcp=MCPConfig.MCPSection(
                stdio_servers={
                    "filesystem": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                    },
                    "git": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-git"],
                    },
                },
                sse_servers={
                    "lab": "https://api.example.com/sse/lab",
                },
            )
        )

        assert len(config.mcp.stdio_servers) == 2
        assert len(config.mcp.sse_servers) == 1
