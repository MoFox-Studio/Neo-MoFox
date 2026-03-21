# protocols 模块

对应源码：src/core/models/protocols.py

## 当前状态

当前文件仅包含模块级说明注释，尚未定义具体 Protocol 接口。

## 设计意图

该模块用于承载基于 typing.Protocol 的结构化接口约束，用于解耦服务实现与调用方。

## 建议约定

后续新增 Protocol 时建议：

- 按能力拆分接口，而非建立过大的聚合接口。
- 为每个 Protocol 提供最小可实现示例。
- 在 managers 或 transport 中明确标注消费点。

## 维护建议

- Protocol 增删字段时，同步检查所有实现类。
- 若接口稳定性要求较高，可在 docs 中记录版本变更点。
