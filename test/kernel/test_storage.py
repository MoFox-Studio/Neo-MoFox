"""
Storage 模块单元测试

测试 JSONStore 类的持久化存储功能。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import shutil

import pytest

from src.kernel.storage import JSONStore


class TestJSONStoreInit:
    """测试 JSONStore 初始化"""

    def test_init_default_storage_dir(self) -> None:
        """测试使用默认存储目录初始化"""
        store = JSONStore()
        assert store.get_storage_dir() == Path("data/json_storage").resolve()

    def test_init_custom_storage_dir(self) -> None:
        """测试使用自定义存储目录初始化"""
        custom_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=custom_dir)
            assert store.get_storage_dir() == Path(custom_dir).resolve()
        finally:
            shutil.rmtree(custom_dir)


class TestJSONStoreSave:
    """测试 save 方法"""

    @pytest.mark.asyncio
    async def test_save_simple_data(self) -> None:
        """测试保存简单数据"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)
            data = {"key": "value", "count": 42}

            await store.save("test_data", data)

            # 验证文件已创建
            file_path = Path(temp_dir) / "test_data.json"
            assert file_path.exists()

            # 验证文件内容
            content = file_path.read_text(encoding="utf-8")
            assert '"key": "value"' in content
            assert '"count": 42' in content
        finally:
            shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_save_complex_data(self) -> None:
        """测试保存复杂数据结构"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)
            data = {
                "string": "test",
                "number": 123,
                "float": 3.14,
                "boolean": True,
                "null": None,
                "list": [1, 2, 3],
                "nested": {"a": 1, "b": 2},
            }

            await store.save("complex", data)

            # 验证可以正确读取
            loaded = await store.load("complex")
            assert loaded == data
        finally:
            shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_save_overwrite_existing(self) -> None:
        """测试覆盖已存在的数据"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)

            # 保存初始数据
            await store.save("test", {"version": 1})

            # 覆盖新数据
            await store.save("test", {"version": 2})

            # 验证数据已被覆盖
            loaded = await store.load("test")
            assert loaded == {"version": 2}
        finally:
            shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_save_creates_directory_if_not_exists(self) -> None:
        """测试目录不存在时自动创建"""
        temp_dir = tempfile.mkdtemp()
        storage_path = Path(temp_dir) / "nested" / "storage" / "path"
        try:
            store = JSONStore(storage_dir=storage_path)

            # 目录不存在，应该自动创建
            await store.save("test", {"key": "value"})

            assert storage_path.exists()
            assert (storage_path / "test.json").exists()
        finally:
            shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_save_with_invalid_name_slash(self) -> None:
        """测试保存时使用包含斜杠的名称抛出错误"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)

            with pytest.raises(ValueError, match="Invalid storage name"):
                await store.save("test/name", {"key": "value"})
        finally:
            shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_save_with_invalid_name_backslash(self) -> None:
        """测试保存时使用包含反斜杠的名称抛出错误"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)

            with pytest.raises(ValueError, match="Invalid storage name"):
                await store.save("test\\name", {"key": "value"})
        finally:
            shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_save_with_invalid_name_dot_dot(self) -> None:
        """测试保存时使用包含 .. 的名称抛出错误（路径遍历攻击）"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)

            with pytest.raises(ValueError, match="Invalid storage name"):
                await store.save("../etc/passwd", {"key": "value"})
        finally:
            shutil.rmtree(temp_dir)


class TestJSONStoreLoad:
    """测试 load 方法"""

    @pytest.mark.asyncio
    async def test_load_existing_data(self) -> None:
        """测试加载已存在的数据"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)
            data = {"key": "value", "count": 42}

            # 先保存数据
            await store.save("test", data)

            # 加载数据
            loaded = await store.load("test")
            assert loaded == data
        finally:
            shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_load_nonexistent_data(self) -> None:
        """测试加载不存在的数据返回 None"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)

            loaded = await store.load("nonexistent")
            assert loaded is None
        finally:
            shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_load_with_invalid_name(self) -> None:
        """测试加载时使用非法名称抛出错误"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)

            with pytest.raises(ValueError, match="Invalid storage name"):
                await store.load("../etc/passwd")
        finally:
            shutil.rmtree(temp_dir)


