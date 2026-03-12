"""测试 ModelConfig 配置模块。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.config.model_config import (
    APIProviderSection,
    ModelConfig,
    ModelInfoSection,
    ModelTasksSection,
    TaskConfigSection,
    get_model_config,
    init_model_config,
)


class TestAPIProviderSection:
    """测试 APIProviderSection 配置节。"""

    def test_create_provider_section(self):
        """测试创建提供商配置节。"""
        provider = APIProviderSection(
            name="openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-test123",
        )

        assert provider.name == "openai"
        assert provider.base_url == "https://api.openai.com/v1"
        assert provider.api_key == "sk-test123"
        assert provider.client_type == "openai"
        assert provider.max_retry == 2
        assert provider.timeout == 10
        assert provider.retry_interval == 10

    def test_provider_with_multiple_api_keys(self):
        """测试多个 API 密钥的提供商。"""
        provider = APIProviderSection(
            name="test",
            base_url="https://test.com",
            api_key=["key1", "key2", "key3"],
        )

        assert provider.api_key == ["key1", "key2", "key3"]

    def test_get_api_key_single(self):
        """测试获取单个 API 密钥。"""
        provider = APIProviderSection(
            name="openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-test123",
        )

        key = provider.get_api_key()
        assert key == "sk-test123"

    def test_get_api_key_multiple_rotation(self):
        """测试多个 API 密钥的轮询。"""
        provider = APIProviderSection(
            name="test",
            base_url="https://test.com",
            api_key=["key1", "key2", "key3"],
        )

        # 测试轮询
        assert provider.get_api_key() == "key1"
        assert provider.get_api_key() == "key2"
        assert provider.get_api_key() == "key3"
        assert provider.get_api_key() == "key1"  # 循环回第一个

    def test_get_api_key_empty_list_raises(self):
        """测试空密钥列表抛出异常。"""
        provider = APIProviderSection(
            name="test",
            base_url="https://test.com",
            api_key=[],
        )

        with pytest.raises(ValueError, match="API密钥列表为空"):
            provider.get_api_key()


class TestModelInfoSection:
    """测试 ModelInfoSection 配置节。"""

    def test_create_model_info(self):
        """测试创建模型信息配置。"""
        model = ModelInfoSection(
            model_identifier="gpt-4",
            name="gpt4",
            api_provider="openai",
            price_in=0.03,
            price_out=0.06,
        )

        assert model.model_identifier == "gpt-4"
        assert model.name == "gpt4"
        assert model.api_provider == "openai"
        assert model.price_in == 0.03
        assert model.price_out == 0.06
        assert model.force_stream_mode is False
        assert model.anti_truncation is False

    def test_model_with_extra_params(self):
        """测试带额外参数的模型配置。"""
        model = ModelInfoSection(
            model_identifier="claude-3-opus",
            name="claude_opus",
            api_provider="anthropic",
            extra_params={"temperature": 1.0, "top_p": 0.9},
        )

        assert model.extra_params == {"temperature": 1.0, "top_p": 0.9}

    def test_model_force_stream_mode(self):
        """测试强制流式输出模式。"""
        model = ModelInfoSection(
            model_identifier="test",
            name="test_model",
            api_provider="test",
            force_stream_mode=True,
        )

        assert model.force_stream_mode is True

    def test_model_anti_truncation(self):
        """测试反截断功能。"""
        model = ModelInfoSection(
            model_identifier="test",
            name="test_model",
            api_provider="test",
            anti_truncation=True,
        )

        assert model.anti_truncation is True


class TestTaskConfigSection:
    """测试 TaskConfigSection 配置节。"""

    def test_create_task_config(self):
        """测试创建任务配置。"""
        task = TaskConfigSection(
            model_list=["gpt-4", "claude-3"],
            max_tokens=2000,
            temperature=0.8,
        )

        assert task.model_list == ["gpt-4", "claude-3"]
        assert task.max_tokens == 2000
        assert task.temperature == 0.8
        assert task.concurrency_count == 1
        assert task.embedding_dimension is None

    def test_task_with_defaults(self):
        """测试使用默认值的任务配置。"""
        task = TaskConfigSection(model_list=["gpt-4"])

        assert task.max_tokens == 800
        assert task.temperature == 0.7
        assert task.concurrency_count == 1

    def test_task_for_embedding(self):
        """测试嵌入任务配置。"""
        task = TaskConfigSection(
            model_list=["text-embedding-3"],
            embedding_dimension=1536,
        )

        assert task.embedding_dimension == 1536


class TestModelTasksSection:
    """测试 ModelTasksSection 配置集合。"""

    def test_default_tasks(self):
        """测试默认任务配置。"""
        tasks = ModelTasksSection()

        assert hasattr(tasks, "utils")
        assert hasattr(tasks, "utils_small")
        assert hasattr(tasks, "actor")
        assert hasattr(tasks, "sub_actor")
        assert hasattr(tasks, "vlm")
        assert hasattr(tasks, "voice")
        assert hasattr(tasks, "tool_use")

    def test_get_existing_task(self):
        """测试获取存在的任务。"""
        tasks = ModelTasksSection()
        tasks.utils = TaskConfigSection(model_list=["gpt-4"])

        task = tasks.get_task("utils")
        assert task.model_list == ["gpt-4"]

    def test_get_nonexistent_task_raises(self):
        """测试获取不存在的任务抛出异常。"""
        tasks = ModelTasksSection()

        with pytest.raises(ValueError, match="任务 'nonexistent' 未找到对应的配置"):
            tasks.get_task("nonexistent")

    def test_get_none_task_raises(self):
        """测试获取 None 任务抛出异常。"""
        tasks = ModelTasksSection()
        tasks.utils = None

        with pytest.raises(ValueError, match="任务 'utils' 未配置"):
            tasks.get_task("utils")


class TestModelConfig:
    """测试 ModelConfig 主配置类。"""

    def test_create_empty_config(self):
        """测试创建空配置。"""
        config = ModelConfig()

        assert config.api_providers == []
        assert config.models == []
        assert isinstance(config.model_tasks, ModelTasksSection)

    def test_create_config_with_providers(self):
        """测试创建带提供商的配置。"""
        config = ModelConfig(
            api_providers=[
                APIProviderSection(
                    name="openai",
                    base_url="https://api.openai.com/v1",
                    api_key="sk-test",
                ),
                APIProviderSection(
                    name="anthropic",
                    base_url="https://api.anthropic.com/v1",
                    api_key="sk-ant-test",
                ),
            ]
        )

        assert len(config.api_providers) == 2

    def test_create_config_with_models(self):
        """测试创建带模型的配置。"""
        config = ModelConfig(
            models=[
                ModelInfoSection(
                    model_identifier="gpt-4",
                    name="gpt4",
                    api_provider="openai",
                ),
                ModelInfoSection(
                    model_identifier="claude-3-opus",
                    name="claude_opus",
                    api_provider="anthropic",
                ),
            ]
        )

        assert len(config.models) == 2

    def test_build_cache_dicts(self):
        """测试构建缓存字典。"""
        config = ModelConfig(
            api_providers=[
                APIProviderSection(
                    name="openai",
                    base_url="https://api.openai.com/v1",
                    api_key="sk-test",
                ),
            ],
            models=[
                ModelInfoSection(
                    model_identifier="gpt-4",
                    name="gpt4",
                    api_provider="openai",
                ),
            ],
        )

        config._build_cache_dicts()

        assert "openai" in config._api_providers_dict # type: ignore
        assert "gpt4" in config._models_dict # type: ignore

    def test_get_provider(self):
        """测试获取提供商。"""
        config = ModelConfig(
            api_providers=[
                APIProviderSection(
                    name="openai",
                    base_url="https://api.openai.com/v1",
                    api_key="sk-test",
                ),
            ],
        )

        provider = config.get_provider("openai")
        assert provider.name == "openai"
        assert provider.base_url == "https://api.openai.com/v1"

    def test_get_nonexistent_provider_raises(self):
        """测试获取不存在的提供商抛出异常。"""
        config = ModelConfig()

        with pytest.raises(KeyError, match="API 提供商 'nonexistent' 未找到"):
            config.get_provider("nonexistent")

    def test_get_model(self):
        """测试获取模型。"""
        config = ModelConfig(
            models=[
                ModelInfoSection(
                    model_identifier="gpt-4",
                    name="gpt4",
                    api_provider="openai",
                ),
            ],
        )

        model = config.get_model("gpt4")
        assert model.name == "gpt4"
        assert model.model_identifier == "gpt-4"

    def test_get_nonexistent_model_raises(self):
        """测试获取不存在的模型抛出异常。"""
        config = ModelConfig()

        with pytest.raises(KeyError, match="模型 'nonexistent' 未找到"):
            config.get_model("nonexistent")

    def test_api_providers_dict_property(self):
        """测试 api_providers_dict 属性。"""
        config = ModelConfig(
            api_providers=[
                APIProviderSection(
                    name="openai",
                    base_url="https://api.openai.com/v1",
                    api_key="sk-test",
                ),
            ],
        )

        providers = config.api_providers_dict
        assert "openai" in providers
        assert providers["openai"].name == "openai"

    def test_models_dict_property(self):
        """测试 models_dict 属性。"""
        config = ModelConfig(
            models=[
                ModelInfoSection(
                    model_identifier="gpt-4",
                    name="gpt4",
                    api_provider="openai",
                ),
            ],
        )

        models = config.models_dict
        assert "gpt4" in models
        assert models["gpt4"].name == "gpt4"

    def test_get_task_model_set(self):
        """测试获取任务的 ModelSet。"""

        config = ModelConfig(
            api_providers=[
                APIProviderSection(
                    name="openai",
                    base_url="https://api.openai.com/v1",
                    api_key="sk-test",
                    client_type="openai",
                    max_retry=3,
                    timeout=15,
                    retry_interval=5,
                ),
            ],
            models=[
                ModelInfoSection(
                    model_identifier="gpt-4",
                    name="gpt4",
                    api_provider="openai",
                    price_in=0.03,
                    price_out=0.06,
                    max_context=20000,
                ),
            ],
        )
        # 设置 model_tasks 的 utils 配置
        config.model_tasks.utils = TaskConfigSection(
            model_list=["gpt4"],
            max_tokens=1000,
            temperature=0.5,
        )

        model_set = config.get_task("utils")

        assert isinstance(model_set, list)
        assert len(model_set) == 1

        entry = model_set[0]
        assert entry["api_provider"] == "openai"
        assert entry["base_url"] == "https://api.openai.com/v1"
        assert entry["model_identifier"] == "gpt-4"
        assert entry["api_key"] == "sk-test"
        assert entry["client_type"] == "openai"
        assert entry["max_retry"] == 3
        assert entry["timeout"] == 15.0
        assert entry["retry_interval"] == 5.0
        assert entry["price_in"] == 0.03
        assert entry["price_out"] == 0.06
        assert entry["temperature"] == 0.5
        assert entry["max_tokens"] == 1000
        assert entry["max_context"] == 20000
        assert entry["tool_call_compat"] is False

    def test_get_task_model_set_with_custom_context_and_compat_fields(self):
        """测试模型集支持模型级 max_context/tool_call_compat，预留参数存于 extra_params。"""
        config = ModelConfig(
            api_providers=[
                APIProviderSection(
                    name="openai",
                    base_url="https://api.openai.com/v1",
                    api_key="sk-test",
                ),
            ],
            models=[
                ModelInfoSection(
                    model_identifier="gpt-4",
                    name="gpt4",
                    api_provider="openai",
                    max_context=16384,
                    tool_call_compat=True,
                    extra_params={"context_reserve_ratio": 0.2, "context_reserve_tokens": 512},
                ),
            ],
        )
        config.model_tasks.utils = TaskConfigSection(
            model_list=["gpt4"],
            max_tokens=1000,
            temperature=0.5,
        )

        model_set = config.get_task("utils")
        entry = model_set[0]
        assert entry["max_context"] == 16384
        assert entry["tool_call_compat"] is True
        assert entry["extra_params"]["context_reserve_ratio"] == 0.2
        assert entry["extra_params"]["context_reserve_tokens"] == 512

    def test_get_task_with_multiple_models(self):
        """测试获取包含多个模型的任务。"""
        config = ModelConfig(
            api_providers=[
                APIProviderSection(
                    name="openai",
                    base_url="https://api.openai.com/v1",
                    api_key=["key1", "key2"],
                ),
                APIProviderSection(
                    name="anthropic",
                    base_url="https://api.anthropic.com/v1",
                    api_key="sk-ant-test",
                ),
            ],
            models=[
                ModelInfoSection(
                    model_identifier="gpt-4",
                    name="gpt4",
                    api_provider="openai",
                ),
                ModelInfoSection(
                    model_identifier="claude-3-opus",
                    name="claude_opus",
                    api_provider="anthropic",
                ),
            ],
        )
        config.model_tasks.actor = TaskConfigSection(
            model_list=["gpt4", "claude_opus"],
        )

        model_set = config.get_task("actor")

        assert len(model_set) == 2
        assert model_set[0]["model_identifier"] == "gpt-4"
        assert model_set[1]["model_identifier"] == "claude-3-opus"

    def test_get_task_nonexistent_raises(self):
        """测试获取不存在的任务抛出异常。"""
        config = ModelConfig()

        with pytest.raises(ValueError, match="任务 'nonexistent' 未找到对应的配置"):
            config.get_task("nonexistent")

    def test_model_post_init_builds_cache(self):
        """测试模型后初始化构建缓存。"""
        config = ModelConfig(
            api_providers=[
                APIProviderSection(
                    name="openai",
                    base_url="https://api.openai.com/v1",
                    api_key="sk-test",
                ),
            ],
        )

        # model_post_init 应该被自动调用
        assert config._api_providers_dict is not None
        assert "openai" in config._api_providers_dict


class TestGlobalModelConfig:
    """测试全局模型配置管理。"""

    def test_init_model_config_default(self, temp_dir: Path):
        """测试使用默认配置初始化。"""
        # 清除全局配置
        import src.core.config.model_config as model_config_module

        model_config_module._global_model_config = None

        config_path = temp_dir / "models.toml"
        config = init_model_config(str(config_path))
        assert config is not None
        assert isinstance(config, ModelConfig)

    def test_init_model_config_from_file(self, temp_dir: Path):
        """测试从文件加载配置。"""
        # 保存原状态
        import src.core.config.model_config as model_config_module
        original_config = model_config_module._global_model_config
        model_config_module._global_model_config = None

        try:
            config_file = temp_dir / "models.toml"
            config_file.write_text(
                """
