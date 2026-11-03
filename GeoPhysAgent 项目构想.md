# GeoPhysAgent 项目构想

## 1. 项目定位

**GeoPhysAgent** 是一个面向地球物理领域的专业智能体（Geophysics Agent），目标不是简单地让大语言模型调用几个地球物理 Python 库，而是构建一个能够理解地球物理任务、自动规划分析流程、调用专业工具、执行计算、验证结果并最终生成分析成果的智能体系统。

项目的核心定位为：

> **GeoPhysAgent — An Agent Harness for Geophysics**

即：**面向地球物理任务的 Agent Harness**。

项目重点关注地球物理数据分析与处理场景，包括地震数据、测井数据、地震属性、断层解释、地球物理反演、地球物理建模以及相关 AI 数据处理任务。

------

## 2. 为什么需要 GeoPhysAgent

目前的大语言模型已经具备较强的代码生成、工具调用和任务规划能力，但面对专业地球物理任务时仍然存在明显问题。

例如用户提出：

> “分析这个工区的地震属性并提取主要断层。”

传统 LLM Agent 可能只能完成简单的：

```text
读取文件
→ 调用 Python
→ 绘图
→ 返回解释
```

但真正的地球物理任务通常需要：

```text
理解任务
→ 判断数据类型
→ 检查数据质量
→ 选择合适的处理方法
→ 设置参数
→ 执行计算
→ 检查中间结果
→ 判断结果是否合理
→ 必要时重新执行
→ 可视化
→ 输出解释结果
```

因此，GeoPhysAgent 希望解决的不只是“LLM 如何调用工具”，而是：

> **如何让 Agent 在地球物理专业环境中可靠地完成一个完整任务。**

这也是项目采用 **Agent Harness** 思路的原因。

------

## 3. 核心设计思想

GeoPhysAgent 不把 Agent 本体和所有专业能力耦合在一起，而采用分层架构。

整体结构为：

```text
                         GeoPhysAgent
                              │
                     ┌────────┴────────┐
                     │   Agent Harness │
                     │                 │
                     │ Task Manager    │
                     │ Context        │
                     │ Memory         │
                     │ Permission     │
                     │ Checkpoint     │
                     │ Validation     │
                     │ Recovery       │
                     │ Logging        │
                     └────────┬────────┘
                              │
                    Geo Agent Runtime
                     自研运行时内核
                              │
              ┌───────────────┴───────────────┐
              ↓                               ↓
           LLM Model                     Geo Skills
                                              │
                                      Geo Tool Registry
                                               │
                          ┌────────────────────┴────────────────────┐
                          ↓                                         ↓
                 Built-in Tool Provider                     MCP Tool Provider
                 First-party Tools                     MCP Client Manager
                                                                  │
                                         ┌────────────────────────┼───────────────┐
                                         ↓                        ↓               ↓
                                  Bundled MCP Server       External Server   Sidecar
                                  Reference Integration    Personal Project  Vendor API
                              │
                         Validator
                              │
                              ↓
                     Analysis / Figure /
                       Report / Result
```

其中：

**Geo Agent Runtime** 是 GeoPhysAgent 自研的运行时内核，负责 Agent Loop、模型调用、工具调用、上下文和扩展机制。`pi/` 中的代码只作为架构和实现参考，不作为项目依赖，也不在其目录中开发 GeoPhysAgent 代码。

**Agent Harness** 负责控制 Agent 如何执行任务，包括任务管理、状态管理、权限管理、上下文、检查点、错误恢复、结果验证和执行记录。

**Geo Skills** 表示地球物理领域知识与工作流程。

**Geo Tools** 是 Agent 可调用的统一专业能力。Geo Tool Registry 与实现方式无关：第一方能力可以通过内置 Tool Provider 注册，跨进程、个人项目和外部软件能力可以通过 MCP Tool Provider 接入。两类 Tool 都接受同一套权限、状态、验证和审计约束。

**Validator** 用于检查 Agent 输出是否满足基本的科学与数据约束。

------

## 4. Pi 作为参考实现

项目保留 `pi/` 目录作为 Agent Runtime 的参考代码库，用于研究以下设计：

- Agent Loop
- Tool Calling
- Session / State
- Extension 与 Hook
- Skills
- LLM 接口
- 中断、恢复与事件模型

GeoPhysAgent 不直接依赖 Pi 的 npm 包、运行时、CLI 或源码模块，也不在 `pi/` 内创建任何 GeoPhysAgent 代码。需要类似能力时，优先根据 GeoPhysAgent 的需求独立设计和重写；确需复制少量实现时，必须进行许可证审查、保留来源与版权信息，并在 GeoPhysAgent 自己的模块中维护。

两者关系为：