class TestJSONStoreDelete:
    """测试 delete 方法"""

    @pytest.mark.asyncio
    async def test_delete_existing_data(self) -> None:
        """测试删除已存在的数据"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)

            # 先保存数据
            await store.save("test", {"key": "value"})

            # 删除数据
            result = await store.delete("test")
            assert result is True

            # 验证文件已删除
            assert not await store.exists("test")
        finally:
            shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_delete_nonexistent_data(self) -> None:
        """测试删除不存在的数据返回 False"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)

            result = await store.delete("nonexistent")
            assert result is False
        finally:
            shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_delete_with_invalid_name(self) -> None:
        """测试删除时使用非法名称抛出错误"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)

            with pytest.raises(ValueError, match="Invalid storage name"):
                await store.delete("../etc/passwd")
        finally:
            shutil.rmtree(temp_dir)


class TestJSONStoreExists:
    """测试 exists 方法"""

    @pytest.mark.asyncio
    async def test_exists_existing_data(self) -> None:
        """测试检查已存在的数据"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)

            # 先保存数据
            await store.save("test", {"key": "value"})

            # 检查存在
            assert await store.exists("test") is True
        finally:
            shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_exists_nonexistent_data(self) -> None:
        """测试检查不存在的数据"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)

            assert await store.exists("nonexistent") is False
        finally:
            shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_exists_with_invalid_name(self) -> None:
        """测试检查时使用非法名称抛出错误"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)

            with pytest.raises(ValueError, match="Invalid storage name"):
                await store.exists("../etc/passwd")
        finally:
            shutil.rmtree(temp_dir)


class TestJSONStoreListAll:
    """测试 list_all 方法"""

    @pytest.mark.asyncio
    async def test_list_all_empty_storage(self) -> None:
        """测试列出空存储"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)

            all_data = await store.list_all()
            assert all_data == []
        finally:
            shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_list_all_with_data(self) -> None:
        """测试列出存储中的所有数据"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)

            # 保存多个数据
            await store.save("data1", {"value": 1})
            await store.save("data2", {"value": 2})
            await store.save("data3", {"value": 3})

            all_data = await store.list_all()

            # 验证返回的名称列表（顺序可能不同）
            assert len(all_data) == 3
            assert set(all_data) == {"data1", "data2", "data3"}

            # 验证名称不包含 .json 后缀
            for name in all_data:
                assert not name.endswith(".json")
        finally:
            shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_list_all_ignores_non_json_files(self) -> None:
        """测试列出时忽略非 JSON 文件"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)

            # 保存数据
            await store.save("data1", {"value": 1})

            # 创建非 JSON 文件
            (Path(temp_dir) / "readme.txt").write_text("text", encoding="utf-8")
            (Path(temp_dir) / "config.yaml").write_text("key: value", encoding="utf-8")

            all_data = await store.list_all()

            # 应该只返回 JSON 文件
            assert all_data == ["data1"]
        finally:
            shutil.rmtree(temp_dir)


class TestJSONStoreConcurrency:
    """测试并发访问"""

    @pytest.mark.asyncio
    async def test_concurrent_save_different_keys(self) -> None:
        """测试并发保存不同的键"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)

            # 并发保存
            tasks = [
                store.save(f"data_{i}", {"value": i})
                for i in range(10)
            ]
            await asyncio.gather(*tasks)

            # 验证所有数据都已保存
            all_data = await store.list_all()
            assert len(all_data) == 10

            # 验证数据内容
            for i in range(10):
                loaded = await store.load(f"data_{i}")
                assert loaded == {"value": i}
        finally:
            shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_concurrent_save_same_key(self) -> None:
        """测试并发保存相同的键"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)

            # 并发保存同一个键
            tasks = [
                store.save("test", {"counter": i})
                for i in range(10)
            ]
            await asyncio.gather(*tasks)

            # 验证数据已保存（最终值可能是任意一个）
            loaded = await store.load("test")
            assert loaded is not None
            assert "counter" in loaded
            assert 0 <= loaded["counter"] < 10
        finally:
            shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_concurrent_read_write(self) -> None:
        """测试并发读写"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)

            # 先保存数据
            await store.save("test", {"value": 0})

            async def increment_read() -> int:
                """多次读取并返回最新值"""
                data = await store.load("test")
                return data["value"] if data else -1

            # 并发读取
            read_tasks = [increment_read() for _ in range(5)]
            read_results = await asyncio.gather(*read_tasks)

            # 所有读取应该返回相同值
            assert all(r == 0 for r in read_results)
        finally:
            shutil.rmtree(temp_dir)


