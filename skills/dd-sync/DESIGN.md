# dd-sync 脚本设计文档

## 1. 目标

将 dd-sync 流程中的机械性工作（阶段二「准备钉钉文件夹」+ 阶段三「执行同步」）封装为 Python 脚本，由 AI 在阶段一完成后直接调用，从而：

- **节省 token**：AI 不再逐文件循环、解析 frontmatter、拼装 dws 命令、写回 frontmatter
- **加快速度**：脚本内部批量处理，不走 AI 的「思考→执行→观察」循环
- **减少出错**：确定性操作由脚本保证，消除 AI 幻觉/遗漏

## 2. 整体流程

```
用户 ──→ AI（阶段一：参数澄清）──→ 生成 dd-sync-cfg.json
                                        │
                    ┌───────────────────┘
                    ▼
              python sync.py --config dd-sync-cfg.json
                    │
                    ├── 阶段二：创建/验证钉钉文件夹，回填 node_id/doc_url
                    │
                    ├── 阶段三：遍历 .md → 判定新建/更新 → 调 dws → 写回 frontmatter
                    │
                    ▼
              终端输出同步结果报告
```

AI 只需做一件事：运行 `python scripts/sync.py --config dd-sync-cfg.json`，然后向用户转述结果。

## 3. 配置文件：`dd-sync-cfg.json`

> `dd-sync-cfg.md` 模板删除，统一用 JSON。

### 3.1 Schema

```json
{
  "version": "1",
  "source_paths": ["docs/", "notes/api-guide.md"],
  "knowledge_base": {
    "name": "智慧校园项目实施知识库",
    "workspace_id": "<WORKSPACE_ID>"
  },
  "root_folder": {
    "name": "项目文档",
    "node_id": "",
    "doc_url": ""
  },
  "folder_mapping": [
    {
      "local_dir": "docs/api",
      "dingtalk_folder_name": "API文档",
      "node_id": "",
      "doc_url": ""
    },
    {
      "local_dir": "docs/guide",
      "dingtalk_folder_name": "",
      "node_id": "",
      "doc_url": ""
    }
  ],
  "ignore_patterns": ["**/draft/*.md", "*.tmp.md"]
}
```

### 3.2 字段约定

| 字段                                    | 类型     | 谁填     | 说明                                                        |
| --------------------------------------- | -------- | -------- | ----------------------------------------------------------- |
| `version`                               | string   | AI       | 固定 `"1"`，预留升级空间                                    |
| `source_paths`                          | string[] | AI       | 项目根目录的相对路径，可混用目录和文件                      |
| `knowledge_base.name`                   | string   | AI       | 知识库名称（仅用于人类阅读）                                |
| `knowledge_base.workspace_id`           | string   | AI       | 知识库 workspace_id，必填                                   |
| `root_folder.name`                      | string   | AI       | 目标根文件夹名（仅用于人类阅读）                            |
| `root_folder.node_id`                   | string   | AI       | 根文件夹 node_id，空 = 上传到知识库根目录                   |
| `root_folder.doc_url`                   | string   | AI       | 根文件夹 doc_url                                            |
| `folder_mapping`                        | array    | AI       | 为空数组时表示无子目录映射，所有文档直放根文件夹            |
| `folder_mapping[].local_dir`            | string   | AI       | 本地子目录路径（相对于 `source_paths` 中的目录）            |
| `folder_mapping[].dingtalk_folder_name` | string   | AI       | 空字符串 = 使用 `local_dir` 的末级目录名                    |
| `folder_mapping[].node_id`              | string   | 脚本回填 |                                                             |
| `folder_mapping[].doc_url`              | string   | 脚本回填 |                                                             |
| `ignore_patterns`                       | string[] | AI       | glob 模式数组，匹配的 `.md` 文件不参与同步。可选，默认 `[]` |

## 4. 脚本结构

```
skills/dd-sync/
├── SKILL.md
├── DESIGN.md                    ← 本文件
├── scripts/
│   └── sync.py                  ← 主脚本
└── references/
    └── templates/
        └── frontmatter.md
```

### 4.1 依赖

- **Python** ≥ 3.9
- **pyyaml**：解析 markdown 文件的 YAML frontmatter
- **标准库**：`json`, `subprocess`, `argparse`, `pathlib`, `re`, `tempfile`, `datetime`

### 4.2 模块划分

