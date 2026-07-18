"""数据集生成与 LLM 标注。

从数据库采样消息并使用 LLM 进行兴趣度标注，生成训练数据集。
"""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import json_repair

from src.app.plugin_system.api.log_api import get_logger

logger = get_logger("default_chatter.semantic_interest.dataset")


class DatasetGenerator:
    """训练数据集生成器。

    从历史消息中采样并使用 LLM 进行标注。
    """

    HARD_MAX_SAMPLES = 5000

    ANNOTATION_PROMPT = """你是一个帮助标注消息兴趣度的专家。你需要根据人格设定判断该消息是否会引起角色的兴趣。

## 人格信息
{persona_info}

## 消息内容
{message_text}

## 标注规则
请判断角色对这条消息的兴趣程度，返回以下之一：
- **-1**: 完全不感兴趣或排斥（话题不相关、违背价值观、无聊重复等）
- **0**: 中立（可以回应但不特别感兴趣）
- **1**: 感兴趣（话题相关、符合兴趣点、能产生深度对话）

只需返回数字 -1、0 或 1，不要其他内容。"""

    BATCH_ANNOTATION_PROMPT = """你是一个帮助标注消息兴趣度的专家。你需要根据人格设定判断每条消息是否会引起角色的兴趣。

## 人格信息
{persona_info}

## 标注规则
对每条消息判断角色的兴趣程度：
- **-1**: 完全不感兴趣或排斥（话题不相关、违背价值观、无聊重复等）
- **0**: 中立（可以回应但不特别感兴趣）
- **1**: 感兴趣（话题相关、符合兴趣点、能产生深度对话）

## 消息列表
{messages_list}

## 输出格式
请严格按照以下JSON格式返回，每条消息一个标签：
```json
{example_output}
```

只返回JSON，不要其他内容。"""

    KEYWORD_GENERATION_PROMPT = """你是一个帮助生成训练数据的专家。请根据人格设定生成感兴趣和不感兴趣的关键词/短语列表。

## 人格信息
{persona_info}

## 任务说明
请分别生成该角色**感兴趣**和**不感兴趣**的关键词或短语：

1. **感兴趣的关键词**：包括但不限于该角色喜欢的话题、活动、领域、价值观相关词汇等（约30-50个）
2. **不感兴趣的关键词**：包括该角色不关心、反感、无聊的话题、价值观冲突的内容等（约30-50个）

## 输出格式
请严格按照以下JSON格式返回：
```json
{{
  "interested": ["关键词1", "关键词2", "关键词3", ...],
  "not_interested": ["关键词1", "关键词2", "关键词3", ...]
}}
```

注意：
- 关键词可以是单个词语或短语（2-10个字）
- 尽量覆盖多样化的话题和场景
- 确保关键词与人格设定高度相关

只返回JSON，不要其他内容。"""

    def __init__(
        self,
        model_name: str | None = None,
        max_samples_per_batch: int = 50,
    ) -> None:
        """初始化数据集生成器。

        Args:
            model_name: LLM 模型任务名（None 则使用 "utils"）
            max_samples_per_batch: 每批次最大采样数
        """
        self.model_name = model_name or "utils"
        self.max_samples_per_batch = max_samples_per_batch
        self._request_builder: Any = None
        self._model_set: Any = None

    async def initialize(self) -> None:
        """初始化 LLM 请求构建器。"""
        try:
            from src.core.config import get_model_config

            self._model_set = get_model_config().get_task(self.model_name)
            self._request_builder = True
            logger.info("数据集生成器初始化完成")
        except Exception as e:
            logger.error(f"LLM 请求构建器初始化失败: {e}")
            self._request_builder = None
            self._model_set = None

    async def _llm_generate(self, prompt: str, max_tokens: int = 500, temperature: float = 0.7) -> str:
        """使用 LLM 生成文本。

        Args:
            prompt: 提示词
            max_tokens: 最大生成 token 数
            temperature: 温度参数

        Returns:
            LLM 生成的文本
        """
        if not self._request_builder:
            await self.initialize()

        if not self._request_builder or not self._model_set:
            logger.warning("LLM 请求构建器未初始化")
            return ""

        try:
            from src.kernel.llm import LLMRequest, LLMPayload, ROLE, Text

            request = LLMRequest(
                model_set=self._model_set,
                request_name="semantic_annotation",
            )
            request.add_payload(LLMPayload(ROLE.USER, Text(prompt)))

            response = await request.send(stream=False)
            await response

            return response.message or ""
        except Exception as e:
            logger.error(f"LLM 生成失败: {e}")
            return ""

    async def sample_messages(
        self,
        days: int = 7,
        min_length: int = 5,
        max_samples: int = 1000,
        priority_ranges: list[tuple[float, float]] | None = None,
    ) -> list[dict[str, Any]]:
        """从数据库采样消息。

        Args:
            days: 采样最近 N 天的消息
            min_length: 最小消息长度
            max_samples: 最大采样数量
            priority_ranges: 优先采样的兴趣分范围列表

        Returns:
            消息样本列表
        """
        from src.core.models.sql_alchemy import Messages
        from src.app.plugin_system.api import database_api

        logger.info(f"开始采样消息，时间范围: 最近 {days} 天，目标数量: {max_samples}")

        if max_samples <= 0:
            logger.warning(f"max_samples={max_samples} 非法，返回空样本")
            return []

        if max_samples > self.HARD_MAX_SAMPLES:
            logger.warning(
                f"max_samples={max_samples} 超过硬上限 {self.HARD_MAX_SAMPLES}，"
                f"已截断为 {self.HARD_MAX_SAMPLES}"
            )
            max_samples = self.HARD_MAX_SAMPLES

        cutoff_time = datetime.now() - timedelta(days=days)
        cutoff_ts = cutoff_time.timestamp()

        try:
            messages = await database_api.filter_query(
                Messages,
                time__gte=cutoff_ts,
            )
        except Exception as e:
            logger.error(f"采样消息查询失败: {e}")
            return []

        logger.info(f"预取 {len(messages)} 条消息")

        filtered: list[dict[str, Any]] = []
        for msg in messages:
            msg_type = getattr(msg, "message_type", None)
            if msg_type is not None:
                type_str = str(msg_type).lower()
                if type_str not in ("text", "messagetype.text", ""):
                    continue

            text = msg.processed_plain_text or msg.content or ""
            text = text.strip() if isinstance(text, str) else ""
            if text and len(text) >= min_length:
                filtered.append(
                    {
                        "message_id": msg.message_id,
                        "sender_id": msg.person_id,
                        "stream_id": msg.stream_id,
                        "time": msg.time,
                        "platform": msg.platform,
                        "message_text": text,
                    }
                )
                if len(filtered) >= max_samples:
                    break

        logger.info(f"过滤后得到 {len(filtered)} 条有效消息（目标: {max_samples}）")

        if len(filtered) < max_samples:
            logger.warning(
                f"过滤后消息数量 ({len(filtered)}) 少于目标 ({max_samples})，"
                f"可能需要扩大采样范围"
            )

        if filtered:
            random.shuffle(filtered)

        result: list[dict[str, Any]] = []
        for msg in filtered:
            result.append(
                {
                    "message_id": msg.get("message_id"),
                    "user_id": msg.get("sender_id"),
                    "chat_id": msg.get("stream_id"),
                    "message_text": msg.get("message_text", ""),
                    "timestamp": msg.get("time"),
                    "platform": msg.get("platform"),
                }
            )

        logger.info(f"采样完成，共 {len(result)} 条消息")
        return result

    async def generate_initial_keywords(
        self,
        persona_info: dict[str, Any],
        temperature: float = 0.7,
        num_iterations: int = 3,
    ) -> list[dict[str, Any]]:
        """使用 LLM 生成初始关键词数据集。

        Args:
            persona_info: 人格信息
            temperature: 生成温度
            num_iterations: 重复生成次数

        Returns:
            初始数据集列表
        """
        logger.info(
            f"开始生成初始关键词数据集，温度={temperature}，迭代{num_iterations}次"
        )

        persona_desc = self._format_persona_info(persona_info)
        prompt = self.KEYWORD_GENERATION_PROMPT.format(persona_info=persona_desc)

        all_keywords_data: list[dict[str, Any]] = []

        for iteration in range(num_iterations):
            try:
                logger.info(f"第 {iteration + 1}/{num_iterations} 次生成关键词...")

                response_text = await self._llm_generate(
                    prompt=prompt, max_tokens=1000, temperature=temperature
                )
                keywords_data = self._parse_keywords_response(response_text)

                if keywords_data:
                    interested = keywords_data.get("interested", [])
                    not_interested = keywords_data.get("not_interested", [])

                    logger.info(
                        f"  生成 {len(interested)} 个感兴趣关键词，"
                        f"{len(not_interested)} 个不感兴趣关键词"
                    )

                    for keyword in interested:
                        if keyword and keyword.strip():
                            all_keywords_data.append(
                                {
                                    "message_text": keyword.strip(),
                                    "label": 1,
                                    "source": "llm_generated_initial",
                                    "iteration": iteration + 1,
                                }
                            )

                    for keyword in not_interested:
                        if keyword and keyword.strip():
                            all_keywords_data.append(
                                {
                                    "message_text": keyword.strip(),
                                    "label": -1,
                                    "source": "llm_generated_initial",
                                    "iteration": iteration + 1,
                                }
                            )
                else:
                    logger.warning(f"第 {iteration + 1} 次生成失败，未能解析关键词")

            except Exception as e:
                logger.error(f"第 {iteration + 1} 次关键词生成失败: {e}")

        logger.info(
            f"初始关键词数据集生成完成，共 {len(all_keywords_data)} 条（不去重）"
        )

        return all_keywords_data

    def _parse_keywords_response(self, response: str) -> dict | None:
        """解析关键词生成的 JSON 响应。

        Args:
            response: LLM 响应文本

        Returns:
            解析后的字典，包含 interested 和 not_interested 列表
        """
        try:
            response = response.strip()
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                response = response.split("```")[1].split("```")[0].strip()

            repaired = json_repair.repair_json(response)
            data = json.loads(repaired)

            if isinstance(data, dict) and "interested" in data and "not_interested" in data:
                if isinstance(data["interested"], list) and isinstance(
                    data["not_interested"], list
                ):
                    return data

            logger.warning(f"关键词响应格式不正确: {data}")
            return None

        except json.JSONDecodeError as e:
            logger.error(f"解析关键词JSON失败: {e}")
            return None
        except Exception as e:
            logger.error(f"解析关键词响应失败: {e}")
            return None

    async def annotate_batch(
        self,
        messages: list[dict[str, Any]],
        persona_info: dict[str, Any],
        save_path: Path | None = None,
        batch_size: int = 50,
    ) -> list[dict[str, Any]]:
        """批量标注消息。

        Args:
            messages: 消息列表
            persona_info: 人格信息
            save_path: 保存路径（可选）
            batch_size: 每次 LLM 请求处理的消息数

        Returns:
            标注后的数据集
        """
        logger.info(
            f"开始批量标注，共 {len(messages)} 条消息，每批 {batch_size} 条"
        )

        annotated_data: list[dict[str, Any]] = []

        for i in range(0, len(messages), batch_size):
            batch = messages[i : i + batch_size]
            labels = await self._annotate_batch_llm(batch, persona_info)

            for msg, label in zip(batch, labels):
                annotated_data.append(
                    {
                        "message_id": msg["message_id"],
                        "message_text": msg["message_text"],
                        "label": label,
                        "user_id": msg.get("user_id"),
                        "chat_id": msg.get("chat_id"),
                        "timestamp": msg.get("timestamp"),
                    }
                )

            logger.info(f"已标注 {len(annotated_data)}/{len(messages)} 条")

        label_counts: dict[int, int] = {}
        for item in annotated_data:
            label = item["label"]
            label_counts[label] = label_counts.get(label, 0) + 1

        logger.info(f"标注完成，标签分布: {label_counts}")

        if save_path:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(annotated_data, f, ensure_ascii=False, indent=2)
            logger.info(f"数据集已保存到: {save_path}")

        return annotated_data

    async def _annotate_batch_llm(
        self,
        messages: list[dict[str, Any]],
        persona_info: dict[str, Any],
    ) -> list[int]:
        """使用一次 LLM 请求标注多条消息。

        Args:
            messages: 消息列表
            persona_info: 人格信息

        Returns:
            标签列表
        """
        persona_desc = self._format_persona_info(persona_info)

        messages_list = ""
        for idx, msg in enumerate(messages, 1):
            messages_list += f"{idx}. {msg['message_text']}\n"

        example_output = json.dumps(
            {str(i): 0 for i in range(1, len(messages) + 1)},
            ensure_ascii=False,
            indent=2,
        )

        prompt = self.BATCH_ANNOTATION_PROMPT.format(
            persona_info=persona_desc,
            messages_list=messages_list,
            example_output=example_output,
        )

        try:
            response_text = await self._llm_generate(
                prompt=prompt, max_tokens=500, temperature=0.1
            )
            labels = self._parse_batch_labels(response_text, len(messages))
            return labels
        except Exception as e:
            logger.error(f"批量LLM标注失败: {e}，返回默认值")
            return [0] * len(messages)

    def _format_persona_info(self, persona_info: dict[str, Any]) -> str:
        """格式化人格信息。

        Args:
            persona_info: 人格信息字典

        Returns:
            格式化后的人格描述
        """

        def _stringify(value: Any) -> str:
            if value is None:
                return ""
            if isinstance(value, (list, tuple, set)):
                return "、".join(
                    [str(v) for v in value if v is not None and str(v).strip()]
                )
            if isinstance(value, dict):
                try:
                    return json.dumps(value, ensure_ascii=False, sort_keys=True)
                except Exception:
                    return str(value)
            return str(value).strip()

        parts: list[str] = []

        name = _stringify(persona_info.get("name"))
        if name:
            parts.append(f"角色名称: {name}")

        personality_core = _stringify(persona_info.get("personality_core"))
        if personality_core:
            parts.append(f"核心人设: {personality_core}")

        personality_side = _stringify(persona_info.get("personality_side"))
        if personality_side:
            parts.append(f"侧面特质: {personality_side}")

        identity = _stringify(persona_info.get("identity"))
        if identity:
            parts.append(f"身份特征: {identity}")

        known_keys = {"name", "personality_core", "personality_side", "identity"}
        for key, value in persona_info.items():
            if key in known_keys:
                continue
            value_str = _stringify(value)
            if value_str:
                parts.append(f"{key}: {value_str}")

        return "\n".join(parts) if parts else "无特定人格设定"

    def _parse_batch_labels(self, response: str, expected_count: int) -> list[int]:
        """解析批量 LLM 响应为标签列表。

        Args:
            response: LLM 响应文本（JSON格式）
            expected_count: 期望的标签数量

        Returns:
            标签列表
        """
        try:
            if isinstance(response, (tuple, list)):
                response = response[0] if response else ""
            response = str(response)

            json_match = re.search(r"```json\s*({.*?})\s*```", response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = response

            repaired = json_repair.repair_json(json_str)
            labels_dict = json.loads(repaired)

            labels: list[int] = []
            for i in range(1, expected_count + 1):
                key = str(i)
                if isinstance(labels_dict, dict) and key in labels_dict:
                    label = labels_dict[key]
                    if label in [-1, 0, 1]:
                        labels.append(label)
                    else:
                        logger.warning(f"无效标签值 {label}，使用默认值 0")
                        labels.append(0)
                else:
                    if isinstance(labels_dict, list) and len(labels_dict) >= i:
                        label = labels_dict[i - 1]
                        labels.append(label if label in [-1, 0, 1] else 0)
                    else:
                        labels.append(0)

            if len(labels) != expected_count:
                logger.warning(
                    f"标签数量不匹配：期望 {expected_count}，实际 {len(labels)}，补齐"
                )
                if len(labels) < expected_count:
                    labels.extend([0] * (expected_count - len(labels)))
                else:
                    labels = labels[:expected_count]

            return labels

        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}，响应内容: {response[:200]}")
            return [0] * expected_count
        except Exception as e:
            try:
                numbers = re.findall(r"-?1|0", response)
                labels = [int(n) for n in numbers[:expected_count]]
                if len(labels) < expected_count:
                    labels.extend([0] * (expected_count - len(labels)))
                return labels
            except Exception:
                logger.error(f"批量标签解析失败: {e}")
                return [0] * expected_count

    @staticmethod
    def load_dataset(path: Path) -> tuple[list[str], list[int]]:
        """加载训练数据集。

        Args:
            path: 数据集文件路径

        Returns:
            (文本列表, 标签列表)
        """
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        texts = [item["message_text"] for item in data]
        labels = [item["label"] for item in data]

        logger.info(f"加载数据集: {len(texts)} 条样本")
        return texts, labels