```text
pi/ 参考代码（只读）
        │
        │ 研究设计、验证思路
        ▼
GeoPhysAgent 自研实现
      ├── Geo Agent Runtime
      ├── Geo-specific Harness
      ├── Geophysics Skills
      ├── Geophysics Tools
      └── Validation
```

这种方式避免产品运行时受 Pi API、版本和发布节奏约束，也使 GeoPhysAgent 能围绕可验证地球物理任务建立更小、更清晰的运行内核。

------

## 5. GeoPhysAgent 的核心能力

项目计划逐步形成以下几个核心能力。

### 5.1 地球物理任务理解

用户可以直接使用自然语言描述任务，例如：

```text
分析这个 LAS 文件中的测井曲线。
计算这套地震数据的 RMS 振幅并绘制结果。
分析该工区的断层发育特征。
预测缺失的横波速度曲线。
```

Agent 根据任务自动确定需要使用的数据、工具和处理流程。

------

### 5.2 专业工具调用

Agent 可以调用内置控制工具、第一方专业工具，也可以通过 MCP Server 调用外部地球物理能力。

例如：

```text
SEGY
LAS
GDAL
GeoPandas
Shapely
Rasterio
NumPy
SciPy
PyTorch
OpenCV
scikit-image
```

后续可以进一步接入：

```text
QGIS / PyQGIS
地震解释工具
地球物理处理软件
自研算法
AI 模型
```

MCP 是 GeoPhysAgent 的一种集成能力，不是产品本体，也不是唯一 Tool 通道。需要跨进程、跨语言、远程部署或隔离厂商依赖时，GeoPhysAgent 可以作为 MCP Host 管理多个 MCP Client。地球物理软件、个人项目或远程计算服务可选择以独立 MCP Server 接入；Server 内部可以调用 Python 库、软件 API、SDK、CLI 或插件接口，但这些依赖不会进入 GeoPhysAgent Core。

```text
GeoPhysAgent
      └── Geo Tool Registry
             ├── Built-in Tool Provider
             └── MCP Tool Provider
                    └── MCP Client Manager
                           ├── LAS MCP Server（参考集成）
                           ├── Seismic MCP Server
                           ├── GIS MCP Server
                           └── Vendor Software MCP Sidecar
```

大型 LAS、SEGY 和地震体不通过 MCP 消息传输，MCP 只传递任务参数、DataAsset 引用、Job 状态和 Artifact 引用。

------

### 5.3 Geophysics Skills

通过 Skill 把专业知识和标准处理流程组织起来。

例如：

```text
skills/
├── seismic-analysis/
├── seismic-attribute/
├── fault-interpretation/
├── well-log-analysis/
├── velocity-analysis/
├── seismic-inversion/
├── geophysical-modeling/
└── gis-analysis/
```

一个 Skill 不只是告诉 Agent“有什么工具”，还可以描述：

```text
什么时候使用
输入数据要求
处理流程
参数选择原则
工具调用顺序
结果判断方法
常见错误
结果输出规范
```

这样可以逐渐积累专业领域知识。

------

## 6. Harness 是项目真正的核心

GeoPhysAgent 最重要的创新点并不是简单增加更多 Tool，而是建立一套适合地球物理任务的 Agent Harness。

例如：

```text
用户任务
   ↓
Task Manager
   ↓
任务拆解
   ↓
Geo Agent Runtime
   ↓
通过 MCP Server 调用 Geophysics Tools
   ↓
产生中间结果
   ↓
Validator
   ↓
是否合理？
 ┌─┴─────────┐
 │           │
否           是
 │           │
Recovery     下一阶段
 │           │
 └────→ Agent
               ↓
          最终结果
```

因此，Harness 负责解决的是：

> **Agent 做什么、怎么做、做到哪一步、出了问题怎么办、结果能不能相信。**

而 Geo Agent Runtime 主要解决的是：

> **Agent 如何进行推理、规划和工具调用。**

------

## 7. 结果验证机制

地球物理任务与普通 Coding Agent 的一个重要区别是：

**代码运行成功并不意味着结果正确。**

例如 Agent 成功生成了断层多边形，并不能说明结果一定合理。

因此 GeoPhysAgent 计划建立独立的 Validator：

```text
Input
 ↓
Processing
 ↓
Result
 ↓
Validator
```

Validator 可以检查：

```text
数据范围
坐标系
空间范围
几何合法性
异常值
统计特征
结果数量
参数合理性
物理约束
```

例如：

```text
Agent：
提取出了 130 个断层多边形。

Validator：
发现 17 个多边形存在异常几何。

        ↓

Harness：
结果不通过。

        ↓

Agent：
调整处理参数并重新执行。

        ↓

Validator：
PASS
```

这会成为 GeoPhysAgent 与普通通用 Agent 的重要区别。

------

