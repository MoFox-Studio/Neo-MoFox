# user_query_helper 模块

对应源码：src/core/utils/user_query_helper.py

## 概述

UserQueryHelper 提供统一用户查询与更新能力，封装 PersonInfo、ChatStreams、Messages 的常用查询操作。

## 核心能力

- person_id 生成
- 用户信息获取或创建
- 用户信息更新
- 用户聊天流查询
- 用户最近消息查询
- 用户印象与态度更新

## person_id 规则

- generate_raw_person_id: platform:user_id
- generate_person_id: sha256(platform_user_id)

哈希 ID 用于跨平台统一主键，减少明文 ID 暴露。

## 关键接口

- get_or_create_person
不存在时创建用户并初始化交互统计。

- update_person_info
更新 nickname/cardname，不存在时自动创建。

- get_user_streams
按 last_active_time 倒序返回用户流列表。

- get_user_recent_messages
按时间倒序返回最近消息。

- update_user_impression
更新长期印象和简短印象。

- update_user_attitude
更新态度分并限制在 0-100。

## 缓存策略

- generate_person_id 使用 lru_cache。
- 部分异步查询接口使用 alru_cache。

## 单例入口

- get_user_query_helper 返回模块级单例。

## 注意事项

- 缓存存在时要关注昵称/名片更新的可见性时机。
- 与权限系统协作时建议统一使用哈希 person_id。