```
sync.py
├── CLI 入口（argparse）
│   ├── --config     配置文件路径（必填）
│   ├── --dry-run    预览模式，不实际调用 dws
│   ├── --verbose    详细输出
│   └── --file       指定单个文件同步，跳过其余文件（用于出错重试）
│
├── Config 模块
│   ├── load_config(path) → dict
│   ├── save_config(config, path)  回填 node_id/doc_url
│   └── validate_config(config)    启动时校验必填字段
│
├── DingTalk 模块（封装 dws 命令）
│   ├── run_dws(args, dry_run=False) → dict
│   ├── list_folder(workspace_id=None, parent_node_id=None) → [{name, nodeId, nodeType, docUrl}]
│   ├── find_folder_by_name(name, workspace_id=None, parent_node_id=None) → Optional[dict]
│   ├── create_folder(name, workspace_id=None, parent_node_id=None) → Optional[dict]
│   ├── ensure_folder(name, workspace_id=None, parent_node_id=None) → Optional[dict]
│   │   └── 先 find_folder_by_name 查找同名 → 有则复用，无则 create_folder
│   ├── create_doc(name, content_file, folder_node_id, workspace_id=None) → Optional[dict]
│   ├── update_doc_overwrite(node_id_or_url, content_file) → dict
│   ├── update_doc_append(node_id, content_file) → dict
│   ├── is_doc_deleted_error(resp) → bool
│   ├── get_block_list(node_id) → list[dict]
│   │   └── dws doc block list，返回 [{blockId, blockType, text}, ...]
│   ├── insert_media(node_id, file_path, ref_block=None, where=None) → dict
│   │   └── dws doc media insert，将图片/附件插入文档
│   └── delete_block(node_id, block_id) → dict
│       └── dws doc block delete --yes
│
├── Markdown 模块
│   ├── parse_frontmatter(filepath) → (frontmatter_dict, body)
│   │   └── 文件开头 "---\n...\n---" 之间用 pyyaml 解析
│   ├── write_frontmatter(filepath, frontmatter)
│   │   └── 只更新 dingding_link / dingding_updated，保留其他字段
│   ├── get_doc_title(body, filepath) → str
│   │   └── 取第一个 "# 标题"，不存在的用文件名
│   └── collect_md_files(source_paths, base_dir, exclude_files, ignore_patterns) → [filepath]
│       └── 递归遍历目录，收集 .md，排除配置文件和 ignore_patterns 匹配的文件
│
├── Image 模块
│   ├── IMG_PATTERN = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
│   ├── discover_images(body, file_dir) → list[dict]
│   │   └── 扫描 body 中所有 ![]() 引用，区分本地路径 / 外部 URL
│   ├── strip_images(body, images) → str
│   │   └── 将 ![]() 替换为 [IMG-PLACEHOLDER-N] 占位标记
│   ├── download_image(url) → Optional[str]
│   │   └── 外部 URL → 下载到临时文件，返回本地路径；失败返回 None
│   └── insert_images(node_id, images, dry_run, verbose)
│       ├── 逐张处理：本地文件直接传 / 外部 URL 先下载
│       ├── 通过 block list 找到占位标记，用 --ref-block + --where after 定位插入
│       ├── 插入成功后 → dws doc block delete 删除占位标记
│       └── 任何失败 → 保留占位标记，并在其后插入错误信息块
│           └── 错误信息块内容：原始 ![]() 引用 + 失败原因
│
└── Sync 主流程
    ├── phase2_prepare_folders(config)
    │   ├── root_folder.node_id 为空 → 设为 workspace_id（上传到知识库根目录）
    │   ├── collect_md_files 预先收集文件（含 ignore_patterns 过滤）
    │   ├── 遍历 folder_mapping，只为有实际文件的映射创建文件夹
    │   └── save_config 回填子文件夹 node_id/doc_url
    │
    └── phase3_sync_documents(config, single_file=None)
        ├── collect_md_files
        ├── 若 single_file 指定，过滤只保留该文件（相对/绝对路径均可）
        ├── for each file:
        │   ├── parse_frontmatter
        │   ├── discover_images + strip_images → 产出 clean_body
        │   ├── 正文（clean_body）为空 → skip + warn
        │   ├── 有 dingding_link？→ update_doc → 更新 dingding_updated
        │   ├── 无 dingding_link？→ create_doc → 写入 dingding_link + dingding_updated
        │   └── 文档写入成功后 → insert_images 上传图片并清理占位标记
        └── 输出同步结果报告（含图片统计）
```

## 5. 关键实现细节

### 5.1 YAML frontmatter 解析

```python
import yaml

def parse_frontmatter(filepath: str) -> tuple[dict, str]:
    """解析 markdown 文件的 YAML frontmatter。

    Returns:
        (frontmatter_dict, body_text)

    文件必须以 "---" 开头才被视为有 frontmatter。
    解析失败时 frontmatter_dict 为空 dict，body 为全文。
    """
    with open(filepath, 'r') as f:
        content = f.read()

    if not content.startswith('---'):
        return {}, content

    # 找到第二个 "---"
    parts = content.split('---', 2)
    if len(parts) < 3:
        return {}, content

    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        fm = {}

    body = parts[2].lstrip('\n')
    return fm, body
```

### 5.2 frontmatter 写回

**原则**：只更新 `dingding_link` 和 `dingding_updated`，其他字段原样保留。

```python
def write_frontmatter(filepath: str, updates: dict):
    """更新文件的 frontmatter。

    updates 仅包含 dingding_link / dingding_updated。
    如果文件没有 frontmatter，则在文件开头创建。
    """
    with open(filepath, 'r') as f:
        content = f.read()

    if content.startswith('---'):
        # 替换已有 frontmatter 中的字段
        parts = content.split('---', 2)
        fm = yaml.safe_load(parts[1]) or {}
        fm.update(updates)
        new_fm = yaml.dump(fm, allow_unicode=True, default_flow_style=False).strip()
        new_content = f"---\n{new_fm}\n---\n{parts[2].lstrip(chr(10))}"
    else:
        # 没有 frontmatter，创建
        new_fm = yaml.dump(updates, allow_unicode=True, default_flow_style=False).strip()
        new_content = f"---\n{new_fm}\n---\n{content}"

    with open(filepath, 'w') as f:
        f.write(new_content)
```

