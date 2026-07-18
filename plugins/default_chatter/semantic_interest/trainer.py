"""训练器入口脚本。

统一管理语义兴趣度模型的训练流程：数据采样、标注、训练、评估。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from src.app.plugin_system.api.log_api import get_logger

from .dataset import DatasetGenerator, generate_training_dataset
from .model_lr import train_semantic_model

logger = get_logger("default_chatter.semantic_interest.trainer")


class SemanticInterestTrainer:
    """语义兴趣度训练器。

    统一管理训练流程。
    """

    def __init__(
        self,
        data_dir: Path | None = None,
        model_dir: Path | None = None,
    ) -> None:
        """初始化训练器。

        Args:
            data_dir: 数据集目录
            model_dir: 模型保存目录
        """
        self.data_dir = Path(data_dir or "data/semantic_interest/datasets")
        self.model_dir = Path(model_dir or "data/semantic_interest/models")

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.model_dir.mkdir(parents=True, exist_ok=True)

    async def prepare_dataset(
        self,
        persona_info: dict[str, Any],
        days: int = 7,
        max_samples: int = 1000,
        model_name: str | None = None,
        dataset_name: str | None = None,
        generate_initial_keywords: bool = True,
        keyword_temperature: float = 0.7,
        keyword_iterations: int = 3,
        max_samples_per_batch: int = 50,
    ) -> Path:
        """准备训练数据集。

        Args:
            persona_info: 人格信息
            days: 采样最近 N 天的消息
            max_samples: 最大采样数
            model_name: LLM 模型任务名
            dataset_name: 数据集名称（默认使用时间戳）
            generate_initial_keywords: 是否生成初始关键词数据集
            keyword_temperature: 关键词生成温度
            keyword_iterations: 关键词生成迭代次数
            max_samples_per_batch: 每批 LLM 标注的消息条数

        Returns:
            数据集文件路径
        """
        if dataset_name is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dataset_name = f"dataset_{timestamp}"

        output_path = self.data_dir / f"{dataset_name}.json"

        logger.info(f"开始准备数据集: {dataset_name}")

        await generate_training_dataset(
            output_path=output_path,
            persona_info=persona_info,
            days=days,
            max_samples=max_samples,
            model_name=model_name,
            generate_initial_keywords=generate_initial_keywords,
            keyword_temperature=keyword_temperature,
            keyword_iterations=keyword_iterations,
            max_samples_per_batch=max_samples_per_batch,
        )

        return output_path

    def train_model(
        self,
        dataset_path: Path,
        model_version: str | None = None,
        tfidf_config: dict | None = None,
        model_config: dict | None = None,
        test_size: float = 0.1,
    ) -> tuple[Path, dict]:
        """训练模型。

        Args:
            dataset_path: 数据集文件路径
            model_version: 模型版本号（默认使用时间戳）
            tfidf_config: TF-IDF 配置
            model_config: 模型配置
            test_size: 验证集比例

        Returns:
            (模型文件路径, 训练指标)
        """
        import joblib

        logger.info(f"开始训练模型，数据集: {dataset_path}")

        texts, labels = DatasetGenerator.load_dataset(dataset_path)

        vectorizer, model, metrics = train_semantic_model(
            texts=texts,
            labels=labels,
            test_size=test_size,
            tfidf_config=tfidf_config,
            model_config=model_config,
        )

        if model_version is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            model_version = timestamp

        model_path = self.model_dir / f"semantic_interest_{model_version}.pkl"

        bundle = {
            "vectorizer": vectorizer,
            "model": model,
            "meta": {
                "version": model_version,
                "trained_at": datetime.now().isoformat(),
                "dataset": str(dataset_path),
                "train_samples": len(texts),
                "metrics": metrics,
                "tfidf_config": vectorizer.get_config(),
                "model_config": model.get_config(),
            },
        }

        joblib.dump(bundle, model_path)
        logger.info(f"模型已保存到: {model_path}")

        return model_path, metrics

    async def full_training_pipeline(
        self,
        persona_info: dict[str, Any],
        days: int = 7,
        max_samples: int = 1000,
        llm_model_name: str | None = None,
        tfidf_config: dict | None = None,
        model_config: dict | None = None,
        dataset_name: str | None = None,
        model_version: str | None = None,
        max_samples_per_batch: int = 50,
        keyword_iterations: int = 3,
    ) -> tuple[Path, Path, dict]:
        """完整训练流程。

        Args:
            persona_info: 人格信息
            days: 采样天数
            max_samples: 最大采样数
            llm_model_name: LLM 模型任务名
            tfidf_config: TF-IDF 配置
            model_config: 模型配置
            dataset_name: 数据集名称
            model_version: 模型版本
            max_samples_per_batch: 每批 LLM 标注的消息条数
            keyword_iterations: 关键词生成迭代次数

        Returns:
            (数据集路径, 模型路径, 训练指标)
        """
        logger.info("开始完整训练流程")

        dataset_path = await self.prepare_dataset(
            persona_info=persona_info,
            days=days,
            max_samples=max_samples,
            model_name=llm_model_name,
            dataset_name=dataset_name,
            max_samples_per_batch=max_samples_per_batch,
            keyword_iterations=keyword_iterations,
        )

        model_path, metrics = self.train_model(
            dataset_path=dataset_path,
            model_version=model_version,
            tfidf_config=tfidf_config,
            model_config=model_config,
        )

        logger.info("完整训练流程完成")
        logger.info(f"数据集: {dataset_path}")
        logger.info(f"模型: {model_path}")
        logger.info(f"指标: {metrics}")

        return dataset_path, model_path, metrics
