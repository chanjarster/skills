---
name: dd-sync
description: >-
  将本地 Markdown 文档批量同步到钉钉知识库，支持新建和增量更新。
  当用户需要将本地文档、目录同步或推送到钉钉知识库时使用，包括但不限于：
  "把 docs/ 同步到钉钉知识库"、"批量上传 markdown 到钉钉"、
  "将这些文档推到知识库"、"帮我把本地 note 同步到钉钉上"、
  "/dd-sync"。依赖 dws skill 和 dws 命令操作钉钉知识库。
---

# dd-sync - 钉钉知识库文档同步 skill

> 本 skill 依赖 **dws skill** 和 **dws** 命令操作钉钉知识库，如果缺少相关依赖，则：
>
> - 按照 https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/blob/main/README_zh.md 安装下载
> - 运行 `dws auth login` 让用户完成 dws 工具的授权过程

## 技能概述

将本地目录中的 Markdown 文档批量同步到钉钉知识库，支持新建和增量更新。通过 YAML frontmatter 跟踪每个文档的同步状态。

**同步分为三个阶段：**

| 阶段                   | 负责方                          | 说明                               |
| ---------------------- | ------------------------------- | ---------------------------------- |
| 阶段一：确认同步参数   | AI 对话式收集                   | 产出 `dd-sync-cfg.json` 配置文件   |
| 阶段二：准备钉钉文件夹 | Python 脚本 (`scripts/sync.py`) | 根据配置创建/复用文件夹            |
| 阶段三：执行文档同步   | Python 脚本 (`scripts/sync.py`) | 遍历 markdown 文件，新建或更新文档 |

---

## 阶段一：确认同步参数（由 AI 执行）

### 1.1 参数澄清循环

在执行同步之前，必须逐一确认以下参数。任一环节出现问题，都应继续向用户追问，直到所有参数均确认无误，方可进入下一阶段。

> 每个问题单独提问，不要一股脑一下子对用户提问

#### Q1：本地 Markdown 源？

向用户提问，获取【本地 Markdown 源】（可以是多个目录、多个 `.md` 文件，或两者混合）。

**确认方法**：在本地文件系统中逐一检查每个路径：

- 如果是目录 → 检查是否存在，且目录下是否包含 `.md` 文件
- 如果是文件 → 检查文件是否存在，且是否为 `.md` 文件

- ✅ 所有路径均有效且包含 `.md` 内容 → 通过，记录 【本地 Markdown 源】
- ❌ 有路径不存在 → 反馈用户，追问正确路径
- ❌ 路径存在但没有 `.md` 文件 → 反馈用户，追问正确路径或确认是否需要调整范围

#### Q2：目标钉钉知识库？

向用户提问，获取【目标钉钉知识库】信息（可以是知识库名称或知识库 ID）。

> 关于知识库ID，提示用户可以访问知识库首页，从浏览器URL中获得

**确认方法**：

1. 判断用户输入的类型：
   - 如果是名称（如 `智慧校园项目实施知识库`）→ 调用 `dws wiki list`，在返回的知识库列表中按名称匹配，找到对应的知识库 ID
   - 如果是 ID（如 `YOUR_WORKSPACE_ID`，可从知识库首页的URL上获得）→ 直接使用该 ID，调用 `dws wiki list` 验证其有效性，获得知识库的名字
2. 内部统一使用知识库 ID 进行后续操作

- ✅ 找到/验证通过 → 通过，记录 `KNOWLEDGE_BASE`（名称）和 `KNOWLEDGE_BASE_ID`（ID）
- ❌ 按名称未找到 → 反馈用户 `dws wiki list` 返回的知识库列表，追问正确的知识库
- ❌ ID 无效 → 反馈用户，追问正确的知识库名称或 ID

#### Q3：目标钉钉知识库文件夹？

> 关于钉钉文件夹ID，提示用户打开文件夹，从浏览器URL中获得

向用户提问，获取 【目标钉钉知识库文件夹】信息（可以是文件夹名字或文件夹 node_id）

**确认方法**：

- 用户给的是文件夹名字时，使用 `dws doc search --query '<文件夹名字>' --workspace-ids <KNOWLEDGE_BASE_ID>` ，在目标知识库中查找该文件夹是否存在。
- 用户给的是文件夹ID是，使用 `dws skill`，在目标知识库中查找该文件夹是否存在。

- ✅ 找到/验证通过 → 通过，记录 【目标钉钉知识库文件夹】（名称）、 `ROOT_FOLDER_NODE_ID`（node_id）、`ROOT_FOLDER_DOC_URL`（doc_url）
- ❌ 文件夹不存在 → 向用户确认是否需要创建，或是否使用了错误的文件夹名

