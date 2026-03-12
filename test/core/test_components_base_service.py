"""测试 src.core.components.base.service 模块。"""



from src.core.components.base.service import BaseService


class ConcreteService(BaseService):
    """具体的 Service 实现用于测试。"""

    service_name = "test_service"
    service_description = "Test service"
    version = "1.0.0"


class TestBaseService:
    """测试 BaseService 类。"""

    def test_service_initialization(self, mock_plugin):
        """测试 Service 初始化。"""
        service = ConcreteService(mock_plugin)
        assert service.plugin == mock_plugin
        assert service.service_name == "test_service"
        assert service.service_description == "Test service"
        assert service.version == "1.0.0"

    def test_get_signature(self, mock_plugin):
        """测试获取签名。"""
        service = ConcreteService(mock_plugin)
        assert service.get_signature() is None

        ConcreteService._plugin_ = "my_plugin"
        service2 = ConcreteService(mock_plugin)
        assert service2.get_signature() == "my_plugin:service:test_service"


class TestServiceWithMethods:
    """测试带方法的 Service。"""

    def test_service_with_methods(self, mock_plugin):
        """测试带方法的 Service。"""
        class MethodService(BaseService):
            service_name = "method_service"

            async def get_data(self) -> dict:
                return {"key": "value"}

            async def set_data(self, key: str, value: str) -> bool:
                return True

        service = MethodService(mock_plugin)

        import asyncio

        # 测试方法可以调用
        data = asyncio.run(service.get_data())
        assert data == {"key": "value"}

        result = asyncio.run(service.set_data("test", "value"))
        assert result is True


class TestServiceAttributes:
    """测试 Service 类属性。"""

    def test_service_with_all_attributes(self, mock_plugin):
        """测试带有所有属性的服务。"""
        class FullService(BaseService):
            service_name = "full_service"
            service_description = "Full service description"
            version = "2.5.0"
            dependencies = ["other_plugin:service:database", "another_plugin:service:cache"]

        service = FullService(mock_plugin)
        assert service.service_name == "full_service"
        assert service.service_description == "Full service description"
        assert service.version == "2.5.0"
        assert service.dependencies == ["other_plugin:service:database", "another_plugin:service:cache"]

    def test_service_different_versions(self, mock_plugin):
        """测试不同版本。"""
        # 使用工厂函数创建不同版本的 Service
        def create_service(version: str):
            class VersionService(BaseService):
                service_name = f"service_{version.replace('.', '_')}"
                version_attr = version

                async def dummy_method(self):
                    return self.version_attr

            service = VersionService(mock_plugin)
            # 检查 version_attr 属性（因为 version 被类属性覆盖了）
            return service, version

        for version in ["0.1.0", "1.0.0", "2.0.0-beta", "3.5.2"]:
            service, expected_version = create_service(version)
            assert service.version_attr == expected_version


class TestServiceWithProtocol:
    """测试实现协议的 Service。"""

    def test_service_implementing_protocol(self, mock_plugin):
        """测试实现 Protocol 的 Service。"""
        from typing import Protocol

        class DataProtocol(Protocol):
            """数据服务协议。"""

            async def store(self, key: str, value: str) -> bool: ...

            async def retrieve(self, key: str) -> str | None: ...

        class DataServiceImpl(BaseService):
            service_name = "data_service"

            async def store(self, key: str, value: str) -> bool:
                return True

            async def retrieve(self, key: str) -> str | None:
                return f"value_for_{key}"

        service = DataServiceImpl(mock_plugin)

        import asyncio

        # 测试协议方法
        result = asyncio.run(service.store("key1", "value1"))
        assert result is True

        value = asyncio.run(service.retrieve("key1"))
        assert value == "value_for_key1"
