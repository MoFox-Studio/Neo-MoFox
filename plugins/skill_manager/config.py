"""SkillManager 插件配置。"""

from __future__ import annotations

from typing import ClassVar

from src.core.components.base import BaseConfig
from src.kernel.config.core import Field, SectionBase, config_section


class SkillManagerConfig(BaseConfig):
    """SkillManager 配置模型。"""

    name: ClassVar[str] = "config"
    description: ClassVar[str] = "SkillManager 配置"

    @config_section("manager", title="技能管理", tag="plugin")
    class ManagerSection(SectionBase):
        """技能管理主配置。"""

        enabled: bool = Field(
            default=True,
            description="是否启用 SkillManager",
        )
        paths: list[str] = Field(
            default_factory=lambda: ["skill"],
            description="skill 根目录路径列表；相对路径默认相对项目根目录",
        )
        inject_actor_reminder: bool = Field(
            default=True,
            description="是否注入 actor system reminder",
        )
        inject_sub_actor_reminder: bool = Field(
            default=True,
            description="是否注入 sub_actor system reminder",
        )

    manager: ManagerSection = Field(default_factory=ManagerSection)

    @config_section("security", title="脚本执行安全", tag="security")
    class SecuritySection(SectionBase):
        """脚本执行相关的安全开关。

        这些开关一律默认取最严的一档：``get_script`` 以 bot 进程权限执行脚本，而工具
        调用层不存在权限模型，任何聊天用户都能通过提示注入影响 LLM 的工具调用参数。
        """

        allow_script_execution: bool = Field(
            default=False,
            description=(
                "是否允许通过 get_script 执行 skill 脚本；关闭时该工具不会注册。"
                "开启后 LLM 可控制脚本的全部命令行参数（等价于可触达脚本声明的完整"
                "参数面），请仅在信任 skill 目录内脚本的前提下开启（高风险，默认关闭）"
            ),
        )
        script_execution_permission_level: str = Field(
            default="owner",
            description=(
                "执行 skill 脚本所需的调用者最低权限级别；"
                "默认 owner，与 /skill 管理命令的权限门对齐"
            ),
            input_type="select",
            choices=["guest", "user", "operator", "owner"],
        )
        powershell_bypass_execution_policy: bool = Field(
            default=False,
            description=(
                "执行 .ps1 脚本时是否附加 -ExecutionPolicy Bypass；"
                "开启会绕过 PowerShell 执行策略与脚本签名校验（高风险，默认关闭）"
            ),
        )
        powershell_no_profile: bool = Field(
            default=True,
            description=(
                "执行 .ps1 脚本时是否附加 -NoProfile；"
                "开启表示跳过用户 profile 脚本（与 skill 无关的额外代码），"
                "关闭则沿用运维为该账户配置的 profile"
            ),
        )
        powershell_non_interactive: bool = Field(
            default=True,
            description=(
                "执行 .ps1 脚本时是否附加 -NonInteractive；"
                "开启表示脚本请求交互输入时直接失败，关闭则让脚本挂到执行超时"
            ),
        )

    security: SecuritySection = Field(default_factory=SecuritySection)


def resolve_security_section(config: object) -> SkillManagerConfig.SecuritySection:
    """取出生效的 ``[security]`` 配置段。

    配置未加载或类型不符时回退到一个全默认实例。该段所有默认值都取最严的一档，
    因此回退等价于 fail-closed，不会因为配置缺失而放开脚本执行。

    Args:
        config: 待解析的配置对象，通常是 ``BasePlugin.config``。

    Returns:
        SkillManagerConfig.SecuritySection: 生效的安全配置段。
    """

    if isinstance(config, SkillManagerConfig):
        return config.security
    return SkillManagerConfig.SecuritySection()