> ⚠️ 风险：`yaml.dump` 会重新格式化整个 frontmatter 块。如果用户有其他自定义字段且格式复杂，可能产生格式变化。**解决方案**：只做原地字符串替换，不重新 dump 整个 block。如果 `dingding_link` 行已存在则替换该行，否则在最后一个 frontmatter 字段后追加。

### 5.3 dws 命令映射

| 操作                   | dws 命令                                                                                                                        | 关键返回值                                    |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| 列出知识库根目录       | `dws doc list --workspace <WS_ID> --format json`                                                                                | `nodes[].{name, nodeId, nodeType, docUrl}`    |
| 列出文件夹内容         | `dws doc list --folder <node_id> --format json`                                                                                 | `nodes[].{name, nodeId, nodeType, docUrl}`    |
| 创建文件夹（知识库根） | `dws doc folder create --name "x" --workspace <WS_ID> --format json`                                                            | `{nodeId, docUrl, name, folderId}`            |
| 创建文件夹（子目录）   | `dws doc folder create --name "x" --folder <parent_node_id> --format json`                                                      | `{nodeId, docUrl, name, folderId}`            |
| 创建文档               | `dws doc create --name "x" --content-file /tmp/x.md --folder <node_id> --format json`                                           | `{nodeId, docUrl, name}`                      |
| 更新文档               | `dws doc update --node <doc_id_or_url> --content-file /tmp/x.md --mode overwrite --content-format markdown --format json --yes` | `{success, nodeId, mode}`                     |
| 获取 block 列表        | `dws doc block list --node <doc_id_or_url> --format json`                                                                       | `{blocks: [{blockId, blockType, text}, ...]}` |
| 插入图片/附件          | `dws doc media insert --node <doc_id_or_url> --file <local_path> [--ref-block <id> --where after] --format json`                | `{success, resourceId}`                       |
| 删除 block             | `dws doc block delete --node <doc_id_or_url> --block-id <block_id> --yes --format json`                                         | `{success}`                                   |

> **注意**：`--mode overwrite` 必须带 `--yes`，否则 dws 会返回错误 `--mode overwrite requires --yes`。
> **media insert 规范**：图片/附件的 `--file` 必须是本地文件路径。若图片来源是外部 URL，需先用 `download_image()` 下载到临时文件再传入。

### 5.4 文件夹准备（阶段二）

```
ensure_root_folder(config):
    脚本不负责创建 root_folder。
    if root_folder.node_id 已有 → 直接使用
    else → 无 node_id 表示上传到知识库根目录，root_folder.node_id = workspace_id
    → 不回填 root_folder

pre_check(config):
    → collect_md_files(source_paths, base_dir, exclude_files, ignore_patterns)
    → 得到所有实际需要同步的文件列表

ensure_sub_folders(config, all_files):
    for each mapping in folder_mapping:
        dingtalk_name = mapping.dingtalk_folder_name or basename(mapping.local_dir)
        if mapping.node_id 已有且有效 → skip（保留，不因当前无文件而删除）
        else:
            → 检查该 local_dir 下是否存在于 all_files 中的文件
            → 没有文件 → 输出 ⏭️ 跳过，不创建文件夹
            → 有文件：
                → find_folder_by_name 找同名文件夹
                → 找到 → 复用 nodeId, docUrl
                → 未找到 → create_folder
            → 回填 mapping.node_id, mapping.doc_url

save_config  将回填后的完整配置写回 dd-sync-cfg.json
```

### 5.5 忽略模式匹配

`ignore_patterns` 使用 Python `fnmatch.fnmatch()` 进行 glob 模式匹配，匹配对象为文件相对于 `base_dir`（配置文件所在目录）的相对路径。

**匹配示例：**

| 模式              | 匹配的文件                           |
| ----------------- | ------------------------------------ |
| `*.tmp.md`        | 根目录下所有 `.tmp.md` 文件          |
| `**/draft/*.md`   | 任意子目录 `draft/` 下的 `.md` 文件  |
| `docs/archive/**` | `docs/archive/` 及其子目录下所有文件 |
| `**/test/**`      | 任意路径中包含 `test/` 的文件        |

> 注意：`fnmatch` 的 `*` 不跨越路径分隔符，`**` 可以。模式不区分大小写（取决于操作系统）。

### 5.6 文档同步（阶段三）

