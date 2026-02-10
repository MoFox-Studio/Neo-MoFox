"""Model 配置模块

定义 LLM 模型相关的配置项，包括 API 提供商、模型信息和任务配置。

使用示例：
    ```python
    from src.core.config.model_config import init_model_config, get_model_config

    # 初始化配置
    init_model_config("config/models.toml")

    # 获取配置
    config = get_model_config()
    
    # 获取特定任务的模型配置
    task_config = config.model_tasks.replyer
    print(task_config.model_list)
    print(task_config.max_tokens)
    
    # 获取 API 提供商信息
    provider = config.get_provider("openai")
    api_key = provider.get_api_key()
    
    # 获取模型信息
    model = config.get_model("gpt-4")
    print(model.price_in)
    ```
"""

from threading import Lock as ThreadLock
from typing import Any, Literal, cast

from src.kernel.config import ConfigBase, SectionBase, config_section, Field
from src.kernel.llm.types import ModelSet

# ==============================================================================
# API Provider Configuration
# ==============================================================================


@config_section("api_providers")
class APIProviderSection(SectionBase):
    """API 提供商配置节
    
    定义单个 API 提供商的配置信息。
    """

    name: str = Field(
        default="SiliconFlow",
        description="API提供商名称（如 openai、azure、gemini 等）",
    )
    base_url: str = Field(
        default="https://api.siliconflow.cn/v1",
        description="API 基础 URL",
    )
    api_key: str | list[str] = Field(
        default="your-siliconflow-api-key-here",
        description="API 密钥，支持单个密钥或密钥列表轮询",
    )
    client_type: Literal["openai", "gemini", "aiohttp_gemini", "bedrock"] = Field(
        default="openai",
        description="客户端类型（openai/gemini/bedrock等）",
    )
    max_retry: int = Field(
        default=3,
        description="最大重试次数",
    )
    timeout: int = Field(
        default=30,
        description="API 调用超时时长（秒）",
    )
    retry_interval: int = Field(
        default=10,
        description="重试间隔时间（秒）",
    )

    # 私有属性，用于密钥轮询
    _api_key_lock: "ThreadLock | None" = None
    _api_key_index: int = 0

    def model_post_init(self, __context: Any) -> None:
        """初始化后处理"""
        super().model_post_init(__context)
        self._api_key_lock = ThreadLock()
        self._api_key_index = 0

    def get_api_key(self) -> str:
        """获取 API 密钥（支持轮询）

        Returns:
            str: API 密钥

        Raises:
            ValueError: 如果密钥列表为空
        """
        if self._api_key_lock is None:
            self._api_key_lock = ThreadLock()
            
        with self._api_key_lock:
            if isinstance(self.api_key, str):
                return self.api_key
            if not self.api_key:
                raise ValueError("API密钥列表为空")
            key = self.api_key[self._api_key_index]
            self._api_key_index = (self._api_key_index + 1) % len(self.api_key)
            return key


# ==============================================================================
# Model Information Configuration
# ==============================================================================


@config_section("models")
class ModelInfoSection(SectionBase):
    """模型信息配置节
    
    定义单个模型的详细信息。
    """

    model_identifier: str = Field(
        ...,
        description="模型标识符（用于 API 调用）",
    )
    name: str = Field(
        ...,
        description="模型名称（用于内部调用）",
    )
    api_provider: str = Field(
        ...,
        description="所属 API 提供商名称",
    )
    price_in: float = Field(
        default=0.0,
        description="每百万 token 输入价格",
    )
    price_out: float = Field(
        default=0.0,
        description="每百万 token 输出价格",
    )
    force_stream_mode: bool = Field(
        default=False,
        description="是否强制使用流式输出模式",
    )
    extra_params: dict[str, Any] = Field(
        default_factory=dict,
        description="额外参数（用于 API 调用时的额外配置）",
    )
    anti_truncation: bool = Field(
        default=False,
        description="是否启用反截断功能",
    )


# ==============================================================================
# Task Configuration
# ==============================================================================


