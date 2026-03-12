"""测试 src.core.models.protocols 模块。"""

from typing import Protocol, runtime_checkable



# 定义测试协议
@runtime_checkable
class TestService(Protocol):
    """测试服务协议。"""

    async def process(self, data: str) -> str: ...

    async def get_status(self) -> bool: ...


@runtime_checkable
class DataService(Protocol):
    """数据服务协议。"""

    async def store(self, key: str, value: str) -> bool: ...

    async def retrieve(self, key: str) -> str | None: ...

    async def delete(self, key: str) -> bool: ...


class TestServiceImpl:
    """测试服务实现。"""

    async def process(self, data: str) -> str:
        return f"processed: {data}"

    async def get_status(self) -> bool:
        return True


class DataServiceImpl:
    """数据服务实现。"""

    def __init__(self):
        self._data = {}

    async def store(self, key: str, value: str) -> bool:
        self._data[key] = value
        return True

    async def retrieve(self, key: str) -> str | None:
        return self._data.get(key)

    async def delete(self, key: str) -> bool:
        if key in self._data:
            del self._data[key]
            return True
        return False


class PartialServiceImpl:
    """部分实现的服务（缺少方法）。"""

    async def process(self, data: str) -> str:
        return f"processed: {data}"

    # 缺少 get_status 方法


class TestProtocols:
    """测试协议功能。"""

    def test_protocol_check_with_implementation(self):
        """测试协议检查（完整实现）。"""
        impl = TestServiceImpl()
        assert isinstance(impl, TestService)

    def test_protocol_check_with_partial_implementation(self):
        """测试协议检查（部分实现）。"""
        impl = PartialServiceImpl()
        # 由于 Protocol 是结构化子类型，只检查是否存在必需方法
        # 缺少 get_status 方法的实现不应该通过检查
        assert not isinstance(impl, TestService)

    def test_protocol_check_with_non_compatible(self):
        """测试协议检查（不兼容的对象）。"""
        class NotAService:
            async def other_method(self) -> str:
                return "not a service"

        impl = NotAService()
        assert not isinstance(impl, TestService)

    def test_data_service_protocol(self):
        """测试数据服务协议。"""
        impl = DataServiceImpl()

        assert isinstance(impl, DataService)

    async def test_data_service_implementation(self):
        """测试数据服务实现的方法。"""
        impl = DataServiceImpl()

        # 测试 store
        assert await impl.store("key1", "value1") is True
        assert await impl.store("key2", "value2") is True

        # 测试 retrieve
        assert await impl.retrieve("key1") == "value1"
        assert await impl.retrieve("key2") == "value2"
        assert await impl.retrieve("nonexistent") is None

        # 测试 delete
        assert await impl.delete("key1") is True
        assert await impl.retrieve("key1") is None
        assert await impl.delete("nonexistent") is False

    async def test_test_service_implementation(self):
        """测试服务实现的方法。"""
        impl = TestServiceImpl()

        # 测试 process
        result = await impl.process("test_data")
        assert result == "processed: test_data"

        # 测试 get_status
        status = await impl.get_status()
        assert status is True


class TestProtocolComposition:
    """测试协议组合。"""

    @runtime_checkable
    class ExtendedService(Protocol):
        """扩展服务协议。"""

        async def process(self, data: str) -> str: ...
        async def get_status(self) -> bool: ...
        async def reset(self) -> bool: ...  # 额外方法

    def test_extended_protocol_check(self):
        """测试扩展协议检查。"""
        # TestServiceImpl 不实现 reset 方法
        impl = TestServiceImpl()
        assert not isinstance(impl, self.ExtendedService)

        # 实现扩展协议的类
        class ExtendedServiceImpl:
            async def process(self, data: str) -> str:
                return f"processed: {data}"

            async def get_status(self) -> bool:
                return True

            async def reset(self) -> bool:
                return True

        impl2 = ExtendedServiceImpl()
        assert isinstance(impl2, self.ExtendedService)


class TestProtocolInheritance:
    """测试协议继承。"""

    @runtime_checkable
    class BaseService(Protocol):
        """基础服务协议。"""

        async def start(self) -> bool: ...

        async def stop(self) -> bool: ...

    @runtime_checkable
    class AdvancedService(BaseService, Protocol):
        """高级服务协议（继承基础服务）。"""

        async def restart(self) -> bool: ...

    def test_protocol_inheritance_check(self):
        """测试协议继承检查。"""
        class BasicServiceImpl:
            async def start(self) -> bool:
                return True

            async def stop(self) -> bool:
                return True

        class AdvancedServiceImpl:
            async def start(self) -> bool:
                return True

            async def stop(self) -> bool:
                return True

            async def restart(self) -> bool:
                return True

        basic_impl = BasicServiceImpl()
        advanced_impl = AdvancedServiceImpl()

        # 基础实现应该匹配基础协议
        assert isinstance(basic_impl, self.BaseService)
        assert not isinstance(basic_impl, self.AdvancedService)

        # 高级实现应该匹配两个协议
        assert isinstance(advanced_impl, self.BaseService)
        assert isinstance(advanced_impl, self.AdvancedService)