```
find_target_folder(filepath, config):
    filepath 的相对目录匹配 folder_mapping[].local_dir
    → 匹配到 → 返回对应 mapping 的 node_id
    → 未匹配到 → 返回 root_folder.node_id

get_doc_title(body, filepath):
    match = 正则匹配 "^# (.+)$"（第一个一级标题）
    → 有 → 返回标题文本
    → 无 → 返回 os.path.splitext(os.path.basename(filepath))[0]

sync_one_file(filepath, config):
    fm, body = parse_frontmatter(filepath)
    if not body.strip() → skip, warn

    # ★ 图片发现与正文清理
    file_dir = os.path.dirname(os.path.abspath(filepath))
    images = discover_images(body, file_dir)
    clean_body = strip_images(body, images)

    target_node_id = find_target_folder(filepath, config)
    title = get_doc_title(body, filepath)  # 标题仍用原始 body

    # 写 clean_body 到临时文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
        f.write(clean_body)
        tmp_path = f.name

    node_id = None
    try:
        if 'dingding_link' in fm and fm['dingding_link']:
            # 更新
            resp = update_doc_overwrite(fm['dingding_link'], tmp_path)
            if resp.get('success'):
                node_id = resp.get('nodeId', fm['dingding_link'])
                fm['dingding_updated'] = now_iso8601()
            elif is_doc_deleted_error(resp):
                # 文档已删除，降级为新建
                result = create_doc(title, tmp_path, target_node_id)
                if result:
                    node_id = result['nodeId']
                    fm['dingding_link'] = result['docUrl']
                    fm['dingding_updated'] = now_iso8601()
        else:
            # 新建
            result = create_doc(title, tmp_path, target_node_id)
            if result:
                node_id = result['nodeId']
                fm['dingding_link'] = result['docUrl']
                fm['dingding_updated'] = now_iso8601()

        write_frontmatter(filepath, fm)
    finally:
        os.unlink(tmp_path)

    # ★ 文档写入完成后，上传图片
    if node_id and images:
        insert_images(node_id, images, dry_run, verbose)
```

### 5.6 大文件分块上传

> 背景：dws CLI 内置自动分片（>30000 字符触发），但遇到超大文件或网络不稳时可能失败（`CONTENT_TRUNCATED` 错误），因此脚本需自行实现分块上传保证可靠性。

#### 策略

| 内容大小     | 策略                               |
| ------------ | ---------------------------------- |
| ≤ 8,000 字符 | 单次上传，直接 `--content-file`    |
| > 8,000 字符 | 分块上传：创建空文档 → 逐片 append |

> dws API 限制单次 append 最大 **10,000 字符**，阈值设为 8,000 留 20% 余量。

#### 分块算法

按 markdown 标题边界切分，优先级从高到低：

1. **H2 标题**（`## `）—— 首选，通常是最自然的章节边界
2. **H3 标题**（`### `）—— 无 H2 时使用
3. **空行** —— 无标题边界时按段落切分
4. **硬切** —— 以上都不满足时，按字符数切分（保证不切断代码块和表格）

每块大小控制在 6,000 ~ 8,000 字符之间。

#### 创建文档时的分块流程

```
sync_one_file_large(filepath, config):
    body = 文件正文（不含 frontmatter）
    chunks = split_content(body, max_chunk_size=8000)
    title = get_doc_title(body, filepath)
    target_node_id = find_target_folder(filepath, config)

    # 第一步：创建空文档
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md') as f:
        f.write("")  # 空内容
        f.flush()
        result = create_doc(title, f.name, target_node_id)
    node_id = result['nodeId']
    doc_url = result['docUrl']

    # 第二步：逐片 append
    for i, chunk in enumerate(chunks):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md') as f:
            f.write(chunk)
            f.flush()
            result = update_doc_append(node_id, f.name)
        if not result:
            # 重试一次
            result = update_doc_append(node_id, f.name)
        if not result:
            raise SyncError(f"分块 {i+1}/{len(chunks)} 写入失败")

    return doc_url
```

#### 更新文档时的分块流程

```
sync_one_file_update_large(filepath, fm, config):
    body = 文件正文
    chunks = split_content(body, max_chunk_size=8000)
    doc_id = fm['dingding_link']

    # 第一片：overwrite 覆盖旧内容
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md') as f:
        f.write(chunks[0])
        f.flush()
        result = update_doc_overwrite(doc_id, f.name)
    if not result:
        raise SyncError("首片 overwrite 失败")

    # 后续片：append 追加
    for i, chunk in enumerate(chunks[1:], start=2):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md') as f:
            f.write(chunk)
            f.flush()
            result = update_doc_append(doc_id, f.name)
        if not result:
            result = update_doc_append(doc_id, f.name)  # 重试一次
        if not result:
            raise SyncError(f"分块 {i}/{len(chunks)} 写入失败")
```

#### 新增 DingTalk 模块函数

```
update_doc_append(node_id, content_file) → dict:
    dws doc update --node <node_id> --content-file <content_file>
                   --mode append --content-format markdown --format json --yes

update_doc_overwrite(node_id_or_url, content_file) → dict:
    dws doc update --node <node_id_or_url> --content-file <content_file>
                   --mode overwrite --content-format markdown --format json --yes
```

#### 分块函数伪代码

```python
def split_content(content: str, max_chunk_size: int = 8000) -> list[str]:
    """按 markdown 标题边界分块，保证每块不超过 max_chunk_size。"""
    chunks = []
    current = ""

    # 用 H2 标题作为首选切分点
    sections = re.split(r'(?=^## )', content, flags=re.MULTILINE)

    for section in sections:
        if len(current) + len(section) > max_chunk_size and current:
            chunks.append(current)
            current = section
        else:
            current += section

    if current:
        # 最后一块如果还是太大，用 H3 再切
        if len(current) > max_chunk_size:
            sub_sections = re.split(r'(?=^### )', current, flags=re.MULTILINE)
            sub_current = ""
            for sub in sub_sections:
                if len(sub_current) + len(sub) > max_chunk_size and sub_current:
                    chunks.append(sub_current)
                    sub_current = sub
                else:
                    sub_current += sub
            if sub_current:
                chunks.append(sub_current)
        else:
            chunks.append(current)

    return chunks
```

