# dd-sync 集成测试

> 使用钉钉知识库运行端到端测试，验证 `sync.py` 的新建、更新、分块、图片处理等功能。

## 前置条件

1. **Python ≥ 3.9** + **pytest** + **pyyaml**

   ```bash
   pip install pytest pyyaml
   ```

2. **dws CLI** 已安装并登录

   ```bash
   dws --version          # 确认已安装
   dws auth login         # 如未登录
   ```

3. **钉钉知识库** — 准备一个用于测试的知识库，记下其 `WORKSPACE_ID`（从知识库页面 URL 中获取）

## 快速开始

```bash
# 在项目根目录下运行
cd skills/dd-sync

# 运行全部测试
WORKSPACE_ID=<YOUR_ID> pytest tests/test_sync_new.py -v

# 测试后保留钉钉上的数据（用于手动检查）
KEEP=1 WORKSPACE_ID=<YOUR_ID> pytest tests/test_sync_new.py -v
```

## 选择性运行

得益于 pytest 框架，可以灵活选择要运行的测试：

```bash
# 只运行 Group A（无 root_folder，上传到知识库根目录）
WORKSPACE_ID=<YOUR_ID> pytest tests/test_sync_new.py::TestGroupA -v

# 只运行 Group B（有 root_folder，上传到指定文件夹）
WORKSPACE_ID=<YOUR_ID> pytest tests/test_sync_new.py::TestGroupB -v

# 只运行单个测试方法
WORKSPACE_ID=<YOUR_ID> pytest tests/test_sync_new.py::TestGroupA::test_01_dry_run -v

# 按名称关键字筛选
WORKSPACE_ID=<YOUR_ID> pytest tests/test_sync_new.py -k "dry_run" -v
WORKSPACE_ID=<YOUR_ID> pytest tests/test_sync_new.py -k "update" -v
WORKSPACE_ID=<YOUR_ID> pytest tests/test_sync_new.py -k "image" -v
WORKSPACE_ID=<YOUR_ID> pytest tests/test_sync_new.py -k "chunked" -v
```

## VS Code 调试

项目已配好 `.vscode/launch.json`，在 VS Code 中按 `F5` 选择对应的启动配置：

| 配置                          | 说明                   |
| ----------------------------- | ---------------------- |
| dd-sync: 全部测试             | 运行全部 22 个测试     |
| dd-sync: Group A              | 仅 TestGroupA（10 个） |
| dd-sync: Group B              | 仅 TestGroupB（12 个） |
| dd-sync: 当前文件中的测试     | 运行当前打开的文件     |
| dd-sync: 按名称筛选 - dry_run | `-k dry_run`           |
| dd-sync: 按名称筛选 - update  | `-k update`            |
| dd-sync: 按名称筛选 - image   | `-k image`             |
| dd-sync: 保留数据运行         | `KEEP=1` 不清理        |

启动时会弹出输入框，填入 `WORKSPACE_ID` 即可。

## 测试结构

```
tests/
├── fixtures/
│   ├── config-a.json          # Group A 配置模板（root_folder.name 为空）
│   ├── config-b.json          # Group B 配置模板（root_folder.name 有值）
│   └── docs/                  # 共享的 markdown 测试文件
│       ├── quickstart.md      # 基础文档
│       ├── empty.md           # 空文档（验证跳过逻辑）
│       ├── config.md          # 带 frontmatter 的文档
│       ├── image_doc.md       # 图片测试文档
│       ├── home-1-1.png       # 测试图片
│       ├── home-1-2.png       # 测试图片
│       ├── api/
│       │   └── 用户接口.md     # 中文文档（无 frontmatter）
│       └── guide/
│           └── 功能手册.md     # 大文件（>9000 字符，触发分块）
├── test_sync.py               # 原版测试脚本（未使用 pytest）
└── test_sync_new.py           # pytest 版本
```

## 测试分组

### TestGroupA（10 个测试）— 无 root_folder

文档直接上传到知识库根目录。

| #   | 测试方法                    | 说明                      |
| --- | --------------------------- | ------------------------- |
| 1   | `test_01_dry_run`           | 预览模式，验证操作计划    |
| 2   | `test_02_image_dry_run`     | 图片处理的 dry-run 预览   |
| 3   | `test_03_create_new_docs`   | 首次同步，新建所有文档    |
| 4   | `test_04_skip_empty`        | 空文档正确跳过            |
| 5   | `test_05_image_sync_result` | 图片文档 frontmatter 验证 |
| 6   | `test_07_update_docs`       | 修改后走 [UPDATE] 路径    |
| 7   | `test_08_image_update`      | 图片文档更新验证          |
| 8   | `test_09_folders_reused`    | 文件夹缓存复用            |
| 9   | `test_10_no_failures`       | 回归验证 0 失败           |

### TestGroupB（12 个测试）— 有 root_folder

文档上传到知识库下指定文件夹。

| #   | 测试方法                    | 说明                          |
| --- | --------------------------- | ----------------------------- |
| 1   | `test_01_dry_run`           | 预览模式                      |
| 2   | `test_02_image_dry_run`     | 图片 dry-run 预览             |
| 3   | `test_03_create_new_docs`   | 首次同步（含大文件分块创建）  |
| 4   | `test_04_skip_empty`        | 空文档跳过                    |
| 5   | `test_05_chunked_create`    | 大文件分块创建结果验证        |
| 6   | `test_06_image_sync_result` | 图片文档验证                  |
| 7   | `test_08_update_docs`       | 文档更新 [UPDATE]             |
| 8   | `test_09_image_update`      | 图片文档更新                  |
| 9   | `test_10_folders_reused`    | 文件夹复用                    |
| 10  | `test_11_chunked_update`    | 大文件分块更新 [UPDATE-chunk] |
| 11  | `test_12_no_failures`       | 回归验证                      |

## 环境变量

| 变量           | 必填 | 说明                                              |
| -------------- | ---- | ------------------------------------------------- |
| `WORKSPACE_ID` | 是   | 钉钉知识库 ID                                     |
| `KEEP`         | 否   | 设为 `1` 时测试后保留钉钉上的数据（默认自动清理） |
