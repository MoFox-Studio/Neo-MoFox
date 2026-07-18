"""语义兴趣度计算模块。

基于 TF-IDF + Logistic Regression 的语义兴趣度计算系统，
支持人设感知的自动训练和模型切换。

核心组件：
- FastScorer：绕过 sklearn 的纯 Python 评分器，使用 token→weight 字典直接计算
- 全局线程池：避免重复创建 ThreadPoolExecutor
- 批处理队列：攒消息一起算，提高 CPU 利用率
"""

from .auto_trainer import AutoTrainer, get_auto_trainer
from .dataset import DatasetGenerator, generate_training_dataset
from .features_tfidf import TfidfFeatureExtractor
from .model_lr import SemanticInterestModel, train_semantic_model
from .optimized_scorer import (
    BatchScoringQueue,
    FastScorer,
    FastScorerConfig,
    clear_fast_scorer_instances,
    convert_sklearn_to_fast,
    get_fast_scorer,
    get_global_executor,
    shutdown_global_executor,
)
from .runtime_scorer import (
    ModelManager,
    SemanticInterestScorer,
    clear_scorer_instances,
    get_all_scorer_instances,
    get_semantic_scorer,
    get_semantic_scorer_sync,
)
from .trainer import SemanticInterestTrainer

__all__ = [
    # 运行时评分
    "SemanticInterestScorer",
    "ModelManager",
    "get_semantic_scorer",
    "get_semantic_scorer_sync",
    "clear_scorer_instances",
    "get_all_scorer_instances",
    # 优化评分器
    "FastScorer",
    "FastScorerConfig",
    "BatchScoringQueue",
    "get_fast_scorer",
    "convert_sklearn_to_fast",
    "clear_fast_scorer_instances",
    "get_global_executor",
    "shutdown_global_executor",
    # 训练组件
    "TfidfFeatureExtractor",
    "SemanticInterestModel",
    "train_semantic_model",
    # 数据集生成
    "DatasetGenerator",
    "generate_training_dataset",
    # 训练器
    "SemanticInterestTrainer",
    # 自动训练
    "AutoTrainer",
    "get_auto_trainer",
]
