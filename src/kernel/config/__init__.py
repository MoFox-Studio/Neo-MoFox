"""src.kernel.config

内核配置模块。

该模块提供基于 Pydantic 的类型安全配置文件系统，支持自动类型校验与 TOML 存储。

本模块采用“静态可见”的配置模型设计：配置类本身继承 :class:`pydantic.BaseModel`，
并在类体中显式声明各配置节字段（这样 IDE/Pylance 能正确推断类型）。

典型使用示例：
    ```python
    from src.kernel.config import ConfigBase, SectionBase, config_section, Field

    class MyConfig(ConfigBase):
        @config_section("general")
        class GeneralSection(SectionBase):
            enabled: bool = Field(
                default=True,
                description="启用功能",
                label="功能开关",
                icon="power_settings_new"
            )
            temperature: float = Field(
                default=0.7,
                ge=0.0,
                le=2.0,
                step=0.1,
                description="生成温度",
                icon="thermostat"
            )

        general: GeneralSection = Field(default_factory=GeneralSection)

    my_config = MyConfig.load("config/my_config.toml")
    print(my_config.general.enabled)
    ```
"""

from __future__ import annotations

from .core import ConfigBase, SectionBase, config_section, Field
from .types import ConfigData, SectionData, TOMLData

__all__ = [
    "ConfigBase",
    "SectionBase",
    "config_section",
    "Field",  # 增强的配置字段定义函数（支持 WebUI，覆盖 Pydantic Field）
    # 类型定义
    "ConfigData",
    "SectionData",
    "TOMLData",
]
