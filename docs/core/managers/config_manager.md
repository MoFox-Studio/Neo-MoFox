# config_manager 模块

对应源码：src/core/managers/config_manager.py

## 概述

ConfigManager 负责插件配置实例的加载、重载与缓存，避免重复读取配置文件。

## 核心职责

- 按插件名加载配置并缓存实例。
- 支持配置重载并覆盖旧缓存。
- 提供配置查询与移除接口。

## 关键入口

- load_config
- reload_config
- get_config
- get_config_manager

## 说明

- 配置底层能力依赖 BaseConfig.load_for_plugin 与 reload。
