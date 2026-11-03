# 外部参考代码与 `pi/` 使用政策

> 状态：Draft  
> 适用范围：GeoPhysAgent 全部源码、测试、构建与发布  
> 最后更新：2026-09-03

## 1. 政策结论

`pi/` 是外部参考仓库，只用于阅读、分析和验证设计思路。GeoPhysAgent：

- 不在 `pi/` 内创建或修改 GeoPhysAgent 代码；
- 不将 Pi npm 包作为依赖；
- 不通过源码 import、workspace、Git 子模块、动态加载或 RPC 调用 Pi；
- 不将 `pi/` 纳入 GeoPhysAgent 的构建、测试、打包和发布；
- 不批量复制 Pi 包或目录；
- 需要相似能力时优先依据 GeoPhysAgent 契约独立重写；
- 确需复制少量实现时，必须经过来源、许可证和维护性审查。

## 2. 允许的参考方式

- 阅读架构文档和源代码；
- 研究 Agent Loop、Tool Calling、事件、Session、Hook 和测试设计；
- 根据观察到的行为编写 GeoPhysAgent 自己的需求和测试；
- 比较异常处理、取消、并发和恢复边界；
- 记录有价值的设计模式及其适用性；
- 使用公开接口思想设计独立且更小的领域实现。

## 3. 禁止的耦合方式

- 在 GeoPhysAgent 源码中 import `@earendil-works/pi-*`；
- 从 GeoPhysAgent 构建脚本编译 `pi/packages/*`；
- 将 Pi CLI 作为 GeoPhysAgent 的子进程 Runtime；
- 使用 Pi Session 文件作为 GeoPhysAgent 的权威状态；
- 在 `pi/` 中增加 Geo Tool、Skill、Validator 或产品代码；
- 依靠修改 `pi/` 源码修复 GeoPhysAgent 功能；
- 未审查地复制完整文件、包、测试目录或生成代码。

## 4. 独立重写流程

独立重写是默认方式：

1. 先在 GeoPhysAgent 文档中定义需求、输入输出和失败行为；
2. 编写不依赖 Pi 的接口和黑盒测试；
3. 阅读外部实现以识别边界条件；
4. 返回 GeoPhysAgent 代码库，根据自己的契约完成实现；
5. 使用 GeoPhysAgent 自己的测试证明行为；
6. 在参考登记中记录参考仓库和提交，但不声称实现兼容 Pi。

## 5. 选择性复制流程

只有同时满足以下条件才允许复制少量代码：

- 独立重写成本明显不合理；
- 复制范围可以精确到函数或小型模块；
- 已确认原许可证允许计划中的使用和分发；
- 已识别其直接依赖，不会连带引入 Pi 子系统；
- 已获得项目维护者审批；
- 有独立测试覆盖复制后的行为；
- 文件中保留必要的版权和许可证声明；
- `THIRD_PARTY_NOTICES` 和来源登记得到更新。

复制登记至少包含：

```yaml
component: agent-loop-helper
source_repository: https://github.com/earendil-works/pi.git
source_commit: <commit>
source_path: <path>
license: <license>
copied_on: <date>
local_path: <path>
modifications: <summary>
reviewer: <name>
```

## 6. 仓库处理

GeoPhysAgent 初始化 Git 仓库时应：

- 默认在根 `.gitignore` 中忽略 `/pi/`；
- 保持 `pi/` 自己的 Git 元数据和远程关系独立；
- 不把 `pi/` 作为 Git 子模块提交；
- 在参考登记中记录当前参考提交；
- CI 在不含 `pi/` 的环境中完成所有构建和测试；
- 发布包生成检查必须拒绝包含 `pi/` 的产物。

## 7. 架构约束测试

CI 应增加以下检查：

- 源码和清单中不存在 `@earendil-works/pi-` 依赖；
- workspace 不包含 `pi/`；
- 构建输入不包含 `pi/`；
- 发布归档不包含 `pi/`；
- 选择性复制的文件均存在来源登记；
- 第三方声明与实际登记一致。

## 8. 参考不等于兼容

GeoPhysAgent 可以借鉴 Pi 的设计，但不承诺：

- API 兼容；
- Session 格式兼容；
- Skill 加载行为兼容；
- Extension 兼容；
- Tool Schema 兼容；
- 事件名称或顺序兼容。

GeoPhysAgent 的行为只由自己的产品规格、架构契约和测试定义。

## 9. MCP SDK 与外部 MCP Server

`pi/` 禁止依赖政策不等于禁止使用所有第三方库。GeoPhysAgent 可以依赖经过审查并固定版本的官方 MCP SDK，用于实现标准协议，前提是：

- SDK 出现在第三方依赖与许可证清单中；
- 版本被锁定；
- 升级前运行 MCP 契约测试；
- GeoPhysAgent 的领域对象不直接等同于 SDK 内部类型；
- SDK 可以通过 MCP Port 隔离和替换。

外部软件和个人项目的 MCP Server 是独立集成组件：

- 不复制到 GeoPhysAgent Core；
- 不反向依赖 `pi/`；
- 通过 Integration Manifest 注册；
- 使用自己的依赖、测试、许可证和发布周期；
- 只能访问 Host 明确授权的数据和凭据。