[[api_providers]]
name = "openai"
base_url = "https://api.openai.com/v1"
api_key = "sk-test"

[[models]]
model_identifier = "gpt-4"
name = "gpt4"
api_provider = "openai"

[model_tasks.utils]
model_list = ["gpt4"]
max_tokens = 1000
"""
            )

            config = init_model_config(str(config_file))
            assert len(config.api_providers) == 1
            assert config.api_providers[0].name == "openai"
        finally:
            # 恢复原状态
            model_config_module._global_model_config = original_config

    def test_get_model_config_before_init_raises(self):
        """测试未初始化时获取配置抛出异常。"""
        import src.core.config.model_config as model_config_module
        original_config = model_config_module._global_model_config
        model_config_module._global_model_config = None

        try:
            with pytest.raises(RuntimeError, match="Model config not initialized"):
                get_model_config()
        finally:
            model_config_module._global_model_config = original_config

    def test_get_model_config_after_init(self, temp_dir: Path):
        """测试初始化后获取配置。"""
        import src.core.config.model_config as model_config_module
        original_config = model_config_module._global_model_config
        model_config_module._global_model_config = None

        try:
            config_path = temp_dir / "models.toml"
            init_model_config(str(config_path))
            config = get_model_config()

            assert isinstance(config, ModelConfig)
        finally:
            model_config_module._global_model_config = original_config

    def test_init_model_config_multiple_times(self, temp_dir: Path):
        """测试多次初始化更新配置。"""
        import src.core.config.model_config as model_config_module
        original_config = model_config_module._global_model_config
        model_config_module._global_model_config = None

        try:
            # 第一次初始化
            config_path = temp_dir / "models.toml"
            config1 = init_model_config(str(config_path))
            # 第二次初始化会更新全局配置
            config2 = init_model_config(str(config_path))

            # 第二次应该返回新创建的实例（因为重新初始化了）
            assert config2 is not None
            assert isinstance(config2, ModelConfig)
            # get_model_config 应该返回第二次初始化的实例
            config3 = get_model_config()
            assert config3 is config2
        finally:
            model_config_module._global_model_config = original_config