@config_section("tasks")
class TaskConfigSection(SectionBase):
    """任务配置节
    
    定义单个任务的模型配置参数。
    """

    model_list: list[str] = Field(
        default_factory=list,
        description="任务使用的模型列表（模型名称）",
    )
    max_tokens: int = Field(
        default=800,
        description="任务最大输出 token 数",
    )
    temperature: float = Field(
        default=0.7,
        description="模型温度参数",
    )
    concurrency_count: int = Field(
        default=1,
        description="并发请求数量",
    )
    embedding_dimension: int | None = Field(
        default=None,
        description="嵌入模型输出向量维度",
    )


# ==============================================================================
# Model Tasks Configuration
# ==============================================================================


class ModelTasksSection(SectionBase):
    """模型任务配置集合
    
    包含所有预定义任务的配置。
    """

    # ========== 核心对话任务 ==========
    utils: TaskConfigSection = Field(
        default_factory=lambda: TaskConfigSection(model_list=["siliconflow-deepseek-ai/DeepSeek-V3.2"]),
        description="在 MoFox 的一些组件中使用的模型，例如表情包模块，取名模块，关系模块，是 MoFox 必须的模型",
    )
    utils_small: TaskConfigSection = Field(
        default_factory=lambda: TaskConfigSection(model_list=["qwen3-8b"]),
        description="在 MoFox 的一些组件中使用的小模型，消耗量较大，建议使用速度较快的小模型",
    )
    actor: TaskConfigSection = Field(
        default_factory=lambda: TaskConfigSection(model_list=["siliconflow-deepseek-ai/DeepSeek-V3.2"]),
        description="动作器模型配置",
    )
    sub_actor: TaskConfigSection = Field(
        default_factory=lambda: TaskConfigSection(model_list=["siliconflow-deepseek-ai/DeepSeek-V3.2"]),
        description="副动作器模型配置",
    )

    # ========== 多模态任务 ==========
    vlm: TaskConfigSection = Field(
        default_factory=lambda: TaskConfigSection(model_list=["qwen2.5-vl-72b"]),
        description="图像识别模型",
    )
    voice: TaskConfigSection = Field(
        default_factory=lambda: TaskConfigSection(model_list=["sensevoice-small"]),
        description="语音识别模型",
    )
    video: TaskConfigSection = Field(
        default_factory=lambda: TaskConfigSection(model_list=["qwen2.5-vl-72b"]),
        description="视频分析模型配置",
    )
    tool_use: TaskConfigSection = Field(
        default_factory=lambda: TaskConfigSection(model_list=["qwen3-8b"]),
        description="工具调用模型，需要使用支持工具调用的模型",
    )
    embedding: TaskConfigSection = Field(
        default_factory=lambda: TaskConfigSection(model_list=["bge-m3"], embedding_dimension=1024),
        description="嵌入模型配置",
    )

    def get_task(self, task_name: str) -> TaskConfigSection:
        """获取指定任务的配置
        
        Args:
            task_name: 任务名称
            
        Returns:
            TaskConfigSection: 任务配置
            
        Raises:
            ValueError: 如果任务未找到或未配置
        """
        if hasattr(self, task_name):
            config = getattr(self, task_name)
            if config is None:
                raise ValueError(f"任务 '{task_name}' 未配置")
            return config
        raise ValueError(f"任务 '{task_name}' 未找到对应的配置")


# ==============================================================================
# Main Model Configuration
# ==============================================================================


