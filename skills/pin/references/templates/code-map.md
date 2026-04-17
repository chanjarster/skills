---
type: code-map
domain: "{业务领域}"
created: "{YYYY-MM-DD}"
---

# {业务领域} 代码地图

> 尽量精简，不要记录代码中显而易见的信息（属性名、方法名、详细逻辑）。

## 代码清单

- {名称}：
  - 关键词：{关键词}
  - 摘要：{摘要}
  - 代码：{文件路径}

## 实体关系

{使用 mermaid 或文字描述核心实体及其关系}

```mermaid
erDiagram
    ENTITY1 ||--o{ ENTITY2 : "关系"
    ENTITY1 {
        type field "说明"
    }
```

## 调用关系

{关键调用链路}

```mermaid
graph LR
    A[入口] --> B[处理层]
    B --> C[数据层]
```

## API 清单

- {方法} `{路径}`
  - 关键词：{关键词}
  - 摘要：{摘要}
  - 代码：{代码路径}