#### Q4：本地子目录 → 钉钉文件夹的映射表？

向用户确认本地目录与钉钉文件夹的映射关系：

1. 分析 【本地 Markdown 源】 中涉及的目录：
   - 「目录」既包含源路径中的目录本身（如 `docs/foo` → 目录 `foo`），也包含这些目录下的子目录
   - 示例：源路径为 `docs/foo/`，其下有 `bar/` 子目录，则映射范围为 `foo` 和 `foo/bar`
2. 询问用户：是否需要将本地目录映射为不同的钉钉文件夹名称？
   - **是** → 请用户逐一指定每个本地目录对应的钉钉文件夹名称
   - **否** → 钉钉文件夹名称直接使用本地目录名
3. 如果 【本地 Markdown 源】 中的路径均为单个 `.md` 文件（不包含任何目录），跳过此问题

确定的映射关系将记录到同步配置文件的 `folder_mapping` 数组中（此时仅填充 `local_dir` 和 `dingtalk_folder_name`，`node_id` 和 `doc_url` 由脚本在阶段二回填）。

#### Q5：钉钉文档命名方式？

向用户提问，获取【钉钉文档命名方式】。

**确认方法**：询问用户希望以什么方式命名钉钉文档：

- **文件名**（默认）→ 使用本地 markdown 文件的文件名（不含 `.md` 后缀）作为钉钉文档名称
- **H1 标题** → 使用文档正文中的第一个一级标题（`# 标题`）作为钉钉文档名称；如果文档中没有 H1 标题，则自动 fallback 到文件名

用户不回答则默认使用文件名。记录到配置文件的 `doc_name_source` 字段，取值为 `"filename"` 或 `"h1"`。

### 1.2 创建同步配置文件（JSON）

全部参数（Q1~Q5）确认完毕后，**创建同步配置文件 `dd-sync-cfg.json`**（JSON 格式），作为阶段一的最终产出。

**JSON 配置模板：**

```json
{
  "version": "1",
  "source_paths": ["docs/", "notes/api-guide.md"],
  "doc_name_source": "filename",
  "knowledge_base": {
    "name": "智慧校园项目实施知识库",
    "workspace_id": "YOUR_WORKSPACE_ID"
  },
  "root_folder": {
    "name": "项目文档",
    "node_id": "YOUR_NODE_ID",
    "doc_url": "https://...",
    "comment": "node_id/doc_url 为空表示待脚本创建"
  },
  "folder_mapping": [
    {
      "local_dir": "docs/api",
      "dingtalk_folder_name": "API文档",
      "node_id": "",
      "doc_url": "",
      "comment": "node_id/doc_url 为空表示待脚本创建"
    }
  ],
  "ignore_patterns": ["**/draft/*.md", "*.tmp.md"]
}
```

**字段说明：**

- `source_paths`：要同步的本地路径列表（目录或 `.md` 文件）
- `doc_name_source`：钉钉文档名称来源策略，可选 `"filename"`（默认，使用文件名去掉 .md 后缀）或 `"h1"`（使用文档第一个 H1 标题，无 H1 时 fallback 到文件名）。可选，默认为 `"filename"`
- `root_folder.node_id` / `root_folder.doc_url`：Q3 已知时填入，否则留空让脚本创建
- `folder_mapping[].node_id` / `folder_mapping[].doc_url`：始终留空，由脚本创建后回填
- `ignore_patterns`：glob 模式数组，匹配的 `.md` 文件将跳过不同步。可选，默认为空数组 `[]`

**每次同步时**，先检查 `dd-sync-cfg.json` 是否存在：

- 存在 → 读取已有参数，向用户确认是否沿用。用户可以选择：
  - 全部沿用 → 直接进入阶段二
  - 仅修改部分参数 → 只重新验证被修改的参数，其余保持不变
  - 全部重新配置 → 进入 1.1 参数澄清循环
  - 创建一个新的配置文件，原来的配置文件保留 → 进入 1.1 参数澄清循环
- 不存在 → 进入 1.1 参数澄清循环

---

## 阶段二 + 阶段三：执行同步脚本（Python 脚本）

阶段一的配置文件 `dd-sync-cfg.json` 就绪后，AI 执行以下命令：

```bash
cd <项目根目录>
python /path/to/skills/dd-sync/scripts/sync.py --config dd-sync-cfg.json
```

**可选参数：**

| 参数        | 说明                                                           |
| ----------- | -------------------------------------------------------------- |
| `--dry-run` | 预览模式，只打印将要执行的操作，不实际调用 dws                 |
| `--verbose` | 输出更详细的日志                                               |
| `--file`    | 只同步指定的单个 markdown 文件（相对或绝对路径），用于出错重试 |

**重试单个文件示例：**