### 5.7 时间格式

使用 ISO 8601 带时区：`2026-06-11T10:30:00+08:00`

```python
from datetime import datetime, timezone, timedelta

def now_iso8601():
    tz = timezone(timedelta(hours=8))  # CST
    return datetime.now(tz).strftime('%Y-%m-%dT%H:%M:%S%z')
    # 格式：2026-06-11T10:30:00+0800
    # 转为标准格式需插入冒号
```

## 5.8 图片处理

> 背景：钉钉文档不支持通过 Markdown `![]()` 语法渲染图片。图片必须以本地文件方式通过 `dws doc media insert` 单独上传。本节描述脚本如何从 Markdown 中提取图片引用、上传图片、以及失败时的处理策略。

### 5.8.1 图片发现

用正则 `!\[([^\]]*)\]\(([^)]+)\)` 扫描 body，每条匹配生成一个 dict：

```python
{
    "index": 0,                          # 序号，用于生成占位标记
    "alt": "架构图",                      # 从 ![alt](...) 提取
    "raw": "![架构图](./images/arch.png)", # 原始 markdown 语法
    "abs_path": "/abs/path/to/image.png", # 绝对路径（外部 URL 时保留原 URL）
    "exists": True,                       # 本地文件是否存在
    "is_external": False,                 # 是否外部 URL
    "placeholder": "[IMG-PLACEHOLDER-0]",            # 占位标记
}
```

**路径解析规则：**

- `./images/a.png` / `../shared/logo.png` → 相对于 markdown 文件所在目录拼接绝对路径
- `https://example.com/img.png` → `is_external=True`，`exists` 不做本地校验
- 本地文件 `exists=False` → 不阻断流程，记录 warn

### 5.8.2 正文替换

`strip_images(body, images)` 将所有 `![]()` 替换为可见文本占位符 `[IMG-PLACEHOLDER-N]`。

⚠️ 最初选择 `<!--DD-SYNC-IMG-N-->` HTML 注释作为占位符，但钉钉在解析 markdown 时会**彻底剥离** HTML 注释（不创建任何 block），导致 `find_block_by_text()` 永远找不到占位标记，图片全部落到文档末尾。

改用 `[IMG-PLACEHOLDER-N]` 作为占位符：纯文本，钉钉必然保留为独立 block，可通过 `dws doc block list` 的 `text` 字段精确匹配。成功上传后用 `delete_block` 删除，即使失败留存在文档中也比 `<!-- -->` 更可读。

### 5.8.3 图片插入

`insert_images(node_id, images, dry_run, verbose)` 在文档写入完成后执行：

```
insert_images(node_id, images):
    blocks = get_block_list(node_id)    # 拿到当前所有 block

    for img in images:
        # ── 步骤 A：准备本地文件 ──
        if img.is_external:
            local_file = download_image(img.abs_path)
            if not local_file -> 进入失败处理
        else:
            if not img.exists -> 进入失败处理
            local_file = img.abs_path

        # ── 步骤 B：定位占位标记 ──
        ref_block_id = find_block_by_text(blocks, img.placeholder)

        # ── 步骤 C：插入图片 ──
        resp = insert_media(node_id, local_file, ref_block_id, where="after")

        # ── 步骤 D：删除占位标记 ──
        if resp.success and ref_block_id:
            delete_block(node_id, ref_block_id)
        else:
            -> 进入失败处理

        # 清理外部 URL 下载的临时文件
        if img.is_external -> os.unlink(local_file)
```

### 5.8.4 失败处理策略

**任何原因导致图片上传失败时（文件不存在、下载失败、media insert 失败等），执行以下操作：**

1. **保留占位标记** — 不删除 `[IMG-PLACEHOLDER-N]` block
2. **在占位标记后插入错误信息块** — 调用 `dws doc block insert` 插入一个引用块（blockquote），内容为：

   ```
   > ⚠️  图片未能同步
   > 原始引用: ![alt](原始路径)
   > 原因: <具体失败原因>
   ```

   其中原因分为：
   - `本地文件不存在: /path/to/image.png`
   - `外部 URL 下载失败: https://...`
   - `media insert 失败: <dws 返回的错误信息>`

这样读者在钉钉文档中看到原文时，能明确知道此处应有图片、图片的原始路径、以及为什么没有显示，便于人工补救。

### 5.8.5 外部 URL 下载

```python
import urllib.request

def download_image(url: str) -> Optional[str]:
    """下载外部图片到临时文件。"""
    parsed = urllib.parse.urlparse(url)
    ext = os.path.splitext(parsed.path)[1] or ".png"
    fd, tmp_path = tempfile.mkstemp(suffix=ext, prefix="ddsync_img_")
    os.close(fd)
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; dd-sync/1.0)"
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            with open(tmp_path, "wb") as f:
                f.write(resp.read())
        return tmp_path
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return None
```

### 5.8.6 分块场景兼容

大文件（>9000 字符）走分块流程时，图片处理与文字分离：

