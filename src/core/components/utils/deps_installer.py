"""插件依赖安装器。

提供对插件所声明的 Python 包依赖的检测与自动安装能力。

主要职责：
- 通过 importlib.metadata / packaging 检测包是否已满足版本要求
- 通过 asyncio.create_subprocess_exec 调用可配置的安装命令（默认 uv pip install）
- 聚合多个插件的依赖，去重后批量安装，减少 subprocess 调用次数

典型使用示例：
    from src.core.components.utils import DependencyInstaller, PluginDepSpec

    installer = DependencyInstaller()

    # 检测缺失的包
    missing = installer.check_missing(["requests>=2.28", "beautifulsoup4"])

    # 批量安装
    success = await installer.install(missing, command="uv pip install")
"""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass, field

from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import Version

from src.kernel.logger import get_logger

logger = get_logger("deps_installer")


@dataclass
class PluginDepSpec:
    """单个插件的依赖规格。

    Attributes:
        plugin_name: 插件名称（用于日志与结果映射）
        packages: pip requirement 格式的包列表，如 ["requests>=2.28"]
        required: 若为 True，安装失败时认为该插件不可加载
    """

    plugin_name: str
    packages: list[str] = field(default_factory=list)
    required: bool = True


class DependencyInstaller:
    """插件 Python 依赖检测与安装器。

    设计为无状态工具类，所有方法均可重复调用。

    Attributes:
        N/A（无实例状态，所有参数通过方法参数传入）
    """

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------

    def check_missing(self, requirements: list[str]) -> list[str]:
        """检测哪些包未安装或版本不满足要求。

        使用 importlib.metadata 查询当前环境中已安装的包版本，
        结合 packaging.requirements 解析版本约束，逐一比对。

        Args:
            requirements: pip requirement 格式字符串列表，
                如 ["requests>=2.28", "beautifulsoup4==2.0"]

        Returns:
            未满足要求的 requirement 字符串列表（保持原始格式）

        Examples:
            >>> installer = DependencyInstaller()
            >>> installer.check_missing(["requests>=2.28"])
            []  # 若已安装且版本满足
        """
        missing: list[str] = []
        for req_str in requirements:
            if not self._is_satisfied(req_str):
                missing.append(req_str)
        return missing

    async def install(self, packages: list[str], command: str = "uv pip install") -> bool:
        """调用安装命令安装一批包。

        使用 asyncio.create_subprocess_exec 执行安装，stdout/stderr 实时输出到 logger。

        Args:
            packages: 要安装的包列表（pip requirement 格式）
            command: 安装命令前缀，如 "uv pip install" 或 "pip install"

        Returns:
            True 表示安装成功（退出码 0），否则 False

        Raises:
            无（所有异常均被捕获并记录到 logger）
        """
        if not packages:
            return True

        try:
            cmd_parts = shlex.split(command) + packages
        except ValueError as e:
            logger.error(f"解析安装命令失败: {command!r} — {e}")
            return False

        logger.info(f"安装依赖: {' '.join(cmd_parts)}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if stdout:
                for line in stdout.decode(errors="replace").splitlines():
                    if line.strip():
                        logger.debug(f"[installer] {line}")

            if proc.returncode != 0:
                error_output = stderr.decode(errors="replace").strip() if stderr else ""
                logger.error(
                    f"依赖安装失败（退出码 {proc.returncode}）: {error_output}"
                )
                return False

            logger.info(f"依赖安装成功: {packages}")
            return True

        except FileNotFoundError:
            cmd_name = cmd_parts[0]
            logger.error(
                f"找不到安装命令 '{cmd_name}'，请确保已安装该工具并在 PATH 中。"
            )
            return False
        except Exception as e:
            logger.error(f"执行安装命令时出现意外错误: {e}")
            return False

    async def install_for_plugins(
        self,
        plugin_specs: list[PluginDepSpec],
        command: str = "uv pip install",
        skip_if_satisfied: bool = True,
    ) -> dict[str, bool]:
        """聚合多个插件的依赖，去重后批量安装，返回每个插件的成功/失败状态。

        流程：
        1. 收集所有插件声明的 requirement 字符串，去重后合并成一个列表
        2. 若 skip_if_satisfied=True，过滤掉已满足的包，仅安装缺失部分
        3. 调用 install() 执行批量安装
        4. 若安装成功，所有插件均视为成功；
           若安装失败，根据各插件的 required 标志决定是否标记失败

        Args:
            plugin_specs: 各插件的依赖规格列表
            command: 安装命令前缀
            skip_if_satisfied: 为 True 时跳过已满足版本的包

        Returns:
            以插件名为键、安装结果（bool）为值的字典。
            若某插件无依赖，对应值为 True。

        Examples:
            >>> specs = [PluginDepSpec("my_plugin", ["requests>=2.28"])]
            >>> results = await installer.install_for_plugins(specs)
            >>> results["my_plugin"]  # True if success
        """
        # 无规格时直接返回
        if not plugin_specs:
            return {}

        # 过滤掉无依赖的插件（直接标记成功）
        specs_with_deps = [s for s in plugin_specs if s.packages]
        results: dict[str, bool] = {
            s.plugin_name: True
            for s in plugin_specs
            if not s.packages
        }

        if not specs_with_deps:
            return results

        # 去重：所有插件依赖合并
        all_packages: list[str] = []
        seen: set[str] = set()
        for spec in specs_with_deps:
            for pkg in spec.packages:
                normalized = pkg.strip()
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    all_packages.append(normalized)

        # 可选跳过已满足的包
        to_install = (
            self.check_missing(all_packages) if skip_if_satisfied else all_packages
        )

        if not to_install:
            logger.debug("所有插件依赖均已满足，跳过安装。")
            for spec in specs_with_deps:
                results[spec.plugin_name] = True
            return results

        logger.info(
            f"需要安装 {len(to_install)} 个包（来自 {len(specs_with_deps)} 个插件）: "
            f"{to_install}"
        )

        install_ok = await self.install(to_install, command=command)

        # 根据安装结果 + required 标志分配每个插件的结果
        for spec in specs_with_deps:
            if install_ok:
                results[spec.plugin_name] = True
            else:
                results[spec.plugin_name] = False
                level = "error" if spec.required else "warning"
                getattr(logger, level)(
                    f"插件 '{spec.plugin_name}' 的依赖安装失败"
                    + ("（依赖为必需，将跳过该插件）" if spec.required else "（依赖非必需，仍尝试加载）")
                )

        return results

    # ------------------------------------------------------------------
    # 私有方法
    # ------------------------------------------------------------------

    @staticmethod
    def _is_satisfied(req_str: str) -> bool:
        """检查单个 requirement 是否已在当前环境中满足。

        Args:
            req_str: pip requirement 格式字符串，如 "requests>=2.28"

        Returns:
            True 表示已满足（已安装且版本符合约束），False 表示不满足或无法判断
        """
        import importlib.metadata as meta

        try:
            req = Requirement(req_str)
        except InvalidRequirement:
            logger.warning(f"无效的 requirement 格式，跳过检测: {req_str!r}")
            return False

        try:
            installed_version_str = meta.version(req.name)
        except meta.PackageNotFoundError:
            return False

        if not req.specifier:
            return True

        try:
            return req.specifier.contains(Version(installed_version_str), prereleases=True)
        except Exception:
            return False
