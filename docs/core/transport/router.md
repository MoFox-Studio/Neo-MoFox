# router 子模块

对应源码目录：src/core/transport/router

## 模块目标

router 子模块提供 HTTPServer 单例能力，作为 RouterManager 挂载插件 HTTP 接口的基础设施。

## 关键文件

- http_server.py: HTTPServer 实现与 get_http_server 单例入口。
- __init__.py: 模块导出。

## HTTPServer 核心职责

- 创建 FastAPI 主应用。
- 基于 uvicorn 在后台任务中启动服务。
- 暴露 start/stop/is_running/get_base_url/openapi 等管理接口。
- 支持运行时动态 mount 子应用和添加路由。

## 生命周期

1. get_http_server 首次调用创建全局实例。
2. start 启动 uvicorn server.serve 后台任务。
3. stop 设置 should_exit 并等待任务结束。
4. 超时时执行取消，避免退出阻塞。

## 与 RouterManager 协作

- RouterManager.mount_router 调用 get_http_server 并 mount 子 app。
- app/runtime 在 core 初始化阶段按配置决定是否启动 HTTPServer。

## 注意事项

- host/port 参数仅在首次创建单例时生效。
- 重复 start 会抛 RuntimeError。