## 8. 第一阶段 MVP

项目第一阶段不追求覆盖完整的地球物理领域，而是建立一个最小可运行闭环。

MVP 可以设计为：

```text
自然语言任务
     ↓
GeoPhysAgent
     ↓
Geo Agent Runtime
     ↓
Geo Tool Registry
     ├── Built-in Tool Provider
     └── MCP Tool Provider
              ↓
        Bundled LAS MCP Server
     ↓
计算结果
     ↓
Validator
     ↓
Markdown / Figure / Data Result
```

第一版工具可以控制在 5～10 个，统一注册为 Geo Tools。它们可以由内置 Provider 或随项目发布的 LAS MCP Server 提供；Harness 不依赖具体 Provider。

例如首先实现：

```text
inspect_las()
curve_statistics()
check_depth_axis()
detect_curve_anomalies()
plot_curve()
validate_result()
```

先实现：

> **读取数据 → 分析 → 调用工具 → 验证 → 输出结果**

形成完整闭环之后，再逐渐增加专业能力。

------

## 9. 项目演进路线

### v0.1 — LAS QC Reference Platform

独立实现最小 Agent Runtime、GeoDomainHarness、Geo Tool/Provider Registry、Validator 和分析包，形成第一个可交付闭环；同时实现 MCP Client Manager 和 Bundled LAS MCP Server，验证 MCP 集成能力。

### v0.2 — External MCP Integration

接入个人项目 MCP Server 和外部软件 MCP Sidecar，完善 Streamable HTTP、认证、权限和长任务恢复。

### v0.3 — Geophysics Skills

建立地震、测井、GIS 等专业 Skill。

### v0.4 — Harness Hardening

强化 Task、Context、Permission、Logging、Session、MCP 隔离和审计机制。

### v0.5 — Validation

建立地球物理结果验证机制。

### v0.6 — Checkpoint & Recovery

支持长任务、中断恢复和失败重试。

### v0.7 — Memory

加入任务历史、用户偏好以及领域知识记忆。

### v0.8 — Human-in-the-loop

对于高风险或关键处理步骤要求用户确认。

### v0.9 — Multi-Agent

针对复杂任务引入多个专业 Agent。

### v1.0 — GeoPhysAgent Platform

形成完整的地球物理智能体平台。

------

## 10. 推荐的项目结构

```text
GeoPhysAgent/
│
├── core/
│   ├── agent/
│   ├── task/
│   ├── context/
│   └── session/
│
├── harness/
│   ├── task_manager.py
│   ├── tool_registry.py
│   ├── permission.py
│   ├── validator.py
│   ├── checkpoint.py
│   ├── recovery.py
│   └── logger.py
│
├── runtime/
│   ├── agent_loop/
│   ├── model/
│   ├── events/
│   └── session/
│
├── integrations/
│   ├── mcp_client/
│   ├── server_registry/
│   └── manifests/
│
├── skills/
│   ├── seismic/
│   ├── well_logging/
│   ├── fault_interpretation/
│   ├── inversion/
│   └── gis/
│
├── tools/
│   ├── seismic/
│   ├── well/
│   ├── raster/
│   ├── vector/
│   ├── ai/
│   └── visualization/
│
├── mcp_servers/
│   ├── las/
│   └── examples/
│
├── validators/
│   ├── data/
│   ├── geometry/
│   ├── statistics/
│   └── physics/
│
├── examples/
│
├── tests/
│
├── docs/
│
├── README.md
├── ROADMAP.md
└── LICENSE
```

------

## 11. 项目的长期目标

GeoPhysAgent 最终希望实现的不是：

> 一个会写地球物理 Python 代码的聊天机器人。

而是：

> **一个能够独立理解、规划、执行、验证和记录地球物理数据分析任务的专业智能体系统。**

理想状态下，用户只需要告诉 Agent：

```text
“分析这个工区的地震数据，
提取主要地震属性，
识别可能的断层，
检查结果，
并生成一份分析报告。”
```

GeoPhysAgent 就能够自主完成：

```text
任务理解
    ↓
数据检查
    ↓
任务规划
    ↓
Skill 选择
    ↓
Tool 调用
    ↓
数据处理
    ↓
中间结果检查
    ↓
异常恢复
    ↓
结果验证
    ↓
可视化
    ↓
报告生成
```

因此，整个项目可以概括成：

> **GeoPhysAgent 自研 Agent Runtime 提供基础运行能力，GeoPhysAgent Harness 是任务执行与验证核心，Geophysics Skills 和 Tools 提供专业知识与计算，MCP 是其中一种标准化外部能力接入机制，Validator 提供结果可信度保障；Pi 仅作为只读参考实现。**

最终形成一个面向地球物理领域、开放、可扩展、可验证的 **Agent Harness**。