```bash
# 某文件同步失败后，单独重试该文件
python /path/to/skills/dd-sync/scripts/sync.py --config dd-sync-cfg.json --file docs/api/auth.md

# 结合 dry-run 预览重试计划
python /path/to/skills/dd-sync/scripts/sync.py --config dd-sync-cfg.json --file docs/api/auth.md --dry-run
```

**脚本自动完成：**

1. **校验配置**：JSON 格式校验、必填字段检查
2. **阶段二 — 准备文件夹**：检查 root_folder 和 folder_mapping 中的 `node_id` 是否为空，为空则在钉钉上创建/复用文件夹。若某个映射目录在应用 `ignore_patterns` 后无待同步文件，则不创建对应文件夹。完成后回填 `node_id` 和 `doc_url` 到配置文件
3. **阶段三 — 同步文档**：
   - 遍历 `source_paths`，收集所有 `.md` 文件（排除配置文件自身）
   - 跳过正文为空的文档
   - 解析 frontmatter：有 `dingding_link` → 更新；无 → 新建
   - **大文件自动分块**：内容 > 8000 字符时自动按 H2/H3 标题边界分片上传
   - 更新失败且文档已被删除时，自动降级为新建
   - 每完成一个文档，更新 frontmatter 中的 `dingding_link` / `dingding_updated`

**输出示例：**

```
========================================
dd-sync v1  [DRY RUN]
========================================

📁 阶段二：准备钉钉文件夹
  ✅ root_folder "项目文档" — 已有 node_id，跳过
  ✅ docs/api → "API文档" (nodeId: <NODE_ID_1>)
  ✅ docs/ui → "UI设计" (nodeId: <NODE_ID_1>)
  ⏭️  docs/empty → "empty" — 目录下无待同步文件，跳过创建

📄 阶段三：同步文档 (共 5 个文件)
  [CREATE] docs/api/auth.md → "认证接口" (https://...)
  [UPDATE] docs/api/user.md → "用户接口" (已更新)
  [UPDATE] docs/ui/home.md → "首页设计" (已更新)
  [SKIP] docs/empty.md — 正文为空
  [CREATE-chunk] docs/guide.md → "开发指南" (https://..., 3 片)

========================================
结果: ✅ 4 成功   ⚠️ 0 降级   ⏭️ 1 跳过   ❌ 0 失败
========================================
```

---

## 注意事项

1. **首次全量同步**：所有文档走「新建」路径。
2. **指定同步配置文件**：用户可以指定【同步配置文件】，一个项目下也可以有多个【同步配置文件】
3. **frontmatter 保留**：编辑源文档时保留已有的 `dingding_link` / `dingding_updated` 字段，避免重复创建。
4. **跳过空文件夹**：若 `folder_mapping` 中某个目录在应用 `ignore_patterns` 后无待同步文件，脚本不为其创建钉钉文件夹。已有 `node_id` 的映射不受影响。
5. **时间格式**：使用 ISO 8601 带时区，如 `2026-06-09T14:33:59+08:00`。
6. **大文件处理**：脚本内置分块上传（>8000 字符自动触发），按 H2 → H3 → 段落边界切分，单次失败自动重试。
7. **节点 ID 缓存**：配置文件中的 `node_id` / `doc_url` 会在首次运行后持久化，后续运行复用，避免重复查找/创建。
8. **忽略模式**：通过 `ignore_patterns` 可排除不想同步的 `.md` 文件（如草稿、临时文件）。支持 glob 模式，如 `**/draft/*.md`、`*.tmp.md`。

---

## 使用例子

用户可以通过以下方式触发本 skill：

**自然语言触发：**

```
帮我把 docs/ 目录下的文档同步到钉钉知识库
把这些 markdown 文件批量推到钉钉知识库里
用配置文件 dd-sync-cfg.json 同步文档到钉钉
```

**Slash command 触发：**

```
/dd-sync 把 /path/to/dir 下的文档同步到钉钉知识库
/dd-sync 按照配置文件 /path/to/dd-sync-cfg.json，把文档同步到钉钉知识库
```

AI 会按以下三阶段执行：

**阶段一：确认同步参数（AI 对话式收集）**

1. 参数澄清循环 —— 逐一询问并验证：本地路径、知识库、目标文件夹、文件夹映射关系
2. 创建同步配置文件 —— 产出 `dd-sync-cfg.json`（JSON 格式，机器可读），下次可复用

**阶段二 & 阶段三：Python 脚本自动化**

```bash
python scripts/sync.py --config dd-sync-cfg.json
```

脚本自动完成文件夹准备、文档收集、新建/更新、大文件分块、frontmatter 回写等机械操作，节省 AI token 消耗并提高执行速度。

---