class TestGlobalJSONStore:
    """测试全局 json_store 单例"""

    @pytest.mark.asyncio
    async def test_global_singleton(self) -> None:
        """测试全局 json_store 是单例"""
        from src.kernel.storage.core import _get_json_store

        store1 = _get_json_store()
        store2 = _get_json_store()

        assert store1 is store2

    @pytest.mark.asyncio
    async def test_global_json_store_save_and_load(self) -> None:
        """测试全局 json_store 的基本功能"""
        # 使用临时目录避免污染默认目录
        temp_dir = tempfile.mkdtemp()
        try:
            # 注意：这里使用新的实例以避免污染全局单例
            from src.kernel.storage import JSONStore

            custom_store = JSONStore(storage_dir=temp_dir)

            # 测试保存和加载
            await custom_store.save("test", {"key": "value"})
            loaded = await custom_store.load("test")
            assert loaded == {"key": "value"}
        finally:
            shutil.rmtree(temp_dir)


class TestJSONStoreEdgeCases:
    """测试边界情况"""

    @pytest.mark.asyncio
    async def test_save_empty_dict(self) -> None:
        """测试保存空字典"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)

            await store.save("empty", {})

            loaded = await store.load("empty")
            assert loaded == {}
        finally:
            shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_save_dict_with_unicode(self) -> None:
        """测试保存包含 Unicode 字符的数据"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)
            data = {
                "chinese": "中文测试",
                "emoji": "😀🎉",
                "arabic": "مرحبا",
                "russian": "Привет",
            }

            await store.save("unicode", data)

            loaded = await store.load("unicode")
            assert loaded == data
        finally:
            shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_save_dict_with_special_characters(self) -> None:
        """测试保存包含特殊字符的数据"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)
            data = {
                "newlines": "line1\nline2\nline3",
                "tabs": "col1\tcol2\tcol3",
                "quotes": 'He said "Hello"',
            }

            await store.save("special", data)

            loaded = await store.load("special")
            assert loaded == data
        finally:
            shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_name_with_underscores_and_numbers(self) -> None:
        """测试使用带下划线和数字的名称"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)

            await store.save("test_name_123", {"value": 1})
            await store.save("my_plugin_data_v2", {"value": 2})

            assert await store.exists("test_name_123")
            assert await store.exists("my_plugin_data_v2")

            loaded = await store.load("test_name_123")
            assert loaded == {"value": 1}
        finally:
            shutil.rmtree(temp_dir)


class TestIntegration:
    """集成测试"""

    @pytest.mark.asyncio
    async def test_full_crud_workflow(self) -> None:
        """测试完整的增删改查工作流程"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)

            # 1. 创建（保存）
            await store.save("user_12345", {"name": "Alice", "age": 30})
            assert await store.exists("user_12345")

            # 2. 读取
            user = await store.load("user_12345")
            assert user == {"name": "Alice", "age": 30}

            # 3. 更新
            await store.save("user_12345", {"name": "Alice", "age": 31})
            user = await store.load("user_12345")
            assert user["age"] == 31

            # 4. 删除
            result = await store.delete("user_12345")
            assert result is True
            assert not await store.exists("user_12345")

            # 再次删除应该返回 False
            result = await store.delete("user_12345")
            assert result is False
        finally:
            shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_multiple_data_sets(self) -> None:
        """测试管理多个数据集"""
        temp_dir = tempfile.mkdtemp()
        try:
            store = JSONStore(storage_dir=temp_dir)

            # 模拟插件数据存储（使用下划线替代冒号，避免 Windows 文件名限制）
            await store.save("plugin_a_config", {"enabled": True})
            await store.save("plugin_a_data", {"items": [1, 2, 3]})
            await store.save("plugin_b_config", {"enabled": False})
            await store.save("plugin_b_cache", {"timestamp": 1234567890})

            # 列出所有数据
            all_data = await store.list_all()
            assert len(all_data) == 4
            assert set(all_data) == {
                "plugin_a_config",
                "plugin_a_data",
                "plugin_b_config",
                "plugin_b_cache",
            }

            # 验证各数据独立性
            assert await store.exists("plugin_a_config")
            assert await store.exists("plugin_b_cache")

            # 删除一个插件的数据不影响另一个
            await store.delete("plugin_a_config")
            assert not await store.exists("plugin_a_config")
            assert await store.exists("plugin_b_config")
        finally:
            shutil.rmtree(temp_dir)
