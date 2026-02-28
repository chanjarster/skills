---
name: doc-writer
description: 帮助用户编写、管理文档：项目概述文档(project.md)、UI/UE规范文档(ui-ue-spec.md)、需求文档(req-xxx.md)、功能设计文档(feat-xxx.md)、模型设计文档(model-xxx.md)、接口设计文档(api-xxx.md)、静态 HTML 页面、变更记录文档(chg-project.md, chg-ui-ue-spec.md, chg-req-xxx.md, chg-feat-xxx.md, chg-api-xxx.md, chg-model-xxx.md)、开发任务文档(task-xxx.md)。执行编码任务时，同步更新开发任务文档 (task-xxx.md)，帮助用户管理开发任务的进度。
---

# doc-writer

## 概述

按照规范管理和组织软件开发过程中需要的文档，同时也对开发任务进行文档化管理。

## 目录结构

所有文档位于项目根目录的 `project-docs` 目录下，目录结构：

```
project-docs/
├─ latest/                    # 最新文档目录，存放的是 Current truth
│   ├─ [设计主题]/              # 设计主题目录，里面存放各类文档
│   │   ├─ req-xxx.md         # 需求文档
│   │   ├─ feat-xxx.md        # 功能设计文档
│   │   ├─ model-xxx.md       # 模型设计文档
│   │   ├─ api-xxx.md         # 接口设计文档
│   │   └─ static-html/       # 静态HTML原型目录
│   │       └─ page-xxx.html  # 静态 html 页面
│   ├─ ui-ue-spec.md          # UI/UE规范
│   └─ project.md             # 项目概述文档
├─ changes/                   # 存放历次的变更记录
│   └─ [变更主题]/             # 变更记录目录
│       ├─ chg-req-xxx.md     # 需求变更文档
│       ├─ chg-feat-xxx.md    # 功能设计变更文档
│       ├─ chg-model-xxx.md   # 模型设计变更文档
│       ├─ chg-api-xxx.md     # 接口变更设计文档
│       ├─ chg-project.md     # 项目概述变更文档
│       ├─ chg-ui-ue-spec.md  # UI/UE规范变更文档
│       └─ static-html/            # 静态HTML原型目录
│           └─ chg-page-xxx.html   # 变更后的静态 html 页面
└─ tasks/                     # 开发任务文档目录
    ├─ completed/             # 已完成任务目录
    └─ task-xxx.md            # 进行中的开发任务文档
```

## 最新文档目录

### 项目概述文档

路径： `project-docs/latest/project.md`
文档内容要求：见 [project.md](references/project.md)

### UI/UE规范文档

路径： `project-docs/latest/ui-ue-spec.md`
文档内容要求：见 [ui-ue-spec.md](references/ui-ue-spec.md)

### 设计主题文档目录

目录：`project-docs/latest/[设计主题]`

### 需求文档

路径：`project-docs/latest/[设计主题]/req-xxx.md`
文档内容要求：见 [req-xxx.md](references/req-xxx.md)

### 功能设计文档

路径：`project-docs/latest/[设计主题]/feat-xxx.md`
文档内容要求：见 [feat-xxx.md](references/feat-xxx.md)

功能点的设计，包括功能描述、功能实现流程、业务规则、使用角色、界面设计要求等关键内容。

### 模型设计文档

路径：`project-docs/latest/[设计主题]/model-xxx.md`
文档内容要求：见 [model-xxx.md](references/model-xxx.md)

### 接口设计文档

路径：`project-docs/latest/[设计主题]/api-xxx.md`
文档内容要求：见 [api-xxx.md](references/api-xxx.md)

### 静态HTML原型文件

路径：`project-docs/latest/[设计主题]/static-html/page-xxx.html`

静态的 HTML 原型页面。

## 变更文档目录

### 项目概述变更文档

路径：`project-docs/changes/[变更主题]/chg-project.md`
文档内容要求：见 [chg-project.md](references/chg-project.md)

### UI/UE规范变更文档

路径：`project-docs/changes/[变更主题]/chg-ui-ue-spec.md`
文档内容要求：见 [chg-ui-ue-spec.md](references/chg-ui-ue-spec.md)

### 需求变更

路径：`project-docs/changes/[变更主题]/chg-req-xxx.md`
文档内容要求：见 [chg-req-xxx.md](references/chg-req-xxx.md)

### 功能设计变更文档

路径：`project-docs/changes/[变更主题]/chg-feat-xxx.md`
文档内容要求：见 [chg-feat-xxx.md](references/chg-feat-xxx.md)

### 模型设计变更

路径：`project-docs/changes/[变更主题]/chg-model-xxx.md`
文档内容要求：见 [chg-model-xxx.md](references/chg-model-xxx.md)

### 接口设计变更

路径：`project-docs/changes/[变更主题]/chg-api-xxx.md`
文档内容要求：见 [chg-api-xxx.md](references/chg-api-xxx.md)

### 静态HTML原型（changes/[变更主题]/static-html/chg-page-xxx.html

路径：`project-docs/changes/[变更主题]/static-html/chg-page-xxx.html`

变更后的静态 HTML 原型页面。

## 任务文档目录

### 任务文档

路径：`project-docs/tasks/task-xxx.md`
文档内容要求：见 [task-xxx.md](references/task-xxx.md)

完成任务的每个阶段后，同步更新 `task-xxx.md`。

## 要求

- 按照上述规范对文档进行命名
- 按照上述规范把文档放到正确的目录下
- 如果要在文档中绘制图形，那么使用 mermaid 代码块
- 根据对话内容，自动创建目录
- 根据对话内容，自动更新文档
