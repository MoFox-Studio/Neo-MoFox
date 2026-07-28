"""插件加载器和注册系统。

本模块包含两层职责：
1) 运行时插件类注册：提供 @register_plugin 装饰器和注册表查询。
2) 宏观插件加载入口：负责从插件目录发现插件、读取 manifest、检查依赖/版本、
    计算加载顺序，并委托 PluginManager 执行单个插件的导入与组件注册。

设计原则：宏观层面的依赖/版本/计划由 loader 负责；单插件加载由 PluginManager 负责。
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from src.core.config import CORE_VERSION
from src.kernel.logger import get_logger

logger = get_logger("plugin_loader")


_PLUGIN_DEPENDENCY_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z0-9_-]+)(?P<spec>\s*(?:===|==|!=|~=|>=|<=|>|<).+)?$"
)


def _split_plugin_dependency_ref(ref: str) -> tuple[str, str | None]:
    """解析插件依赖引用，返回名称和版本约束。

    Args:
        ref: 插件依赖引用字符串，例如 "plugin_name>=1.0.0"

    Returns:
        一个元组，包含插件名称和可选的版本约束字符串。
    """

    value = str(ref or "").strip()
    if not value:
        return "", None

    name, separator, remainder = value.partition(":")
    if separator and remainder.lstrip().startswith(
        ("===", "==", "!=", "~=", ">=", "<=", ">", "<")
    ):
        return name.strip(), remainder.strip() or None

    match = _PLUGIN_DEPENDENCY_PATTERN.match(value)
    if match:
        return match.group("name"), (match.group("spec") or "").strip() or None

    return value, None


def _find_manifest_in_zip(zf: zipfile.ZipFile) -> str | None:
    """在 ZIP 中查找 manifest.json，支持根级和一级子目录。

    常见的打包方式有两种：
    1. manifest.json 直接在 zip 根级
    2. plugin_name/manifest.json（带一层子目录前缀）

    Returns:
        manifest.json 在 zip 内的路径，未找到返回 None
    """
    namelist = zf.namelist()
    # 1) 根级
    if "manifest.json" in namelist:
        return "manifest.json"
    # 2) 一级子目录
    for name in namelist:
        # 匹配 "xxx/manifest.json" 形式
        parts = name.replace("\\", "/").split("/")
        if len(parts) == 2 and parts[1] == "manifest.json":
            return name
    return None


def _get_zip_root_prefix(zf: zipfile.ZipFile) -> str:
    """获取 ZIP 内的根目录前缀（如果存在）。

    如果 zip 内所有内容都在同一个子目录下，返回该子目录名（含尾部 /）；
    否则返回空字符串。
    """
    namelist = zf.namelist()
    if not namelist:
        return ""
    # 检查是否所有条目都以同一前缀开头
    first = namelist[0]
    if "/" in first:
        prefix = first.split("/")[0] + "/"
        if all(n.startswith(prefix) for n in namelist):
            return prefix
    return ""


if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from src.core.managers import PluginManager

# 全局插件注册表
_plugin_registry: dict[str, type["BasePlugin"]] = {}


def register_plugin(cls: type["BasePlugin"]) -> type["BasePlugin"]:
    """注册插件类装饰器。

    此装饰器用于将插件类注册到全局插件注册表。
    每个插件必须定义 'plugin_name' 属性。

    Args:
        cls: 要注册的插件类

    Returns:
        注册后的类（本身不变）

    Raises:
        ValueError: 如果未定义 plugin_name 或插件已注册

    Examples:
        >>> @register_plugin
        ... class MyPlugin(BasePlugin):
        ...     plugin_name = "my_plugin"
        ...     plugin_description = "我的超棒插件"
        ...
        >>> # 插件现已注册，可以通过 get_plugin_class() 检索
    """
    # 检查是否定义了 plugin_name
    if not hasattr(cls, "plugin_name") or not cls.plugin_name:
        raise ValueError(f"插件类 '{cls.__name__}' 必须定义 'plugin_name' 属性")

    plugin_name = cls.plugin_name

    # 检查重复注册
    if plugin_name in _plugin_registry:
        raise ValueError(
            f"插件 '{plugin_name}' 已被 "
            f"'{_plugin_registry[plugin_name].__name__}' 注册"
        )

    # 注册插件
    _plugin_registry[plugin_name] = cls

    return cls


def get_plugin_class(plugin_name: str) -> type["BasePlugin"] | None:
    """通过名称获取已注册的插件类。

    Args:
        plugin_name: 要检索的插件名称

    Returns:
        如果找到返回插件类，否则返回 None

    Examples:
        >>> plugin_cls = get_plugin_class("my_plugin")
        >>> if plugin_cls:
        ...     plugin_instance = plugin_cls(config)
    """
    return _plugin_registry.get(plugin_name)


def list_registered_plugins() -> list[str]:
    """列出所有已注册的插件名称。

    Returns:
        已注册的插件名称列表

    Examples:
        >>> plugins = list_registered_plugins()
        >>> ['my_plugin', 'other_plugin', 'awesome_plugin']
    """
    return list(_plugin_registry.keys())


def is_plugin_registered(plugin_name: str) -> bool:
    """检查插件是否已注册。

    Args:
        plugin_name: 要检查的插件名称

    Returns:
        如果插件已注册返回 True，否则返回 False

    Examples:
        >>> if is_plugin_registered("my_plugin"):
        ...     print("插件已加载")
    """
    return plugin_name in _plugin_registry


def unregister_plugin(plugin_name: str) -> bool:
    """注销插件。

    从注册表中移除插件。主要用于测试目的。

    Args:
        plugin_name: 要注销的插件名称

    Returns:
        如果插件已注销返回 True，如果未找到返回 False

    Examples:
        >>> unregister_plugin("my_plugin")
        True
    """
    if plugin_name in _plugin_registry:
        del _plugin_registry[plugin_name]
        return True
    return False


def clear_registry() -> None:
    """清除所有已注册的插件。

    从注册表中移除所有插件。主要用于测试目的。

    Examples:
        >>> clear_registry()
    """
    _plugin_registry.clear()


def get_registry_count() -> int:
    """获取已注册插件的数量。

    Returns:
        已注册插件的数量

    Examples:
        >>> count = get_registry_count()
        >>> 5
    """
    return len(_plugin_registry)


@dataclass
class ComponentInclude:
    """组件包含声明。

    用于在 manifest.json 中声明插件包含的组件及其依赖项。

    Attributes:
        component_type: 组件类型（action, tool, chatter, command, collection, event_handler, adapter, service, router）
        component_name: 组件名称
        dependencies: 该组件依赖的其他组件签名列表
        enabled: 是否启用该组件（默认 True）
    """

    component_type: str
    component_name: str
    dependencies: list[str]  # 组件签名列表，如 ["other_plugin:tool:calculator"]
    enabled: bool = True


@dataclass
class PluginManifest:
    """插件清单数据。

    表示插件的 manifest.json 文件内容。

    Attributes:
        name: 唯一的插件名称/标识符
        version: 插件版本字符串
        description: 人类可读的描述
        author: 插件作者名称
        dependencies: 包含 'plugins' 和 'components' 列表的字典
        include: 插件包含的组件列表及组件级依赖
        entry_point: 相对于插件根目录的 Python 入口点文件
        api_version: 插件要求的插件 API 版本。支持字符串（等价于对全部
            API 模块应用同一要求）与 ``dict[str, str]``（仅校验声明模块）
            两种形式。详见 ``PLUGIN_API_VERSIONS`` 与
            ``_check_api_version_compatibility``。
        min_core_version: 所需的最低核心版本。声明插件依赖的核心能力
            （如某些新事件、新的核心组件机制等），基于 ``CORE_VERSION``
            做简单 ``>=`` 比较。与 ``api_version`` 同等判断：只要任一项
            声明且不满足即拒绝注册。留空表示不声明核心版本约束。
        python_dependencies: 插件所需的 Python 包列表（pip requirement 格式，如 "requests>=2.28"）
        dependencies_required: 若为 True，Python 依赖安装失败时跳过该插件；
            若为 False，仅发出警告，仍尝试加载
        _source_path: 内部：插件加载来源路径
    """

    name: str
    version: str
    description: str
    author: str
    dependencies: dict[str, list[str]] = field(
        default_factory=lambda: {"plugins": [], "components": []}
    )
    include: list[ComponentInclude] = field(default_factory=list)
    entry_point: str = "plugin.py"
    api_version: str | dict[str, str] = ""
    min_core_version: str = ""
    python_dependencies: list[str] = field(default_factory=list)
    dependencies_required: bool = True
    _source_path: str = ""  # 内部：清单加载来源路径


async def load_manifest(plugin_path: str) -> PluginManifest | None:
    """从插件路径读取并解析 manifest.json。

    支持文件夹、ZIP 和 .MFP（本质为 ZIP）。
    """
    try:
        if plugin_path.endswith((".zip", ".mfp")):
            with zipfile.ZipFile(plugin_path, "r") as zf:
                manifest_entry = _find_manifest_in_zip(zf)
                if manifest_entry is None:
                    logger.error(f"manifest.json 不存在: {plugin_path}")
                    return None
                manifest_data = json.loads(zf.read(manifest_entry).decode("utf-8"))
        else:
            manifest_file = Path(plugin_path) / "manifest.json"
            if not manifest_file.exists():
                logger.error(f"manifest.json 不存在: {manifest_file}")
                return None
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest_data = json.load(f)

        required_fields = [
            "name",
            "version",
            "description",
            "author",
            "dependencies",
            "entry_point",
        ]
        for field_name in required_fields:
            if field_name not in manifest_data:
                logger.error(
                    f"manifest.json 缺少必需字段: {field_name} ({plugin_path})"
                )
                return None

        include_list: list[ComponentInclude] = []
        for item in manifest_data.get("include", []) or []:
            try:
                include_list.append(
                    ComponentInclude(
                        component_type=item.get("component_type", ""),
                        component_name=item.get("component_name", ""),
                        dependencies=item.get("dependencies", []) or [],
                        enabled=bool(item.get("enabled", True)),
                    )
                )
            except Exception as e:
                logger.warning(f"解析 include 项失败 ({plugin_path}): {e}")

        # 归一化 api_version：接受字符串与 dict 两种形式
        raw_api_version = manifest_data.get("api_version", "")
        if isinstance(raw_api_version, dict):
            api_version: str | dict[str, str] = {
                str(k): str(v) for k, v in raw_api_version.items()
            }
        else:
            api_version = str(raw_api_version or "")

        return PluginManifest(
            name=manifest_data["name"],
            version=manifest_data["version"],
            description=manifest_data.get("description", ""),
            author=manifest_data.get("author", ""),
            dependencies=manifest_data.get(
                "dependencies", {"plugins": [], "components": []}
            )
            or {"plugins": [], "components": []},
            include=include_list,
            entry_point=manifest_data.get("entry_point", "plugin.py"),
            api_version=api_version,
            min_core_version=str(manifest_data.get("min_core_version", "") or ""),
            python_dependencies=manifest_data.get("python_dependencies", []) or [],
            dependencies_required=bool(
                manifest_data.get("dependencies_required", True)
            ),
            _source_path=plugin_path,
        )

    except Exception as e:
        logger.error(f"加载 manifest.json 失败 ({plugin_path}): {e}")
        return None


class PluginLoader:
    """宏观插件加载器（入口点）。

    负责：发现插件、读取清单、依赖/版本检查、计算加载顺序。
    不负责：导入执行插件模块细节、组件注册细节（委托 PluginManager）。
    """

    def __init__(self) -> None:
        self._failed_plugins: dict[str, str] = {}

    def get_failed_plugins(self) -> dict[str, str]:
        return self._failed_plugins.copy()

    async def discover_plugins(self, plugins_dir: str) -> list[str]:
        """扫描插件目录，返回可用插件路径列表。"""
        discovered: list[str] = []
        plugins_path = Path(plugins_dir)

        if not plugins_path.exists():
            logger.warning(f"插件目录不存在: {plugins_dir}")
            return discovered

        for item in sorted(plugins_path.iterdir(), key=lambda path: path.name.lower()):
            if (
                item.is_dir()
                and not item.name.startswith(".")
                and not item.name.startswith("__")
            ):
                manifest_path = item / "manifest.json"
                if manifest_path.exists():
                    discovered.append(str(item))
                    logger.debug(f"发现插件文件夹: {item}")
            elif item.suffix in (".zip", ".mfp"):
                try:
                    with zipfile.ZipFile(item, "r") as zf:
                        if _find_manifest_in_zip(zf) is not None:
                            discovered.append(str(item))
                            logger.debug(f"发现插件压缩包: {item}")
                except Exception as e:
                    logger.warning(f"无法读取压缩包 {item}: {e}")

        logger.info(f"在 {plugins_dir} 中发现 {len(discovered)} 个插件")
        return discovered

    def _check_version_compatibility(
        self, manifest: PluginManifest
    ) -> tuple[bool, str]:
        """检查插件版本兼容性（AND 语义）。

        ``api_version`` 与 ``min_core_version`` 同等判断：

        - 声明了 ``api_version`` 就校验；声明了 ``min_core_version`` 就校验。
        - 只要任一项声明且不满足 → **拒绝注册**。
        - 两者都未声明 → 允许加载但发出警告。

        两字段各司其职：
        - ``api_version``：声明插件依赖的 **插件 API 模块** 版本（按 20 个
          ``*_api`` 模块逐一校验）。
        - ``min_core_version``：声明插件依赖的 **核心能力** 版本（如某些新事件、
          新的核心组件机制），基于 ``CORE_VERSION`` 做简单 ``>=`` 比较。

        Returns:
            (is_compatible, reason): 兼容性结果与原因说明
        """
        failures: list[str] = []
        warnings: list[str] = []
        checked_any = False

        if manifest.api_version:
            checked_any = True
            ok, reason = self._check_api_version_compatibility(manifest)
            if not ok:
                failures.append(reason)
            elif reason and reason != "兼容":
                warnings.append(f"api_version: {reason}")

        if manifest.min_core_version:
            checked_any = True
            ok, reason = self._check_core_version_compatibility(manifest)
            if not ok:
                failures.append(reason)
            elif reason and "兼容" not in reason:
                warnings.append(f"min_core_version: {reason}")

        if not checked_any:
            logger.warning(
                f"插件 '{manifest.name}' 未声明 api_version 或 min_core_version，"
                "无法保证兼容性，将尝试加载"
            )
            return True, "未声明版本要求，已尝试加载"

        if failures:
            reason = "；".join(failures)
            logger.warning(f"插件 '{manifest.name}' 版本不兼容：{reason}")
            return False, reason

        if warnings:
            return True, "；".join(warnings)

        return True, "兼容"

    def _check_api_version_compatibility(
        self, manifest: PluginManifest
    ) -> tuple[bool, str]:
        """基于 ``PLUGIN_API_VERSIONS`` 的逐模块语义化版本兼容检查。

        ``manifest.api_version`` 支持两种形式：

        - **字符串**（如 ``"1.0.0"``）：等价于对全部 20 个 API 模块都要求该版本。
          现存使用字符串形式的 manifest 零改动即可继续加载。
        - **dict**（如 ``{"llm_api": "1.0.0", "send_api": "1.2.0"}``）：仅校验
          声明的 keys，未声明的模块不校验。dict 的 key 必须是合法 API 模块名，
          任何未知 key 会被拒绝（防止拼写错误被静默接受）。

        每个模块的比对规则（major/minor/micro 整数比较）：

        - major 不一致 → 拒绝（破坏性变更）
        - core 低于插件要求 → 拒绝（核心过旧）
        - 否则 → 兼容（次版本号一般保持兼容，不再发出警告）
        """
        from src.app.plugin_system.api import PLUGIN_API_VERSIONS  # lazy import

        # 1) 归一化为 dict 形式
        if isinstance(manifest.api_version, str):
            if not manifest.api_version:
                reason = "api_version 为空字符串，无法校验"
                logger.error(f"插件 '{manifest.name}' {reason}")
                return False, reason
            # 字符串形式：等价于对所有 API 模块应用同一要求
            plugin_reqs: dict[str, str] = {
                name: manifest.api_version for name in PLUGIN_API_VERSIONS
            }
        else:
            plugin_reqs = dict(manifest.api_version)

        # 2) 校验所有 key 都是已知 API 模块
        unknown = sorted(set(plugin_reqs) - set(PLUGIN_API_VERSIONS))
        if unknown:
            reason = f"manifest 声明了未知的 API 模块: {', '.join(unknown)}"
            logger.error(f"插件 '{manifest.name}' {reason}")
            return False, reason

        # 3) 逐模块比对
        reject_reasons: list[str] = []
        for api_name, plugin_req_str in plugin_reqs.items():
            core_ver_str = PLUGIN_API_VERSIONS[api_name]
            try:
                plugin_req = Version(plugin_req_str)
                core_api = Version(core_ver_str)
            except InvalidVersion as e:
                reason = (
                    f"API '{api_name}' 版本号格式无效："
                    f"插件='{plugin_req_str}', 核心='{core_ver_str}' - {e}"
                )
                logger.error(f"插件 '{manifest.name}' {reason}")
                return False, reason

            if core_api.major != plugin_req.major:
                reject_reasons.append(
                    f"API '{api_name}' 主版本不匹配 "
                    f"(插件={plugin_req.major}, 核心={core_api.major})"
                )
                continue

            if core_api.minor < plugin_req.minor or (
                core_api.minor == plugin_req.minor and core_api.micro < plugin_req.micro
            ):
                reject_reasons.append(
                    f"API '{api_name}' 核心版本 {core_ver_str} 低于插件要求 {plugin_req_str}"
                )
                continue

        # 4) 聚合结果
        if reject_reasons:
            reason = "；".join(reject_reasons)
            logger.warning(f"插件 '{manifest.name}' API 版本不兼容：{reason}")
            return False, reason

        return True, "兼容"

    def _check_core_version_compatibility(
        self, manifest: PluginManifest
    ) -> tuple[bool, str]:
        """基于 ``CORE_VERSION`` 的核心版本兼容检查。

        ``min_core_version`` 声明插件依赖的核心能力版本（如某些新事件、新的核心
        组件机制），与 ``api_version`` 同等判断。当 ``api_version`` 与
        ``min_core_version`` 同时声明时，两者必须都满足才能通过注册。
        """
        try:
            current_version = Version(CORE_VERSION)
            required_version = Version(manifest.min_core_version)
            is_compatible = current_version >= required_version
        except InvalidVersion as e:
            reason = (
                f"版本号格式无效：min_core_version='{manifest.min_core_version}'，"
                f"CORE_VERSION='{CORE_VERSION}' - {e}"
            )
            logger.error(f"插件 '{manifest.name}' {reason}")
            return False, reason

        if not is_compatible:
            return (False, f"核心版本不兼容，需要 {manifest.min_core_version}")

        return True, "兼容"

    def _parse_plugin_ref(self, ref: str) -> str:
        return _split_plugin_dependency_ref(ref)[0]

    def _matches_plugin_dependency(
        self, dependency_version: str, version_spec: str | None
    ) -> bool:
        """检查插件依赖版本是否满足约束。

        Args:
            dependency_version: 依赖插件的版本字符串
            version_spec: 版本约束字符串，例如 ">=1.0.0"

        Returns:
            bool: 如果依赖版本满足约束，返回 True；否则返回 False
        """

        if not version_spec:
            return True
        try:
            return Version(dependency_version) in SpecifierSet(version_spec)
        except (InvalidSpecifier, InvalidVersion) as error:
            logger.warning(
                f"插件依赖版本约束无效，无法校验: version='{dependency_version}', spec='{version_spec}' - {error}"
            )
            return False

    def _prune_unloadable_plugins(
        self, manifests: dict[str, PluginManifest]
    ) -> dict[str, PluginManifest]:
        """剔除缺失依赖/版本不兼容的插件，返回最终可加载集合。"""
        loadable = dict(manifests)

        # 版本兼容性先筛一轮
        for name in list(loadable.keys()):
            manifest = loadable[name]
            compatible, reason = self._check_version_compatibility(manifest)
            if not compatible:
                self._failed_plugins[name] = reason
                del loadable[name]

        changed = True
        while changed:
            changed = False
            for name in list(loadable.keys()):
                manifest = loadable[name]
                deps = [
                    _split_plugin_dependency_ref(dep_ref)
                    for dep_ref in manifest.dependencies.get("plugins", [])
                ]
                missing: list[str] = []
                incompatible: list[str] = []
                invalid: list[str] = []
                for dep_name, version_spec in deps:
                    if not dep_name:
                        invalid.append(str(version_spec or ""))
                        continue
                    dependency_manifest = loadable.get(dep_name)
                    if dependency_manifest is None:
                        missing.append(dep_name)
                        continue
                    if not self._matches_plugin_dependency(
                        dependency_manifest.version, version_spec
                    ):
                        incompatible.append(
                            f"{dep_name}{version_spec or ''} (当前 {dependency_manifest.version})"
                        )

                if missing or incompatible or invalid:
                    # 依赖可能是“未发现”或“因不兼容/缺失被剔除”
                    reasons: list[str] = []
                    if missing:
                        reasons.append(
                            "依赖插件不可用: " + ", ".join(sorted(set(missing)))
                        )
                    if incompatible:
                        reasons.append("依赖插件版本不满足: " + ", ".join(incompatible))
                    if invalid:
                        reasons.append("依赖声明无效: " + ", ".join(invalid))
                    self._failed_plugins[name] = "；".join(reasons)
                    del loadable[name]
                    changed = True

        return loadable

    async def plan_plugins(
        self, plugins_dir: str
    ) -> tuple[list[str], dict[str, PluginManifest]]:
        """构建加载计划：返回 (load_order, manifests_to_load)。"""
        self._failed_plugins.clear()

        discovered = await self.discover_plugins(plugins_dir)
        if not discovered:
            return [], {}

        manifests: dict[str, PluginManifest] = {}
        for path in discovered:
            manifest = await load_manifest(path)
            if not manifest:
                self._failed_plugins[path] = "无法加载 manifest.json"
                continue
            manifests[manifest.name] = manifest

        # 剔除缺失依赖/不兼容插件（并递归影响依赖它的插件）
        loadable = self._prune_unloadable_plugins(manifests)
        if not loadable:
            return [], {}

        resolver = PluginDependencyResolver()
        for manifest in loadable.values():
            resolver.add_plugin(manifest)

        cycle = resolver.check_circular_dependency()
        if cycle:
            cycle_str = " -> ".join(cycle)
            raise ValueError(f"检测到循环依赖: {cycle_str}")

        load_order = resolver.resolve_load_order()
        logger.info(f"插件加载顺序: {' -> '.join(load_order)}")
        return load_order, loadable

    async def load_all_plugins(
        self,
        plugins_dir: str,
        *,
        plugin_manager: "PluginManager | None" = None,
    ) -> dict[str, bool]:
        """按计划加载插件，并委托 PluginManager 执行单插件加载。"""
        from src.core.managers import get_plugin_manager

        manager = plugin_manager or get_plugin_manager()
        load_order, manifests_to_load = await self.plan_plugins(plugins_dir)
        if not load_order:
            if self._failed_plugins:
                logger.warning("无可加载插件，失败原因如下：")
                for name, reason in self._failed_plugins.items():
                    logger.warning(f"  - {name}: {reason}")
            return {}

        results: dict[str, bool] = {}
        for plugin_name in load_order:
            manifest = manifests_to_load[plugin_name]
            success = await manager.load_plugin_from_manifest(
                manifest._source_path,
                manifest,
            )
            results[plugin_name] = success

        return results


_global_plugin_loader: PluginLoader | None = None


def get_plugin_loader() -> PluginLoader:
    global _global_plugin_loader
    if _global_plugin_loader is None:
        _global_plugin_loader = PluginLoader()
    return _global_plugin_loader


async def load_all_plugins(plugins_dir: str) -> dict[str, bool]:
    """便捷入口：使用全局 PluginLoader 加载目录下所有插件。"""
    return await get_plugin_loader().load_all_plugins(plugins_dir)


class PluginDependencyResolver:
    """使用拓扑排序的插件依赖解析器。

    分析插件依赖关系并确定正确的加载顺序以满足所有依赖。
    使用 Kahn 算法进行拓扑排序，使用 DFS 进行循环检测。

    Attributes:
        _plugins: 按名称索引的插件清单字典

    Examples:
        >>> resolver = PluginDependencyResolver()
        >>> resolver.add_plugin(manifest1)
        >>> resolver.add_plugin(manifest2)
        >>> load_order = resolver.resolve_load_order()
        >>> ['plugin1', 'plugin2']  # plugin2 依赖于 plugin1
    """

    def __init__(self) -> None:
        """初始化依赖解析器。"""
        self._plugins: dict[str, PluginManifest] = {}

    def add_plugin(self, manifest: PluginManifest) -> None:
        """将插件添加到依赖图。

        Args:
            manifest: 要添加的插件清单

        Examples:
            >>> resolver.add_plugin(plugin_manifest)
        """
        self._plugins[manifest.name] = manifest

    def resolve_load_order(self) -> list[str]:
        """使用拓扑排序解析插件加载顺序。

        基于插件的依赖关系使用 Kahn 算法确定正确的加载顺序。

        Returns:
            按依赖顺序排列的插件名称列表

        Raises:
            ValueError: 如果检测到循环依赖

        Examples:
            >>> order = resolver.resolve_load_order()
            >>> ['base_plugin', 'dependent_plugin', 'another_dependent']
        """
        # 构建依赖图
        in_degree: dict[str, int] = {name: 0 for name in self._plugins}
        graph: dict[str, set[str]] = {name: set() for name in self._plugins}

        for plugin_name, manifest in self._plugins.items():
            # 处理插件依赖
            for dep_ref in manifest.dependencies.get("plugins", []):
                dep_name = self._parse_plugin_ref(dep_ref)
                if dep_name in self._plugins:
                    graph[dep_name].add(plugin_name)
                    in_degree[plugin_name] += 1

        # Kahn 算法拓扑排序
        queue = sorted(name for name, degree in in_degree.items() if degree == 0)
        load_order = []

        while queue:
            current = queue.pop(0)
            load_order.append(current)

            for dependent in sorted(graph[current]):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)
            queue.sort()

        # 检查循环依赖
        if len(load_order) != len(self._plugins):
            remaining = [name for name in self._plugins if name not in load_order]
            raise ValueError(f"检测到循环依赖，涉及的插件: {remaining}")

        return load_order

    def check_circular_dependency(self) -> list[str] | None:
        """使用 DFS 检查循环依赖。

        对依赖图执行深度优先搜索以检测循环。

        Returns:
            如果找到循环则返回构成循环的插件名称列表，否则返回 None

        Examples:
            >>> cycle = resolver.check_circular_dependency()
            >>> if cycle:
            ...     print(f"检测到循环: {' -> '.join(cycle)}")
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {name: WHITE for name in self._plugins}
        cycle: list[str] = []

        def dfs(node: str, path: list[str]) -> bool:
            color[node] = GRAY
            path.append(node)

            manifest = self._plugins[node]
            for dep_ref in manifest.dependencies.get("plugins", []):
                dep_name = self._parse_plugin_ref(dep_ref)
                if dep_name not in self._plugins:
                    continue  # 外部依赖，跳过

                if color[dep_name] == GRAY:
                    # 找到循环
                    cycle_start = path.index(dep_name)
                    cycle.extend(path[cycle_start:])
                    cycle.append(dep_name)  # 回到起点
                    return True
                elif color[dep_name] == WHITE:
                    if dfs(dep_name, path):
                        return True

            path.pop()
            color[node] = BLACK
            return False

        for plugin_name in self._plugins:
            if color[plugin_name] == WHITE:
                if dfs(plugin_name, []):
                    return cycle

        return None

    def _parse_plugin_ref(self, ref: str) -> str:
        """解析插件引用字符串。

        从引用字符串中提取插件名称。
        未来版本可能支持版本约束。

        Args:
            ref: 插件引用字符串（例如 'plugin_name:>=1.0.0'）

        Returns:
            插件名称

        Examples:
            >>> resolver._parse_plugin_ref("my_plugin:>=1.0.0")
            'my_plugin'
            >>> resolver._parse_plugin_ref("other_plugin")
            'other_plugin'
        """
        return _split_plugin_dependency_ref(ref)[0]

    def clear(self) -> None:
        """清除解析器中的所有插件。

        Examples:
            >>> resolver.clear()
        """
        self._plugins.clear()