async def generate_training_dataset(
    output_path: Path,
    persona_info: dict[str, Any],
    days: int = 7,
    max_samples: int = 1000,
    model_name: str | None = None,
    generate_initial_keywords: bool = True,
    keyword_temperature: float = 0.7,
    keyword_iterations: int = 3,
    max_samples_per_batch: int = 50,
) -> Path:
    """生成训练数据集（主函数）。

    Args:
        output_path: 输出文件路径
        persona_info: 人格信息
        days: 采样最近 N 天的消息
        max_samples: 最大采样数
        model_name: LLM 模型任务名
        generate_initial_keywords: 是否生成初始关键词数据集
        keyword_temperature: 关键词生成温度
        keyword_iterations: 关键词生成迭代次数
        max_samples_per_batch: 每批 LLM 标注的消息条数

    Returns:
        保存的文件路径
    """
    generator = DatasetGenerator(
        model_name=model_name,
        max_samples_per_batch=max_samples_per_batch,
    )
    await generator.initialize()

    initial_keywords_data: list[dict[str, Any]] = []
    if generate_initial_keywords:
        logger.info("步骤 1/3: 生成初始关键词数据集")
        initial_keywords_data = await generator.generate_initial_keywords(
            persona_info=persona_info,
            temperature=keyword_temperature,
            num_iterations=keyword_iterations,
        )
        logger.info(f"初始关键词数据集已生成: {len(initial_keywords_data)} 条")

    logger.info(f"步骤 2/3: 采样真实消息（最近 {days} 天，最多 {max_samples} 条）")
    messages = await generator.sample_messages(days=days, max_samples=max_samples)
    logger.info(f"消息采样完成: {len(messages)} 条")

    logger.info("步骤 3/3: LLM 标注真实消息")
    annotated_messages = await generator.annotate_batch(
        messages=messages,
        persona_info=persona_info,
        save_path=None,
        batch_size=max_samples_per_batch,
    )
    logger.info(f"消息标注完成: {len(annotated_messages)} 条")

    logger.info("合并数据集")
    combined_dataset: list[dict[str, Any]] = []

    if initial_keywords_data:
        combined_dataset.extend(initial_keywords_data)
        logger.info(f"  + 初始关键词: {len(initial_keywords_data)} 条")

    combined_dataset.extend(annotated_messages)
    logger.info(f"  + 标注消息: {len(annotated_messages)} 条")

    logger.info(f"合并后总计: {len(combined_dataset)} 条")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(combined_dataset, f, ensure_ascii=False, indent=2)

    logger.info(f"训练数据集已保存: {output_path}")

    return output_path