- 文字内容用 `clean_body` 分块上传（与现有逻辑一致）
- 所有 chunk 写入完成后，统一调用 `insert_images()`
- 图片插入不受分块影响，因为定位靠的是占位标记的 blockId，而非 chunk 边界

---

## 6. 错误处理

| 场景                             | 处理策略                                                               |
| -------------------------------- | ---------------------------------------------------------------------- |
| dws 命令未安装/未登录            | 启动时 `dws --version` 检测，失败则提示安装/登录                       |
| 配置文件 JSON 格式错误           | 启动时校验，输出具体错误行                                             |
| 配置文件必填字段缺失             | 启动时校验，列出缺失字段                                               |
| `source_paths` 中路径不存在      | 跳过并 warn，继续处理其他路径                                          |
| 单个文档创建失败（网络/权限）    | 记录错误，继续处理下一个文档                                           |
| 分块上传某一片失败               | 重试一次，仍失败则记录错误并跳过该文档                                 |
| 更新失败且原因是「文档已删除」   | `error.message == "workspace node has been recycled"` 时降级为「新建」 |
| 更新失败且原因是其他错误         | 记录错误，跳过该文档                                                   |
| 正文为空的文档                   | 跳过并 warn                                                            |
| frontmatter YAML 解析失败        | 视为无 frontmatter，走「新建」路径                                     |
| 图片文件不存在 / 外部URL下载失败 | 记录 warn，保留占位标记并插入错误信息块                                |
| 图片 media insert 失败           | 记录 warn，保留占位标记并插入错误信息块（附 dws 返回的错误原因）       |
| 占位标记 block delete 失败       | 记录 warn（图片已成功上传，仅标记清理失败），不阻塞流程                |

## 7. 输出格式

### 7.1 正常输出

```
========================================
dd-sync v1
========================================

📁 阶段二：准备钉钉文件夹
  ✅ root_folder "项目文档" — 已存在，复用 (node_id: abc123)
  ✅ docs/api → "API文档" — 已存在，复用 (node_id: def456)
  ✅ docs/guide → "guide" — 新建 (node_id: ghi789)
  ⏭️  docs/empty-dir → "empty" — 目录下无待同步文件，跳过创建

📄 阶段三：同步文档 (共 5 个文件)
  [CREATE] docs/api/auth.md → "认证接口" (https://alidocs.dingtalk.com/...)
  [UPDATE] docs/api/user.md → "用户接口" (已更新, 2026-06-11T10:30:00+08:00)
  [CREATE] docs/guide/start.md → "快速开始" (https://alidocs.dingtalk.com/...)
    [IMG] docs/images/start-guide.png → ✅
  [SKIP]   docs/guide/empty.md — 正文为空
  [CREATE] notes/readme.md → "说明" (https://alidocs.dingtalk.com/...)
    [IMG] https://cdn.example.com/logo.svg → ✅

========================================
结果: ✅ 4 成功   ⚠️ 0 降级   ⏭️ 1 跳过   ❌ 0 失败
图片: ✅ 2 插入   ⚠️ 0 失败
========================================
```

### 7.2 错误输出

```
========================================
dd-sync v1
========================================

📁 阶段二：准备钉钉文件夹
  ...

📄 阶段三：同步文档 (共 5 个文件)
  [CREATE] docs/api/auth.md → ✅ (https://...)
  [CREATE] docs/api/broken.md → ❌ 创建失败: 权限不足
  [UPDATE] docs/guide/start.md → ✅ 已更新
  [UPDATE] docs/guide/deleted.md → ⚠️ 文档已删除，降级为新建 → ✅ (https://...)
  [CREATE] docs/readme.md → "说明" → ✅
    ⚠️  docs/images/missing.png — 本地文件不存在，占位标记已保留
    [IMG] https://example.com/404.png → ⚠️ 下载失败: HTTP 404

========================================
结果: ✅ 3 成功   ⚠️ 1 降级   ❌ 1 失败
失败详情:
  - docs/api/broken.md: 创建失败: 权限不足
图片: ✅ 1 插入   ⚠️ 2 失败
========================================
```

## 8. dry-run 模式

`--dry-run` 下不实际调用 `dws` 命令，仅输出预览。可与 `--file` 组合使用，预览单个文件的同步计划：

```
========================================
dd-sync v1  [DRY RUN]
========================================

📁 阶段二：准备钉钉文件夹（模拟）
  ✅ root_folder "项目文档" — 将查找或创建
  ✅ docs/api → "API文档" — 将查找或创建

📄 阶段三：同步文档（模拟）
  [CREATE] docs/api/auth.md → "认证接口" → 目标文件夹: API文档
  [UPDATE] docs/api/user.md → "用户接口" → 已有 dingding_link

========================================
以上为预览，未执行实际操作。
去掉 --dry-run 参数以执行同步。
========================================
```

## 9. SKILL.md 改动要点

1. 去掉对 `dd-sync-cfg.md` 的所有引用
2. 阶段一产出改为 `dd-sync-cfg.json`
3. 阶段二和阶段三合并为：「运行脚本 `python scripts/sync.py --config dd-sync-cfg.json`」
4. 保留注意事项和 frontmatter 说明（供 AI 理解流程）

