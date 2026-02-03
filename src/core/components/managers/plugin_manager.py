"""插件管理器。

本模块提供插件管理器，负责“单个插件”的导入执行、组件注册与生命周期钩子调用。

宏观层面的插件发现、manifest 读取、依赖/版本检查与加载顺序计算由
src.core.components.loader.PluginLoader 负责。
"""

import importlib.util
import inspect
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.kernel.logger import get_logger

from src.core.components.loader import (
    PluginManifest,
    get_plugin_class,
)
from src.core.components.registry import get_global_registry
from src.core.components.state_manager import get_global_state_manager
from src.core.components.types import ComponentState, ComponentType, build_signature

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin


logger = get_logger("plugin_manager")


class PluginManager:
    """插件管理器。

    负责单个插件的导入、组件注册、卸载和生命周期管理。

    Attributes:
        _loaded_plugins: 已加载的插件实例字典
        _manifests: 插件清单字典
        _plugin_paths: 插件路径字典

    Examples:
        >>> manager = PluginManager()
        >>> await manager.load_all_plugins("plugins")
        >>> plugin = manager.get_plugin("my_plugin")
        >>> await manager.unload_plugin("my_plugin")
    """

    def __init__(self) -> None:
        """初始化插件管理器。"""
        self._loaded_plugins: dict[str, "BasePlugin"] = {}
        self._manifests: dict[str, PluginManifest] = {}
        self._plugin_paths: dict[str, str] = {}
        self._failed_plugins: dict[str, str] = {}

        logger.info("插件管理器初始化完成")

    async def load_plugin_from_manifest(self, plugin_path: str, manifest: PluginManifest) -> bool:
        """加载单个插件（manifest 已由 loader 宏观层校验并提供）。"""
        plugin_name = manifest.name

        # 1. 检查是否已加载
        if plugin_name in self._loaded_plugins:
            logger.warning(f"插件 '{plugin_name}' 已经加载")
            return True

        # 2. 加载插件模块（导入会触发 @register_plugin 执行）
        if plugin_path.endswith((".zip", ".mfp")):
            plugin_module = await self._load_from_archive(plugin_path, manifest)
        else:
            plugin_module = await self._load_from_folder(plugin_path, manifest)

        if not plugin_module:
            error_msg = "插件模块加载失败"
            self._failed_plugins[plugin_name] = error_msg
            return False

        # 3. 查找 @register_plugin 注册的插件类
        plugin_class = get_plugin_class(plugin_name)
        if not plugin_class:
            error_msg = "插件类未注册（未使用 @register_plugin 装饰器）"
            self._failed_plugins[plugin_name] = error_msg
            logger.error(f"插件 '{plugin_name}' 加载失败: {error_msg}")
            return False

        # 4. 实例化插件
        try:
            plugin_instance = plugin_class(config=None)  # type: ignore
        except Exception as e:
            error_msg = f"插件实例化失败: {e}"
            self._failed_plugins[plugin_name] = error_msg
            logger.error(f"插件 '{plugin_name}' 加载失败: {error_msg}")
            return False

        # 5. 注册组件到全局注册表
        await self._register_components(plugin_instance)

        # 6. 调用生命周期钩子
        try:
            await plugin_instance.on_plugin_loaded()
        except Exception as e:
            logger.error(f"调用插件 '{plugin_name}' 的 on_plugin_loaded 钩子时出错: {e}")

        # 7. 记录并更新状态
        self._loaded_plugins[plugin_name] = plugin_instance
        self._manifests[plugin_name] = manifest
        self._plugin_paths[plugin_name] = plugin_path

        state_manager = get_global_state_manager()
        await state_manager.set_state_async(
            build_signature(plugin_name, ComponentType.PLUGIN, plugin_name),
            ComponentState.ACTIVE,
        )

        logger.info(f"✅ 插件加载成功: {plugin_name} v{manifest.version}")
        return True

    async def load_plugin(self, plugin_path: str) -> bool:
        """兼容入口：仅用于直接按路径加载单插件。

        宏观校验/依赖检查请使用 loader.PluginLoader。
        """
        from src.core.components.loader import load_manifest

        manifest = await load_manifest(plugin_path)
        if not manifest:
            self._failed_plugins[plugin_path] = "无法加载 manifest.json"
            return False
        return await self.load_plugin_from_manifest(plugin_path, manifest)

    async def unload_plugin(self, plugin_name: str) -> bool:
        """卸载插件。

        卸载指定插件，调用生命周期钩子并清理资源。

        Args:
            plugin_name: 插件名称

        Returns:
            bool: 是否卸载成功

        Examples:
            >>> success = await manager.unload_plugin("my_plugin")
            >>> True
        """
        if plugin_name not in self._loaded_plugins:
            logger.warning(f"插件 '{plugin_name}' 未加载")
            return False

        try:
            plugin = self._loaded_plugins[plugin_name]

            # 调用卸载钩子
            try:
                await plugin.on_plugin_unloaded()
            except Exception as e:
                logger.error(f"调用插件 '{plugin_name}' 的 on_plugin_unloaded 钩子时出错: {e}")

            # 更新状态
            state_manager = get_global_state_manager()
            await state_manager.set_state_async(
                build_signature(plugin_name, ComponentType.PLUGIN, plugin_name),
                ComponentState.UNLOADED
            )

            # 从全局注册表中移除该插件的组件
            await self._unregister_plugin_components(plugin_name)

            # 移除引用
            del self._loaded_plugins[plugin_name]
            if plugin_name in self._manifests:
                del self._manifests[plugin_name]
            if plugin_name in self._plugin_paths:
                del self._plugin_paths[plugin_name]

            logger.info(f"✅ 插件卸载成功: {plugin_name}")
            return True

        except Exception as e:
            logger.error(f"❌ 插件卸载失败: {plugin_name} - {e}")
            return False

    async def _unregister_plugin_components(self, plugin_name: str) -> None:
        """从全局注册表中注销某插件的所有组件，并更新状态。"""
        registry = get_global_registry()
        state_manager = get_global_state_manager()

        components = registry.get_by_plugin(plugin_name)
        if not components:
            return

        for signature in list(components.keys()):
            try:
                registry.unregister(signature)
            except Exception as e:
                logger.warning(f"注销组件失败 '{signature}': {e}")
                continue

            try:
                await state_manager.set_state_async(signature, ComponentState.UNLOADED)
                state_manager.remove_runtime_data(signature)
            except Exception as e:
                logger.warning(f"更新组件状态失败 '{signature}': {e}")

    async def reload_plugin(self, plugin_name: str) -> bool:
        """重载插件。

        先卸载插件，然后重新加载。

        Args:
            plugin_name: 插件名称

        Returns:
            bool: 是否重载成功

        Examples:
            >>> success = await manager.reload_plugin("my_plugin")
            >>> True
        """
        if plugin_name not in self._loaded_plugins:
            logger.warning(f"插件 '{plugin_name}' 未加载，无法重载")
            return False

        plugin_path = self._plugin_paths.get(plugin_name)
        if not plugin_path:
            logger.error(f"未找到插件 '{plugin_name}' 的路径")
            return False

        # 卸载
        if not await self.unload_plugin(plugin_name):
            return False

        # 重新加载
        return await self.load_plugin(plugin_path)

    def get_plugin(self, plugin_name: str) -> "BasePlugin | None":
        """获取插件实例。

        Args:
            plugin_name: 插件名称

        Returns:
            BasePlugin | None: 插件实例，如果未找到则返回 None

        Examples:
            >>> plugin = manager.get_plugin("my_plugin")
        """
        return self._loaded_plugins.get(plugin_name)

    def get_all_plugins(self) -> dict[str, "BasePlugin"]:
        """获取所有已加载插件。

        Returns:
            dict[str, BasePlugin]: 插件名到插件实例的字典

        Examples:
            >>> plugins = manager.get_all_plugins()
        """
        return self._loaded_plugins.copy()

    def list_loaded_plugins(self) -> list[str]:
        """列出所有已加载的插件名称。

        Returns:
            list[str]: 已加载插件名称列表

        Examples:
            >>> names = manager.list_loaded_plugins()
            >>> ['my_plugin', 'other_plugin']
        """
        return list(self._loaded_plugins.keys())

    def get_manifest(self, plugin_name: str) -> PluginManifest | None:
        """获取插件清单。

        Args:
            plugin_name: 插件名称

        Returns:
            PluginManifest | None: 插件清单，如果未找到则返回 None

        Examples:
            >>> manifest = manager.get_manifest("my_plugin")
        """
        return self._manifests.get(plugin_name)

    def is_plugin_loaded(self, plugin_name: str) -> bool:
        """检查插件是否已加载。

        Args:
            plugin_name: 插件名称

        Returns:
            bool: 插件是否已加载

        Examples:
            >>> if manager.is_plugin_loaded("my_plugin"):
            ...     print("插件已加载")
        """
        return plugin_name in self._loaded_plugins

    # === 私有方法 ===

    # manifest 读取 / 版本校验 / 依赖解析：已迁移至 loader.PluginLoader

    async def _load_from_archive(self, archive_path: str, manifest: PluginManifest) -> Any | None:
        """从 ZIP/MFP 加载插件模块。

        Args:
            archive_path: 压缩包路径
            manifest: 插件清单

        Returns:
            加载的模块对象，失败返回 None
        """
        try:
            with zipfile.ZipFile(archive_path, 'r') as zf:
                # 提取到临时目录
                with tempfile.TemporaryDirectory() as tmpdir:
                    zf.extractall(tmpdir)

                    # 添加到 sys.path
                    sys.path.insert(0, tmpdir)

                    try:
                        # 动态导入
                        entry_point = Path(tmpdir) / manifest.entry_point
                        if not entry_point.exists():
                            logger.error(f"入口点不存在: {manifest.entry_point}")
                            return None

                        spec = importlib.util.spec_from_file_location(
                            manifest.name,
                            str(entry_point)
                        )
                        if spec is None or spec.loader is None:
                            logger.error(f"无法创建模块规范: {entry_point}")
                            return None

                        module = importlib.util.module_from_spec(spec)
                        sys.modules[manifest.name] = module
                        spec.loader.exec_module(module)

                        return module
                    finally:
                        # 从 sys.path 移除
                        if tmpdir in sys.path:
                            sys.path.remove(tmpdir)

        except Exception as e:
            logger.error(f"从压缩包加载插件模块失败 ({archive_path}): {e}")
            return None

    async def _load_from_folder(self, folder_path: str, manifest: PluginManifest) -> Any | None:
        """从文件夹加载插件模块。

        Args:
            folder_path: 文件夹路径
            manifest: 插件清单

        Returns:
            加载的模块对象，失败返回 None
        """
        try:
            folder = Path(folder_path)

            # 添加到 sys.path
            sys.path.insert(0, str(folder))

            try:
                entry_point = folder / manifest.entry_point
                if not entry_point.exists():
                    logger.error(f"入口点不存在: {manifest.entry_point}")
                    return None

                spec = importlib.util.spec_from_file_location(
                    manifest.name,
                    str(entry_point)
                )
                if spec is None or spec.loader is None:
                    logger.error(f"无法创建模块规范: {entry_point}")
                    return None

                module = importlib.util.module_from_spec(spec)
                sys.modules[manifest.name] = module
                spec.loader.exec_module(module)

                return module
            finally:
                # 从 sys.path 移除
                if str(folder) in sys.path:
                    sys.path.remove(str(folder))

        except Exception as e:
            logger.error(f"从文件夹加载插件模块失败 ({folder_path}): {e}")
            return None

    async def _register_components(self, plugin_instance: "BasePlugin") -> None:
        """注册插件的所有组件到全局注册表。

        通过 get_components() 获取插件的所有组件类，推断组件类型，
        构建签名，注册到全局注册表，并通知对应的管理器。

        Args:
            plugin_instance: 插件实例
        """
        registry = get_global_registry()
        state_manager = get_global_state_manager()

        # 获取插件的所有组件
        components = plugin_instance.get_components()
        plugin_name = plugin_instance.plugin_name

        logger.debug(f"开始注册插件 '{plugin_name}' 的 {len(components)} 个组件")

        for component_cls in components:
            # 推断组件类型和名称
            component_type, component_name, dependencies = self._identify_component(component_cls)

            if not component_type or not component_name:
                logger.warning(
                    f"跳过无法识别的组件: {component_cls.__name__} "
                    f"(缺少类型标识或名称属性)"
                )
                continue

            # 构建组件签名
            signature = build_signature(plugin_name, component_type, component_name)

            # 检查是否已注册
            if signature in registry:
                logger.warning(f"组件 '{signature}' 已经注册，跳过")
                continue

            try:
                # 注册到全局注册表
                registry.register(component_cls, signature, dependencies)
                logger.debug(f"注册组件: {signature}")

                # 设置组件状态
                await state_manager.set_state_async(signature, ComponentState.ACTIVE)

            except Exception as e:
                logger.error(f"注册组件 '{signature}' 失败: {e}")
                continue

        logger.info(f"✅ 插件 '{plugin_name}' 的组件注册完成")

    def _identify_component(
        self, component_cls: type
    ) -> tuple[ComponentType | None, str | None, list[str]]:
        """识别组件的类型、名称和依赖。

        通过检查组件类的基类推断组件类型，并获取对应的名称属性。
        动态导入基类以避免循环导入问题。

        Args:
            component_cls: 组件类

        Returns:
            tuple[ComponentType | None, str | None, list[str]]:
                (组件类型, 组件名称, 依赖列表)
        """
        # 动态导入基类以避免循环导入
        from src.core.components.base.action import BaseAction
        from src.core.components.base.adapter import BaseAdapter
        from src.core.components.base.chatter import BaseChatter
        from src.core.components.base.collection import BaseCollection
        from src.core.components.base.command import BaseCommand
        from src.core.components.base.event_handler import BaseEventHandler
        from src.core.components.base.router import BaseRouter
        from src.core.components.base.service import BaseService
        from src.core.components.base.tool import BaseTool

        # 组件类型到名称属性和基类的映射
        type_mapping: dict[
            ComponentType,
            tuple[type, str],
        ] = {
            ComponentType.ACTION: (BaseAction, "action_name"),
            ComponentType.TOOL: (BaseTool, "tool_name"),
            ComponentType.ADAPTER: (BaseAdapter, "adapter_name"),
            ComponentType.CHATTER: (BaseChatter, "chatter_name"),
            ComponentType.COMMAND: (BaseCommand, "command_name"),
            ComponentType.COLLECTION: (BaseCollection, "collection_name"),
            ComponentType.EVENT_HANDLER: (BaseEventHandler, "handler_name"),
            ComponentType.SERVICE: (BaseService, "service_name"),
            ComponentType.ROUTER: (BaseRouter, "router_name"),
        }

        # 检查组件类型
        for comp_type, (base_cls, name_attr) in type_mapping.items():
            try:
                if inspect.isclass(component_cls) and issubclass(component_cls, base_cls):
                    component_name = getattr(component_cls, name_attr, None)
                    dependencies = getattr(component_cls, "dependencies", [])
                    return comp_type, component_name, dependencies
            except TypeError:
                # component_cls 不是类
                continue

        return None, None, []


# 全局插件管理器实例
_global_plugin_manager: PluginManager | None = None


def get_plugin_manager() -> PluginManager:
    """获取全局插件管理器实例。

    Returns:
        PluginManager: 全局插件管理器单例

    Examples:
        >>> manager = get_plugin_manager()
        >>> await manager.load_all_plugins("plugins")
    """
    global _global_plugin_manager
    if _global_plugin_manager is None:
        _global_plugin_manager = PluginManager()
    return _global_plugin_manager
