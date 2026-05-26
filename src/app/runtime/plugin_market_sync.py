"""插件市场同步。

在 Bot 最终进行插件发现与加载前，将插件市场订阅列表同步到本地插件目录。
仅自动安装 / 更新 .mfp 插件，不会改动文件夹插件或普通 .zip 插件。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import httpx

from src.core.components.loader import PluginLoader, PluginManifest, load_manifest
from src.kernel.logger import get_logger


logger = get_logger("plugin_market_sync")


@dataclass
class LocalPluginRecord:
    """本地插件记录。"""

    manifest: PluginManifest
    path: Path


@dataclass
class PluginMarketSyncReport:
    """一次插件市场同步结果。"""

    skipped: bool = False
    reason: str = ""
    installed: int = 0
    updated: int = 0
    removed: int = 0
    resolved_dependencies: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """返回适合展示在 UI 中的简短摘要。"""

        if self.skipped:
            return self.reason or "已跳过"

        parts: list[str] = []
        if self.installed:
            parts.append(f"安装 {self.installed}")
        if self.updated:
            parts.append(f"更新 {self.updated}")
        if self.removed:
            parts.append(f"清理 {self.removed}")
        if self.resolved_dependencies:
            parts.append(f"依赖补齐 {self.resolved_dependencies}")
        if self.errors:
            parts.append(f"{len(self.errors)} 个错误")
        return "、".join(parts) or "无需同步"


class PluginMarketSyncService:
    """将插件市场订阅同步到本地插件目录。"""

    def __init__(
        self,
        *,
        config: Any,
        plugins_dir: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._market = config.plugin_market
        self._plugins_dir = Path(plugins_dir)
        self._transport = transport

    async def sync(self) -> PluginMarketSyncReport:
        """执行一次同步。"""

        report = PluginMarketSyncReport()
        reason = self._skip_reason()
        if reason is not None:
            report.skipped = True
            report.reason = reason
            return report

        self._plugins_dir.mkdir(parents=True, exist_ok=True)
        local_plugins = await self._load_local_plugins()
        logger.info(
            f"已加载 {len(local_plugins)} 个本地插件: {list(local_plugins.keys())}"
        )

        async with httpx.AsyncClient(
            base_url=self._market.base_url.rstrip("/"),
            timeout=self._market.timeout,
            trust_env=self._trust_env(),
            follow_redirects=True,
            headers={"Authorization": f"Bearer {self._market.access_token}"},
            transport=self._transport,
        ) as client:
            subscriptions = await self._fetch_subscriptions(client)
            subscribed_ids = {
                item["plugin_id"]
                for item in subscriptions
                if item.get("plugin_id")
            }
            logger.info(
                f"获取到 {len(subscribed_ids)} 个已订阅插件: {sorted(subscribed_ids)}"
            )

            if self._market.strict_subscribed_list_mode:
                logger.info("严格订阅模式已启用，将清理未订阅的 mfp 插件")
                await self._cleanup_unsubscribed_plugins(
                    client,
                    local_plugins,
                    subscribed_ids,
                    report,
                )

            visited: set[str] = set()
            for item in subscriptions:
                plugin_id = str(item.get("plugin_id") or "").strip()
                if not plugin_id:
                    continue
                await self._ensure_plugin(
                    client,
                    plugin_id,
                    local_plugins,
                    report,
                    visited,
                    is_dependency=False,
                )

        logger.info(f"插件市场同步完成: {report.summary()}")
        return report

    def _skip_reason(self) -> str | None:
        if not self._market.enabled:
            return "已跳过（已禁用）"
        if not str(self._market.base_url or "").strip():
            return "已跳过（未配置市场地址）"
        if not str(self._market.user_id or "").strip():
            return "已跳过（未配置市场用户 ID）"
        if not str(self._market.access_token or "").strip():
            return "已跳过（未配置访问令牌）"
        return None

    def _trust_env(self) -> bool:
        if self._market.use_advanced_trust_env:
            return bool(self._config.advanced.trust_env)
        return False

    async def _load_local_plugins(self) -> dict[str, LocalPluginRecord]:
        loader = PluginLoader()
        records: dict[str, LocalPluginRecord] = {}
        for plugin_path in await loader.discover_plugins(str(self._plugins_dir)):
            manifest = await load_manifest(plugin_path)
            if manifest is None:
                continue
            records[manifest.name] = LocalPluginRecord(
                manifest=manifest,
                path=Path(plugin_path),
            )
        return records

    async def _fetch_subscriptions(
        self,
        client: httpx.AsyncClient,
    ) -> list[dict[str, Any]]:
        response = await client.get(
            f"/api/v1/machine/authors/{self._market.user_id}/subscriptions"
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items", [])
        return items if isinstance(items, list) else []

    async def _fetch_plugin_detail(
        self,
        client: httpx.AsyncClient,
        plugin_id: str,
    ) -> dict[str, Any] | None:
        response = await client.get(f"/api/v1/plugins/{plugin_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else None

    async def _fetch_dependencies(
        self,
        client: httpx.AsyncClient,
        plugin_id: str,
    ) -> list[dict[str, Any]]:
        response = await client.get(f"/api/v1/plugins/{plugin_id}/dependencies")
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items", [])
        return items if isinstance(items, list) else []

    async def _fetch_install_info(
        self,
        client: httpx.AsyncClient,
        plugin_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        response = await client.get(f"/api/v1/plugins/{plugin_id}/install")
        response.raise_for_status()
        payload = response.json()
        plugin = payload.get("plugin", {})
        version = payload.get("version", {})
        if not isinstance(plugin, dict) or not isinstance(version, dict):
            raise ValueError(f"插件市场返回的安装信息格式无效: {plugin_id}")
        return plugin, version

    async def _cleanup_unsubscribed_plugins(
        self,
        client: httpx.AsyncClient,
        local_plugins: dict[str, LocalPluginRecord],
        subscribed_ids: set[str],
        report: PluginMarketSyncReport,
    ) -> None:
        for plugin_id, record in list(local_plugins.items()):
            if plugin_id in subscribed_ids:
                continue
            if record.path.suffix.lower() != ".mfp":
                continue

            try:
                detail = await self._fetch_plugin_detail(client, plugin_id)
            except Exception as exc:  # pragma: no cover - defensive logging path
                report.errors.append(f"查询市场插件失败 {plugin_id}: {exc}")
                continue

            if detail is None:
                continue
            if str(detail.get("status") or "").lower() != "published":
                continue

            if record.path.exists():
                record.path.unlink()
            local_plugins.pop(plugin_id, None)
            report.removed += 1
            logger.info(f"严格模式：清理未订阅 mfp 插件 {plugin_id}")

    async def _ensure_plugin(
        self,
        client: httpx.AsyncClient,
        plugin_id: str,
        local_plugins: dict[str, LocalPluginRecord],
        report: PluginMarketSyncReport,
        visited: set[str],
        *,
        is_dependency: bool,
    ) -> None:
        if plugin_id in visited:
            return
        visited.add(plugin_id)

        if self._market.auto_resolve_plugin_dependencies:
            try:
                dependencies = await self._fetch_dependencies(client, plugin_id)
            except Exception as exc:
                report.errors.append(f"读取依赖失败 {plugin_id}: {exc}")
                dependencies = []
            dep_ids = [
                str(item.get("plugin_id") or "").strip()
                for item in dependencies
                if item.get("plugin_id") and item.get("exists_in_market")
            ]
            if dep_ids:
                logger.info(
                    f"插件 {plugin_id} 解析到 {len(dep_ids)} 个缺失前置依赖: {dep_ids}"
                )
            for item in dependencies:
                dependency_id = str(item.get("plugin_id") or "").strip()
                if not dependency_id or not item.get("exists_in_market"):
                    continue
                await self._ensure_plugin(
                    client,
                    dependency_id,
                    local_plugins,
                    report,
                    visited,
                    is_dependency=True,
                )

        local_record = local_plugins.get(plugin_id)
        if local_record is None and not is_dependency and not self._market.auto_install_subscribed_missing:
            logger.info(f"订阅插件 {plugin_id} 未安装，且 auto_install_subscribed_missing 已关闭，跳过")
            return

        try:
            _, version = await self._fetch_install_info(client, plugin_id)
        except Exception as exc:
            report.errors.append(f"读取安装信息失败 {plugin_id}: {exc}")
            return

        target_version = str(version.get("version") or "").strip()
        asset_url = str(version.get("asset_download_url") or "").strip()
        asset_name = str(version.get("asset_name") or "").strip() or f"{plugin_id}-{target_version or 'latest'}.mfp"
        target_path = self._plugins_dir / asset_name

        if not asset_url:
            report.errors.append(f"插件 {plugin_id} 缺少可下载的 mfp 资产")
            return

        if local_record is None:
            tag = "依赖" if is_dependency else "订阅"
            logger.info(f"安装{tag}插件 {plugin_id} v{target_version} -> {asset_name}")
            await self._download_asset(client, asset_url, target_path)
            manifest = await load_manifest(str(target_path))
            if manifest is None:
                report.errors.append(f"下载后的插件 manifest 无法解析: {plugin_id}")
                return
            local_plugins[plugin_id] = LocalPluginRecord(manifest=manifest, path=target_path)
            report.installed += 1
            if is_dependency:
                report.resolved_dependencies += 1
            return

        if local_record.path.suffix.lower() != ".mfp":
            return
        if not self._market.auto_update_mfp:
            return
        if target_version and local_record.manifest.version == target_version:
            if not is_dependency:
                logger.info(f"订阅插件 {plugin_id} v{target_version} 已是最新，跳过")
            return

        old_version = local_record.manifest.version
        tag = "依赖" if is_dependency else "订阅"
        logger.info(
            f"检测到{tag}插件 {plugin_id} 有更新: v{old_version} -> v{target_version}"
        )

        await self._download_asset(client, asset_url, target_path)
        manifest = await load_manifest(str(target_path))
        if manifest is None:
            report.errors.append(f"更新后的插件 manifest 无法解析: {plugin_id}")
            return
        if local_record.path != target_path and local_record.path.exists():
            local_record.path.unlink()
        local_plugins[plugin_id] = LocalPluginRecord(manifest=manifest, path=target_path)
        report.updated += 1
        if is_dependency:
            report.resolved_dependencies += 1

    async def _download_asset(
        self,
        client: httpx.AsyncClient,
        asset_url: str,
        target_path: Path,
    ) -> None:
        response = await client.get(asset_url)
        response.raise_for_status()

        target_path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(delete=False, dir=target_path.parent, suffix=".download") as handle:
            handle.write(response.content)
            temp_path = Path(handle.name)
        temp_path.replace(target_path)