class ModelConfig(ConfigBase):
    """模型配置类
    
    定义 LLM 模型相关的所有配置，包括 API 提供商、模型信息和任务配置。
    """

    # ========== API 提供商配置 ==========
    api_providers: list[APIProviderSection] = Field(
        default_factory=lambda: [
            APIProviderSection(
                name="SiliconFlow",
                base_url="https://api.siliconflow.cn/v1",
                api_key="your-siliconflow-api-key-here",
                client_type="openai",
                max_retry=3,
                timeout=30,
                retry_interval=10,
            ),
        ],
        description="API 提供商列表",
    )

    # ========== 模型信息配置 ==========
    models: list[ModelInfoSection] = Field(
        default_factory=lambda: [
            ModelInfoSection(
                name="siliconflow-deepseek-ai/DeepSeek-V3.2",
                model_identifier="deepseek-ai/DeepSeek-V3.2",
                api_provider="SiliconFlow",
                price_in=2.0,
                price_out=8.0,
            ),
            ModelInfoSection(
                name="qwen3-8b",
                model_identifier="Qwen/Qwen3-8B",
                api_provider="SiliconFlow",
                price_in=0.0,
                price_out=0.0,
                extra_params={"enable_thinking": False},
            ),
            ModelInfoSection(
                name="qwen2.5-vl-72b",
                model_identifier="Qwen/Qwen2.5-VL-72B-Instruct",
                api_provider="SiliconFlow",
                price_in=4.13,
                price_out=4.13,
            ),
            ModelInfoSection(
                name="sensevoice-small",
                model_identifier="FunAudioLLM/SenseVoiceSmall",
                api_provider="SiliconFlow",
                price_in=0.0,
                price_out=0.0,
            ),
            ModelInfoSection(
                name="bge-m3",
                model_identifier="BAAI/bge-m3",
                api_provider="SiliconFlow",
                price_in=0.0,
                price_out=0.0,
            ),
        ],
        description="模型信息列表",
    )

    # ========== 任务配置 ==========
    @config_section("model_tasks")
    class ModelTasksConfig(ModelTasksSection):
        """模型任务配置（内嵌类，用于 TOML 节定义）"""
        pass

    model_tasks: ModelTasksConfig = Field(
        default_factory=ModelTasksConfig,
        description="模型任务配置集合",
    )

    # ========== 私有缓存字典 ==========
    _api_providers_dict: dict[str, APIProviderSection] | None = None
    _models_dict: dict[str, ModelInfoSection] | None = None

    def model_post_init(self, __context: Any) -> None:
        """初始化后处理，构建缓存字典"""
        super().model_post_init(__context)
        self._build_cache_dicts()

    def _build_cache_dicts(self) -> None:
        """构建 API 提供商和模型的缓存字典"""
        self._api_providers_dict = {
            provider.name: provider for provider in self.api_providers
        }
        self._models_dict = {
            model.name: model for model in self.models
        }

    def get_provider(self, provider_name: str) -> APIProviderSection:
        """获取指定的 API 提供商配置
        
        Args:
            provider_name: 提供商名称
            
        Returns:
            APIProviderSection: 提供商配置
            
        Raises:
            KeyError: 如果提供商未找到
        """
        if self._api_providers_dict is None:
            self._build_cache_dicts()
        
        if provider_name not in self._api_providers_dict:  # type: ignore
            raise KeyError(f"API 提供商 '{provider_name}' 未找到")
        return self._api_providers_dict[provider_name]  # type: ignore

    def get_model(self, model_name: str) -> ModelInfoSection:
        """获取指定的模型配置
        
        Args:
            model_name: 模型名称
            
        Returns:
            ModelInfoSection: 模型配置
            
        Raises:
            KeyError: 如果模型未找到
        """
        if self._models_dict is None:
            self._build_cache_dicts()
        
        if model_name not in self._models_dict:  # type: ignore
            raise KeyError(f"模型 '{model_name}' 未找到")
        return self._models_dict[model_name]  # type: ignore

    @property
    def api_providers_dict(self) -> dict[str, APIProviderSection]:
        """获取 API 提供商字典
        
        Returns:
            dict: 提供商名称到配置的映射
        """
        if self._api_providers_dict is None:
            self._build_cache_dicts()
        return self._api_providers_dict  # type: ignore

    @property
    def models_dict(self) -> dict[str, ModelInfoSection]:
        """获取模型字典
        
        Returns:
            dict: 模型名称到配置的映射
        """
        if self._models_dict is None:
            self._build_cache_dicts()
        return self._models_dict  # type: ignore

    def get_task(self, task_name: str) -> ModelSet:
        """获取任务的 ModelSet（符合 kernel.llm 要求的格式）
        
        返回格式符合 kernel.llm.types.ModelEntry 定义，包含完整的模型配置信息。
        
        Args:
            task_name: 任务名称（如 'replyer', 'utils', 'embedding' 等）
            
        Returns:
            ModelSet: ModelSet 列表，每个元素包含：
                - api_provider: str - API 提供商名称
                - base_url: str - API 基础 URL
                - model_identifier: str - 模型标识符
                - api_key: str - API 密钥
                - client_type: str - 客户端类型
                - max_retry: int - 最大重试次数
                - timeout: float - 超时时间（秒）
                - retry_interval: float - 重试间隔（秒）
                - price_in: float - 输入价格
                - price_out: float - 输出价格
                - temperature: float - 温度参数
                - max_tokens: int - 最大 token 数
                - extra_params: dict - 额外参数
                
        Raises:
            ValueError: 如果任务未找到或未配置
            KeyError: 如果模型或提供商未找到
            
        Examples:
            ```python
            from src.core.config import get_model_config
            from src.kernel.llm import LLMRequest
            
            config = get_model_config()
            model_set = config.get_task("replyer")
            
            # 直接用于 LLMRequest
            request = LLMRequest(model_set=model_set, request_name="chat")
            ```
        """
        # 获取任务配置
        task_config = self.model_tasks.get_task(task_name)
        
        # 构建 ModelSet
        model_set: list[dict[str, Any]] = []

        global_extra_params: dict[str, Any] = {}
        try:
            from src.core.config import get_core_config

            core_config = get_core_config()
            global_extra_params = {
                "force_sync_http": core_config.advanced.force_sync_http,
                "trust_env": core_config.advanced.trust_env,
            }
        except Exception:
            global_extra_params = {}
        
        for model_name in task_config.model_list:
            # 获取模型信息
            model_info = self.get_model(model_name)
            
            # 获取提供商信息
            provider = self.get_provider(model_info.api_provider)
            
            # 构建 ModelEntry 字典
            extra_params = dict(global_extra_params)
            extra_params.update(model_info.extra_params)

            model_entry: dict[str, Any] = {
                "api_provider": provider.name,
                "base_url": provider.base_url,
                "model_identifier": model_info.model_identifier,
                "api_key": provider.get_api_key(),  # 支持密钥轮询
                "client_type": provider.client_type,
                "max_retry": provider.max_retry,
                "timeout": float(provider.timeout),
                "retry_interval": float(provider.retry_interval),
                "price_in": model_info.price_in,
                "price_out": model_info.price_out,
                "temperature": task_config.temperature,
                "max_tokens": task_config.max_tokens,
                "extra_params": extra_params,
            }
            
            model_set.append(model_entry)
        
        return cast(ModelSet, model_set)


