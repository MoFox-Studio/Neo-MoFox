"""在临时目录内演示同一进程的配置修改、组件启停与插件增删。

从仓库根目录运行：
    uv run python -m examples.src.core.managers.plugin_hot_reload_example

示例使用真实文件监听和公开插件注册机制，不连接外部平台。
"""

import asyncio
import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

from src.app.plugin_system.api import plugin_api
from src.core.managers.plugin_hot_reload import PluginHotReloadService
from src.core.managers.plugin_manager import get_plugin_manager
from src.kernel.concurrency import get_task_manager

PLUGIN_SOURCE = '''"""使用公开基类和注册入口的示例插件。"""
from src.app.plugin_system.base import (
    BaseAction, BaseConfig, BasePlugin, Field, SectionBase,
    config_section, register_plugin,
)


class Config(BaseConfig):
    """运行时可修改的插件配置。"""

    name = "config"

    @config_section("settings")
    class Settings(SectionBase):
        """组件启用状态及业务参数。"""

        enabled: bool = Field(default=True)
        greeting: str = Field(default="初始配置")

    settings: Settings = Field(default_factory=Settings)


class Greeting(BaseAction):
    """根据最新配置返回问候语。"""

    name = "greeting"
    associated_types = ["text"]

    async def execute(self) -> tuple[bool, str]:
        """读取本次插件实例的配置。"""
        return True, self.plugin.config.settings.greeting


@register_plugin
class Plugin(BasePlugin):
    """通过配置选择注册组件的现有插件写法。"""

    plugin_name = "PLUGIN_NAME"
    configs = [Config]

    def get_components(self) -> list[type]:
        """关闭时不注册动作，开启时注册动作。"""
        return [Greeting] if self.config.settings.enabled else []
'''


def create_plugin(name: str) -> Path:
    """在临时插件根目录写入插件清单和入口。"""
    root = Path("plugins") / name
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "author": "MoFox",
                "description": "运行时配置示例",
                "entry_point": "plugin.py",
                "dependencies": {"plugins": [], "components": []},
                "api_version": {"plugin_api": "1.0.0"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "plugin.py").write_text(
        PLUGIN_SOURCE.replace("PLUGIN_NAME", name), encoding="utf-8"
    )
    return root


async def wait_until(check: Callable[[], bool]) -> None:
    """等待真实文件事件完成处理，超时则明确报告示例失败。"""
    async with asyncio.timeout(10):
        while not check():
            await asyncio.sleep(0.05)


async def main() -> None:
    """隔离文件系统，演示配置生效、插件加入和移除后释放全部资源。"""
    original_cwd = Path.cwd()
    pid = os.getpid()
    manager = get_plugin_manager()
    with TemporaryDirectory(prefix="mofox_hot_reload_example_") as directory:
        os.chdir(directory)
        service = PluginHotReloadService(manager)
        try:
            root = create_plugin("hot_reload_demo")
            assert await plugin_api.load_plugin(str(root))
            await service.start()
            config_path = Path("config/plugins/hot_reload_demo/config.toml")
            config_path.write_text(
                '[settings]\nenabled = false\ngreeting = "修改已生效"\n',
                encoding="utf-8",
            )
            await wait_until(
                lambda: (
                    not manager.get_plugin("hot_reload_demo").config.settings.enabled
                )
            )
            assert manager.get_plugin("hot_reload_demo").get_components() == []
            print("配置已生效，动作组件已关闭")

            config_path.write_text(
                '[settings]\nenabled = true\ngreeting = "重新开启"\n',
                encoding="utf-8",
            )
            await wait_until(
                lambda: manager.get_plugin("hot_reload_demo").config.settings.enabled
            )
            assert len(manager.get_plugin("hot_reload_demo").get_components()) == 1
            print("配置已生效，动作组件已开启")

            create_plugin("added_demo")
            await wait_until(lambda: manager.is_plugin_loaded("added_demo"))
            shutil.rmtree(Path("plugins/added_demo"))
            await wait_until(lambda: not manager.is_plugin_loaded("added_demo"))
            assert os.getpid() == pid
            print(f"新增与移除完成，进程始终为 PID {pid}")
        finally:
            await service.stop()
            for name in manager.list_loaded_plugins():
                await plugin_api.unload_plugin(name)
            get_task_manager().shutdown_process_pool(wait=True)
            os.chdir(original_cwd)


if __name__ == "__main__":
    asyncio.run(main())
