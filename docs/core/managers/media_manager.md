# media_manager 模块

对应源码：src/core/managers/media_manager.py

## 概述

MediaManager 管理图片与表情包识别、缓存与清理，重点围绕 VLM 识别降本与媒体文件管理。

## 核心职责

- 初始化 VLM 模型集合并注册识别 Prompt。
- 管理媒体缓存目录（pending/images/emojis）。
- 基于哈希去重识别结果并持久化。
- 注册定时清理任务，处理陈旧待识别文件。

## 关键入口

- recognize_image
- save_media_info
- get_media_manager / initialize_media_manager

## 注意事项

- 文件中存在 asyncio.create_task 调度逻辑，后续可评估统一迁移到 task_manager。