# ==============================================================================
# Global Configuration Management
# ==============================================================================


# 全局配置实例（延迟初始化）
_global_model_config: ModelConfig | None = None


def get_model_config() -> ModelConfig:
    """获取全局模型配置实例
    
    Returns:
        ModelConfig: 模型配置实例
        
    Raises:
        RuntimeError: 如果配置未初始化
    """
    global _global_model_config
    if _global_model_config is None:
        raise RuntimeError(
            "Model config not initialized. "
            "Call init_model_config() first."
        )
    return _global_model_config


def init_model_config(config_path: str) -> ModelConfig:
    """初始化模型配置
    
    Args:
        config_path: 配置文件路径
        
    Returns:
        ModelConfig: 模型配置实例
        
    Examples:
        使用默认配置：
        ```python
        config = init_model_config()
        ```
        
        从文件加载：
        ```python
        config = init_model_config("config/models.toml")
        ```
    """
    global _global_model_config

    from pathlib import Path

    path = Path(config_path)

    # 确保配置文件存在
    if not path.exists():
        # 确保父目录存在
        path.parent.mkdir(parents=True, exist_ok=True)

        # 创建默认配置文件
        default_config = ModelConfig.default()
        _global_model_config = ModelConfig.model_validate(default_config)

        # 保存默认配置到文件
        from src.kernel.config.core import _render_toml_with_signature
        toml_content = _render_toml_with_signature(ModelConfig, default_config)
        path.write_text(toml_content, encoding="utf-8")

    # 从文件加载配置
    _global_model_config = ModelConfig.load(config_path, auto_update=True)

    return _global_model_config


# ==============================================================================
# Exports
# ==============================================================================


__all__ = [
    # 配置类
    "ModelConfig",
    "APIProviderSection",
    "ModelInfoSection",
    "TaskConfigSection",
    "ModelTasksSection",
    # 全局管理函数
    "get_model_config",
    "init_model_config",
]
