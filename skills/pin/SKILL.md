---
name: pin
description: 管理项目情报（Project Information）文档系统。当需要创建、读取、更新或导航（搜索）项目文档时使用，包括业务知识（需求文档、操作手册）、技术文档（架构、开发环境、代码地图、研究记录）、UI设计（设计图、切图）、规范文档（git 规范、代码规范、示例）和任务日志。在项目初始化、编写需求/技术/UI/规范文档、管理开发任务、执行开发任务、或其他需要了解项目上下文时触发。
---

# Pin - 项目情报管理

## 概述

Pin 是一个结构化的项目文档管理系统，以 `pin-docs/` 为根目录，帮助 AI 从业务、技术、设计、规范、任务五个维度理解和管理项目信息。

核心目录结构：

```
pin-docs/
├── 0-业务知识/        # 业务知识：需求文档、操作手册
│   ├── 0-索引.md
│   ├── 1-需求文档/    # PRD，按业务领域组织
│   └── 2-操作手册/    # 用户视角的操作指南
├── 1-技术文档-X/      # 技术架构、开发环境、代码地图、研究记录
│   ├── 0-索引.md
│   ├── 1-技术架构.md
│   ├── 2-开发环境.md
│   ├── 3-代码地图/
│   └── 4-研究记录/
├── 2-UI设计/          # UI设计图、UI切图
│   ├── 0-索引.md
│   ├── 1-UI设计图/
│   └── 2-UI切图/
├── 3-规范文档-X/      # git 规范、代码规范、示例
│   ├── 0-索引.md
│   ├── 1-git规范.md
│   ├── 2-代码规范.md
│   └── 3-示例/
├── 4-任务日志/        # 开发任务管理
│   ├── 0-索引.md
│   ├── 0-未开始/
│   ├── 1-进行中/
│   └── 2-已完成/
└── 5-assets/         # 图片、截图等公共资源
```

完整的结构和文档定义参考 `references/idea.md`。

## 核心工作流

按需阅读以下工作流文档：

| 工作流 | 触发场景 | 文档 |
|---|---|---|
| 项目初始化 | 首次为项目创建 pin-docs 目录结构 | [references/workflows/1-项目初始化.md](references/workflows/1-项目初始化.md) |
| 文档导航 | 需要搜索/定位项目文档 | [references/workflows/2-文档导航.md](references/workflows/2-文档导航.md) |
| 文档 CRUD | 需要创建、更新或删除文档 | [references/workflows/3-文档CRUD.md](references/workflows/3-文档CRUD.md) |
| 任务管理 | 需要创建/执行/流转开发任务 | [references/workflows/4-任务管理.md](references/workflows/4-任务管理.md) |
| 索引维护规则 | 执行任何文档增删改操作前必读 | [references/workflows/5-索引维护规则.md](references/workflows/5-索引维护规则.md) |

**重要**：索引维护规则是强制规则，在执行任何文档增删改操作前务必阅读。

## 文档类型指南

注意：写任何文档前，**一定**要阅读并理解文档定义 `references/idea.md`。

### 0-业务知识/1-需求文档

**用途**：记录业务需求、领域知识、边界上下文。

**关键文件**：

- `0-overview.md`：项目背景、通用语言（Ubiquitous language）、领域清单、领域关系图。模板：`references/templates/overview.md`
- `<业务领域>.md`：单一业务领域的需求描述。模板：`references/templates/requirement.md`

### 0-业务知识/2-操作手册

**用途**：从用户角度描述系统功能和使用方法。

**结构**：与需求文档保持领域对应。

**模板**：`references/templates/operation-manual.md`

### 1-技术文档-X/1-技术架构.md

**用途**：记录技术栈、微服务清单、模块关系。

**内容**：开发语言、重要依赖、微服务清单、模块关系。保持精简。

**模板**：`references/templates/tech-architecture.md`

### 1-技术文档-X/2-开发环境.md

**用途**：本地开发指南——如何启动、调试、使用外部资源、构建、CI/CD。

**模板**：`references/templates/dev-environment.md`

### 1-技术文档-X/3-代码地图

**用途**：代码索引，帮助 AI 定位代码。

**结构**：按业务领域组织，每个领域一个文件。包含代码清单、实体关系、调用关系、API 清单。

**特殊**：`0-公共组件.md` 单独记录公共领域的代码索引。

**模板**：`references/templates/code-map.md`

### 1-技术文档-X/4-研究记录

**用途**：保存技术研究沉淀，帮助 AI 复用以前的技术成果，避免重复踩坑。

**内容范围**：开发中遇到的技术坑点、难以处理的 BUG、复杂函数逻辑/调用链分析、与项目相关的技术文章等。

**结构**：以 `<研究主题>/research.md` 的形式组织，每个研究主题一个子目录。

**模板**：`references/templates/research.md`

### 2-UI设计

**用途**：存放 UI 设计图和前端切图，帮助 AI 理解界面设计和视觉规范。

- `1-UI设计图/`：产品设计稿、界面流程图、交互原型截图等
- `2-UI切图/`：前端开发使用的切图资源（图标、背景图等）

### 3-规范文档-X

**用途**：告知 AI 应遵循的工程规范。

- `1-git规范.md`：分支规范、commit 规范。模板：`references/templates/git-convention.md`
- `2-代码规范.md`：总体要求和公共规范。模板：`references/templates/code-convention.md`
- `3-示例/`：具体代码示例。模板：`references/templates/example.md`

### 0-索引.md

**用途**：每层目录的导航入口，列出该目录下所有文档的关键词、摘要、路径。

**模板**：`references/templates/index-template.md`

### 任务文档

每个任务目录下：

- `task.md`：任务描述。模板：`references/templates/task.md`
- `plan.md`：开发计划。模板：`references/templates/plan.md`
- `result.md`：任务结果（完成时填写）。模板：`references/templates/result.md`

### 5-assets

**用途**：存放图片、截图等公共资源。

文档中需要引用图片时，使用相对路径（如 `../5-assets/login-flow.png`）指向 `5-assets/` 目录下的文件。

## 文档编写原则

1. **简明扼要**：只记录 AI 需要但无法从代码中直接获得的信息
2. **用图表达**：关系、流程使用 mermaid 绘制
3. **避免重复**：代码中显而易见的信息（属性名、方法名、详细逻辑）不要写入文档
4. **保持一致性**：需求文档、操作手册、代码地图的业务领域命名保持一致
5. **公共资源**：图片、截图等存放在 `5-assets/` 目录下，文档中使用相对路径引用

## 模板 Frontmatter 约定

模板 frontmatter 中的字段分两类：

- **占位符字段**：使用 `{占位符}` 语法（如 `created: "{YYYY-MM-DD}"`、`author: "{维护者}"`），AI 在实例化模板时需替换为实际值。
- **固定标记字段**：值不含 `{}`，无需替换。如 `maintained-by: pin skill` 是固定标记，声明该文档由 pin skill 维护，实例化时保留原值。
