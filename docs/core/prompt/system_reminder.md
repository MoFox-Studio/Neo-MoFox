# system_reminder 模块

对应源码：src/core/prompt/system_reminder.py

## 概述

SystemReminderStore 提供轻量级内存提醒存储，用于按 bucket/name 组织系统提醒文本。

## 核心概念

- bucket: 提醒分类
- name: 分类内唯一键
- content: 提醒文本

预定义 bucket 枚举：

- actor
- sub_actor

## 核心接口

- set(bucket, name, content)
写入或覆盖提醒。

- get(bucket, names=None)
读取并渲染提醒块文本。

- delete(bucket, name)
删除单条提醒。

- clear_bucket(bucket)
清空某个 bucket。

- clear_all()
清空全部提醒。

## 输出格式

get 返回多块文本，格式为：

[name]
content

多块之间以空行分隔。

## 并发与安全

- 使用 RLock 保证线程安全。
- bucket/name/content 均进行非空校验。

## 全局实例

- get_system_reminder_store 获取全局单例。
- reset_system_reminder_store 供测试重置。