---

## 附录 A：dws 命令实测响应

> 测试环境：dws v1.0.35，测试知识库 `<WORKSPACE_ID>`。
> 以下为各命令的原始 JSON 返回，供实现时参考字段名和数据结构。

### A.1 `dws doc list --workspace`（列出知识库根目录）

```bash
dws doc list --workspace <WORKSPACE_ID> --limit 5 --format json
```

```json
{
  "hasMore": false,
  "nodes": [
    {
      "contentType": null,
      "createTime": 1781155342000,
      "docUrl": "https://alidocs.dingtalk.com/i/nodes/<NODE_ID>?utm_scene=team_space",
      "extension": null,
      "hasChildren": true,
      "name": "_dd_sync_test_folder",
      "nodeId": "<NODE_ID>",
      "nodeType": "folder",
      "updateTime": 1781155342000,
      "workspaceId": "<WORKSPACE_ID>"
    }
  ],
  "success": true
}
```

> 关键字段：`nodes[].name`、`nodes[].nodeId`、`nodes[].nodeType`（`"folder"` / `"file"`）、`nodes[].docUrl`、`nodes[].hasChildren`

### A.2 `dws doc folder create`（创建文件夹）

```bash
dws doc folder create --name "_dd_sync_test_folder" --workspace <WORKSPACE_ID> --format json
```

```json
{
  "createTime": 1781155342000,
  "docUrl": "https://alidocs.dingtalk.com/i/nodes/<NODE_ID>?utm_scene=team_space",
  "folderId": "<FOLDER_ID>",
  "message": "Folder created successfully.",
  "name": "_dd_sync_test_folder",
  "nodeId": "<NODE_ID>",
  "success": true
}
```

> 关键字段：`nodeId`、`docUrl`、`folderId`、`name`

### A.3 `dws doc create`（创建文档）

```bash
echo "# 测试标题

## 第一章

这是测试内容。" > /tmp/_dd_sync_test.md
dws doc create --name "测试文档" --content-file /tmp/_dd_sync_test.md \
  --folder <NODE_ID> --format json
```

```json
{
  "createTime": 1781155350000,
  "docUrl": "https://alidocs.dingtalk.com/i/nodes/<DOC_NODE_ID>",
  "folderId": "<NODE_ID>",
  "message": "文档创建成功，初始内容已写入。",
  "name": "测试文档",
  "nodeId": "<DOC_NODE_ID>",
  "success": true
}
```

> 关键字段：`nodeId`、`docUrl`、`name`、`folderId`
> 注：`docUrl` 不含 `?utm_scene=team_space` 查询参数，与 `folder create` 的 `docUrl` 略有不同。

### A.4 `dws doc update`（覆盖更新文档）

```bash
echo "# 测试标题（已更新）

## 第一章

内容已更新。" > /tmp/_dd_sync_test.md
dws doc update --node <DOC_NODE_ID> \
  --content-file /tmp/_dd_sync_test.md --mode overwrite --format json --yes
```

```json
{
  "message": "文档内容已成功覆盖，所有原有内容已替换为新内容。",
  "mode": "overwrite",
  "nodeId": "<DOC_NODE_ID>",
  "success": true
}
```

> 关键字段：`success`、`nodeId`、`mode`
> ⚠️ 必须带 `--yes`，否则报错：`--mode overwrite requires --yes unless --dry-run is set`

### A.5 `dws doc list --folder`（列出文件夹内容）

```bash
dws doc list --folder <NODE_ID> --format json
```

```json
{
  "hasMore": false,
  "nodes": [
    {
      "contentType": "ALIDOC",
      "createTime": 1781155350000,
      "docUrl": "https://alidocs.dingtalk.com/i/nodes/<DOC_NODE_ID>?utm_scene=team_space",
      "extension": "adoc",
      "hasChildren": false,
      "name": "测试文档",
      "nodeId": "<DOC_NODE_ID>",
      "nodeType": "file",
      "updateTime": 1781155350000,
      "workspaceId": "<WORKSPACE_ID>"
    }
  ],
  "success": true
}
```

> 文件节点的 `nodeType` 为 `"file"`，文件夹为 `"folder"`。

### A.6 `dws doc update`（文档被删除后更新）

```bash
# 先删除文档
dws doc delete --node <DOC_NODE_ID> --yes --format json
# 再尝试更新
dws doc update --node <DOC_NODE_ID> \
  --content-file /tmp/_dd_sync_test.md --mode overwrite --format json --yes
```

```json
{
  "error": {
    "category": "api",
    "code": 1,
    "hint": "The API returned a business-level error. Check required parameters and values.",
    "message": "workspace node has been recycled",
    "operation": "tools/call",
    "reason": "business_error",
    "server_error_code": "invalidParameter.item.notFound",
    "server_key": "doc",
    "trace_id": "<TRACE_ID>"
  }
}
```

> **降级判断条件**：`"error" in response and response["error"]["message"] == "workspace node has been recycled"`

### A.7 `dws doc delete`（删除文档/文件夹）

```bash
dws doc delete --node <DOC_NODE_ID> --yes --format json
```

```json
{
  "message": "节点已成功移入回收站，30 天内可从回收站恢复。nodeId: <DOC_NODE_ID>",
  "success": true
}
```

