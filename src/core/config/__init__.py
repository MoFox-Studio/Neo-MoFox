"""Core 层配置模块

本模块定义 Core 层所需的配置项，使用 kernel/config 的配置系统。

使用示例：
    ```python
    from src.core.config import init_core_config, get_core_config

    # 初始化配置（在使用前必须调用一次）
    init_core_config("config/core.toml")

    # 获取配置实例
    config = get_core_config()
    print(config.database.database_type)
    
    # 初始化模型配置
    from src.core.config import init_model_config, get_model_config
    init_model_config("config/models.toml")
    
    # 获取模型配置
    model_config = get_model_config()
    task = model_config.model_tasks.replyer
    print(task.model_list)
    ```
"""

from .core_config import (
    CoreConfig,
    get_core_config,
    init_core_config,
)
from .mcp_config import (
    MCPConfig,
    get_mcp_config,
    init_mcp_config,
)
from .model_config import (
    ModelConfig,
    get_model_config,
    init_model_config,
    APIProviderSection,
    ModelInfoSection,
    TaskConfigSection,
    ModelTasksSection,
)

__all__ = [
    # Core 配置
    "CoreConfig",
    "get_core_config",
    "init_core_config",
    # MCP 配置
    "MCPConfig",
    "get_mcp_config",
    "init_mcp_config",
    # Model 配置
    "ModelConfig",
    "get_model_config",
    "init_model_config",
    "APIProviderSection",
    "ModelInfoSection",
    "TaskConfigSection",
    "ModelTasksSection",
]
