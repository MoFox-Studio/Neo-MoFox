# permission_manager 模块

对应源码：src/core/managers/permission_manager.py

## 概述

PermissionManager 负责权限组与命令级覆盖权限，提供统一的权限检查与授权修改能力。

## 核心职责

- 生成 raw/hash 两种 person_id。
- 管理用户权限组（owner/operator/user/guest）。
- 管理命令级授权覆盖（允许或禁止）。
- 结合配置与数据库执行权限判定。

## 关键入口

- get_user_permission_level
- set_user_permission_group
- check_command_permission
- get_permission_manager

## 相关模型

- PermissionGroups
- CommandPermissions