> 删除后节点进入回收站，30 天内可恢复。脚本不需要 `delete` 命令，此处仅作参考。

### A.8 `dws doc block list`（获取文档 block 列表）

```bash
dws doc block list --node <DOC_NODE_ID> --format json
```

```json
{
  "blocks": [
    {
      "blockType": "heading",
      "element": {
        "blockType": "heading",
        "heading": {
          "level": "heading-1",
          "text": "相对路径图片测试"
        },
        "id": "mqixwp03ladj8bidhrm",
        "index": 0
      },
      "index": 0
    },
    {
      "blockType": "heading",
      "element": {
        "blockType": "heading",
        "heading": {
          "level": "heading-2",
          "text": "使用相对路径引用上层图片1"
        },
        "id": "mqixwp03sbvc8ybkmd",
        "index": 1
      },
      "index": 1
    },
    {
      "blockType": "paragraph",
      "element": {
        "blockType": "paragraph",
        "id": "mqixwp03pyemttvn5pp",
        "index": 2,
        "paragraph": {
          "text": "[IMG-PLACEHOLDER-0]"
        }
      },
      "index": 2
    },
    {
      "blockType": "heading",
      "element": {
        "blockType": "heading",
        "heading": {
          "level": "heading-2",
          "text": "使用相对路径引用上层图片2"
        },
        "id": "mqixwp03eehnidl4xyk",
        "index": 3
      },
      "index": 3
    },
    {
      "blockType": "paragraph",
      "element": {
        "blockType": "paragraph",
        "id": "mqixwp03xwwo2lx45si",
        "index": 4,
        "paragraph": {
          "text": "[IMG-PLACEHOLDER-1]"
        }
      },
      "index": 4
    },
    {
      "blockType": "paragraph",
      "element": {
        "blockType": "paragraph",
        "id": "mqixwq2ww2u7vsra2h",
        "index": 5,
        "paragraph": {
          "text": ""
        }
      },
      "index": 5
    },
    {
      "blockType": "paragraph",
      "element": {
        "blockType": "paragraph",
        "id": "mqixwqs6z8ihfho2npr",
        "index": 6,
        "paragraph": {
          "text": ""
        }
      },
      "index": 6
    }
  ],
  "hasMore": false,
  "logId": "0bb7c36217817533328126160e079a",
  "success": true,
  "totalCount": 7
}
```

> 关键字段：`blocks[].element.id`（block 唯一标识）、`blocks[].blockType`（`"p"` / `"h1"` / `"attachment"` 等）、`blocks[].element.paragraph.text`（文本内容，用于匹配占位标记）

### A.9 `dws doc media insert`（插入图片/附件到文档）

```bash
dws doc media insert --node <DOC_NODE_ID> --file /tmp/test.png --format json
```

```json
{
  "resourceId": "<RESOURCE_ID>",
  "success": true
}
```

> 关键字段：`success`、`resourceId`
> 支持 `--ref-block <BLOCK_ID> --where after|before` 精确定位插入位置

### A.10 `dws doc block delete`（删除单个 block）

```bash
dws doc block delete --node <DOC_NODE_ID> --block-id <BLOCK_ID> --yes --format json
```

```json
{
  "success": true
}
```

> 删除后 block 从文档中移除，不可恢复。用于清理图片占位标记。

## 测试

集成测试套件位于 `tests/test_sync.py`，分为两组，各使用独立的 `dd-sync-cfg.json`：

**Group A：不上传根文件夹**（`root_folder.name` 为空，`folder_mapping: []`，文档直接上传到知识库根目录）

每组先以空配置运行（触发创建），再以预填配置运行（触发更新/复用）。

| 测试         | 说明                                          |
| ------------ | --------------------------------------------- |
| Dry-run 预览 | 不实际调用 dws，验证操作计划                  |
| 首次同步     | 新建文档到知识库根目录，验证 frontmatter 写入 |
| 空文档跳过   | 空 `.md` 文件正确跳过                         |
| 文档更新     | 修改后重新同步走 [UPDATE] 路径                |
| 文件夹复用   | 无 root_folder，验证直接使用 workspace 根目录 |
| 回归验证     | 全部文件无失败                                |

**Group B：上传根文件夹**（`root_folder.name` 有值，`folder_mapping` 含子目录，文档上传到指定文件夹下）

| 测试           | 说明                                              |
| -------------- | ------------------------------------------------- |
| Dry-run 预览   | 不实际调用 dws，验证子目录映射和操作计划          |
| 首次同步       | 新建文档到子目录，验证大文件分块创建、config 回填 |
| 空文档跳过     | 空 `.md` 文件正确跳过                             |
| 大文件分块创建 | >8,000 字符文件验证分片上传结果                   |
| 文档更新       | 修改后重新同步走 [UPDATE] 路径                    |
| 文件夹复用     | node_id 已缓存时跳过创建                          |
| 大文件分块更新 | 分块文件修改后重新覆盖                            |
| 回归验证       | 全部文件无失败                                    |

```bash
# 使用默认知识库运行
python tests/test_sync.py

# 指定知识库
WORKSPACE_ID=YOUR_ID python tests/test_sync.py

# 测试后保留数据（手动检查）
python tests/test_sync.py --keep
```
