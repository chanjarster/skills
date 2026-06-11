#!/usr/bin/env python3
"""
dd-sync 集成测试套件

使用钉钉知识库运行端到端测试。通过环境变量 WORKSPACE_ID 指定目标知识库。

测试分为两组，各使用独立的 dd-sync-cfg.json：
  - Group A: root_folder.name 为空 → 文档直接上传到知识库根目录
  - Group B: root_folder.name 有值 → 文档上传到知识库下的指定文件夹

Usage:
    WORKSPACE_ID=<YOUR_ID> python tests/test_sync.py  # 指定知识库（必填）
    python tests/test_sync.py --keep             # 测试后不清理（用于手动检查）
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import shutil
import argparse
from pathlib import Path

# ─────────────────────────────────────────
# 配置
# ─────────────────────────────────────────

WORKSPACE_ID = os.environ.get("WORKSPACE_ID", "")
SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "sync.py"

TEST_ROOT_FOLDER_A = f"_dd_sync_test_A_{int(time.time())}"  # 实际不用（Group A 无 root_folder），仅用于文件目录名
TEST_ROOT_FOLDER_B = f"_dd_sync_test_B_{int(time.time())}"

# ─────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────


def run_sync(config_path: str, *extra_args: str) -> subprocess.CompletedProcess:
    """运行 sync.py，返回 subprocess 结果。"""
    cmd = [sys.executable, str(SCRIPT_PATH), "--config", config_path, *extra_args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180)


def run_dws(*args: str) -> dict:
    """运行 dws 命令并返回 JSON。"""
    cmd = ["dws", *args, "--format", "json"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    output = r.stdout.strip()
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        pass
    start = output.find("{")
    end = output.rfind("}")
    if start >= 0 and end >= 0:
        try:
            return json.loads(output[start:end + 1])
        except json.JSONDecodeError:
            pass
    return {"_raw": output[:200], "_stderr": r.stderr.strip()[:200]}


def assert_in(text: str, output: str, label: str):
    if text not in output:
        print(f"\n❌ FAIL [{label}]: 期望包含 '{text}'")
        print(f"   实际输出(尾):\n{output[-1500:]}")
        sys.exit(1)
    print(f"  ✅ {label}")


def assert_not_in(text: str, output: str, label: str):
    if text in output:
        print(f"\n❌ FAIL [{label}]: 不应包含 '{text}'")
        sys.exit(1)
    print(f"  ✅ {label}")


def assert_file_contains(filepath: str, text: str, label: str):
    with open(filepath, "r") as f:
        content = f.read()
    if text not in content:
        print(f"\n❌ FAIL [{label}]: 文件 {filepath} 不包含 '{text}'")
        sys.exit(1)
    print(f"  ✅ {label}")


def assert_file_not_contains(filepath: str, text: str, label: str):
    with open(filepath, "r") as f:
        content = f.read()
    if text in content:
        print(f"\n❌ FAIL [{label}]: 文件 {filepath} 不应包含 '{text}'")
        sys.exit(1)
    print(f"  ✅ {label}")


def assert_eq(expected, actual, label: str):
    if expected != actual:
        print(f"\n❌ FAIL [{label}]: 期望 {expected!r}, 实际 {actual!r}")
        sys.exit(1)
    print(f"  ✅ {label}")


def assert_single_frontmatter(filepath: str, label: str):
    """断言文件有且仅有一个 YAML frontmatter 块，无重复。"""
    with open(filepath, "r") as f:
        content = f.read()

    if not content.startswith("---"):
        print(f"\n❌ FAIL [{label}]: 文件 {filepath} 没有 frontmatter")
        sys.exit(1)

    # 找第二个 ---（结束第一个 frontmatter）
    parts = content.split("---", 2)
    if len(parts) < 3:
        print(f"\n❌ FAIL [{label}]: 文件 {filepath} frontmatter 不完整")
        sys.exit(1)

    # 第一个 frontmatter 结束后的内容
    after_fm = parts[2]

    # 检查剩余内容中是否还有以 --- 开头的新 frontmatter 块
    if after_fm.lstrip().startswith("---"):
        print(f"\n❌ FAIL [{label}]: 文件 {filepath} 存在重复 frontmatter")
        sys.exit(1)

    # 更严格：dingding_link 不应出现多次
    count = content.count("dingding_link:")
    if count > 1:
        print(f"\n❌ FAIL [{label}]: 文件 {filepath} 有 {count} 个 dingding_link（重复 frontmatter）")
        sys.exit(1)

    print(f"  ✅ {label}")


# ─────────────────────────────────────────
# 测试 setup
# ─────────────────────────────────────────

# 原始文件备份（Group B 在两轮测试间重置用）
_FILE_ORIGINALS: dict[str, str] = {}


def _write_file(path: str, content: str):
    """写入文件并备份原始内容。"""
    _FILE_ORIGINALS[path] = content
    with open(path, "w") as f:
        f.write(content)


def _build_large_doc() -> str:
    """生成一个大文件，用于分块同步测试（> 8000 字符）。"""
    sections = []
    for i in range(1, 26):
        sections.append(
            f"## 第{i}章：功能模块 {i}\n\n"
            f"本章介绍第 {i} 个功能模块的设计和使用方法。\n\n"
            f"### {i}.1 概述\n\n"
            f"功能模块 {i} 负责处理与第 {i} 类业务相关的操作。"
            f"该模块采用微服务架构，支持水平扩展和高可用部署。\n\n"
            f"### {i}.2 接口设计\n\n"
            f"| 方法 | 路径 | 说明 |\n"
            f"|------|------|------|\n"
            f"| GET | /api/v{i}/items | 获取第{i}类项目列表 |\n"
            f"| POST | /api/v{i}/items | 创建第{i}类项目 |\n"
            f"| PUT | /api/v{i}/items/{{id}} | 更新第{i}类项目 |\n"
            f"| DELETE | /api/v{i}/items/{{id}} | 删除第{i}类项目 |\n\n"
            f"### {i}.3 实现代码\n\n"
            f"```python\n"
            f"class Module{i}Handler:\n"
            f"    def __init__(self, config):\n"
            f"        self.config = config\n"
            f"        self.cache = CacheManager(f'module_{i}')\n"
            f"    async def process(self, request):\n"
            f"        result = await self.execute(request)\n"
            f"        return Response(data=result)\n"
            f"```\n\n"
        )
    return "# 系统功能模块参考手册\n\n" + "".join(sections)


def _create_basic_files(parent: str):
    """创建 quickstart.md, empty.md, config.md 三个基础测试文件。"""
    _write_file(os.path.join(parent, "quickstart.md"),
        "# 快速开始指南\n\n"
        "## 环境准备\n\n"
        "安装 Node.js 18+ 和 pnpm。\n\n"
        "```bash\nnpm install -g pnpm\n```\n\n"
        "## 项目启动\n\n"
        "```bash\npnpm install\npnpm dev\n```\n"
    )
    _write_file(os.path.join(parent, "empty.md"), "")
    _write_file(os.path.join(parent, "config.md"),
        "---\n"
        "title: 配置说明\n"
        "author: admin\n"
        "---\n\n"
        "# 配置说明\n\n"
        "复制 `.env.example` 为 `.env`。\n"
    )


def setup_test_dir(tmpdir: str) -> dict:
    """创建测试目录结构和所有文档文件。返回 ctx dict。"""
    global _FILE_ORIGINALS
    _FILE_ORIGINALS = {}

    # ── Group A: 无 root_folder，无 folder_mapping ──
    flat_dir = os.path.join(tmpdir, "docs", "flat")
    flat_api_dir = os.path.join(flat_dir, "api")
    flat_guide_dir = os.path.join(flat_dir, "guide")
    os.makedirs(flat_dir)
    os.makedirs(flat_api_dir)
    os.makedirs(flat_guide_dir)
    _create_basic_files(flat_dir)

    # Group A 中文文档（无 frontmatter）
    _write_file(os.path.join(flat_api_dir, "用户接口.md"),
        "# 用户接口\n\n"
        "## 注册接口\n\n"
        "**请求地址：** `POST /api/user/register`\n\n"
        "| 参数 | 类型 | 必填 | 说明 |\n"
        "|------|------|------|------|\n"
        "| username | string | 是 | 用户名 |\n"
        "| password | string | 是 | 密码 |\n"
    )

    # Group A 大文件（用于测试无 folder_mapping 时分块创建）
    large_content = _build_large_doc()
    _write_file(os.path.join(flat_guide_dir, "功能手册.md"), large_content)

    # ── Group B: 有 root_folder，有 folder_mapping ──
    nested_dir = os.path.join(tmpdir, "docs", "nested")
    api_dir = os.path.join(nested_dir, "api")
    guide_dir = os.path.join(nested_dir, "guide")
    os.makedirs(api_dir)
    os.makedirs(guide_dir)
    _create_basic_files(nested_dir)

    # 中文文档（无 frontmatter）
    _write_file(os.path.join(api_dir, "用户接口.md"),
        "# 用户接口\n\n"
        "## 注册接口\n\n"
        "**请求地址：** `POST /api/user/register`\n\n"
        "| 参数 | 类型 | 必填 | 说明 |\n"
        "|------|------|------|------|\n"
        "| username | string | 是 | 用户名 |\n"
        "| password | string | 是 | 密码 |\n"
    )

    # 大文件（用于分块测试，需 > 8000 字符）
    large_content = _build_large_doc()
    _write_file(os.path.join(guide_dir, "功能手册.md"), large_content)

    return {
        "tmpdir": tmpdir,
        "flat_dir": flat_dir,
        "flat_api_dir": flat_api_dir,
        "flat_guide_dir": flat_guide_dir,
        "nested_dir": nested_dir,
        "api_dir": api_dir,
        "guide_dir": guide_dir,
    }


def make_config(ctx: dict, group: str, node_ids: dict | None = None) -> str:
    """创建 dd-sync-cfg-{group}.json。

    group="a" → root_folder.name 为空（上传到知识库根目录），folder_mapping=[]
    group="b" → root_folder.name 有值（上传到指定文件夹），folder_mapping 含子目录
    node_ids 不为 None 时，填充对应的 node_id/doc_url（用于预填配置）。
    """
    node_ids = node_ids or {}
    suffix = "prefilled" if node_ids else "empty"

    if group == "a":
        config = {
            "version": "1",
            "source_paths": ["docs/flat/"],
            "knowledge_base": {
                "name": "测试知识库-GroupA",
                "workspace_id": WORKSPACE_ID,
            },
            "root_folder": {
                "name": "",              # ← 空：不上传根文件夹
                "node_id": node_ids.get("root_folder.node_id", ""),
                "doc_url": node_ids.get("root_folder.doc_url", ""),
                "comment": "Group A: 无 root_folder，上传到知识库根目录",
            },
            "folder_mapping": [
                {
                    "local_dir": "docs/flat/api",
                    "dingtalk_folder_name": "API文档",
                    "node_id": node_ids.get("docs_flat_api_folder.node_id", ""),
                    "doc_url": node_ids.get("docs_flat_api_folder.doc_url", ""),
                },
                {
                    "local_dir": "docs/flat/guide",
                    "dingtalk_folder_name": "开发指南",
                    "node_id": node_ids.get("docs_flat_guide_folder.node_id", ""),
                    "doc_url": node_ids.get("docs_flat_guide_folder.doc_url", ""),
                },
            ],
        }
    else:
        config = {
            "version": "1",
            "source_paths": ["docs/nested/"],
            "knowledge_base": {
                "name": "测试知识库-GroupB",
                "workspace_id": WORKSPACE_ID,
            },
            "root_folder": {
                "name": TEST_ROOT_FOLDER_B,
                "node_id": node_ids.get("root_folder.node_id", ""),
                "doc_url": node_ids.get("root_folder.doc_url", ""),
                "comment": "Group B: 有 root_folder，上传到指定文件夹",
            },
            "folder_mapping": [
                {
                    "local_dir": "docs/nested/api",
                    "dingtalk_folder_name": "API文档",
                    "node_id": node_ids.get("docs_nested_api_folder.node_id", ""),
                    "doc_url": node_ids.get("docs_nested_api_folder.doc_url", ""),
                },
                {
                    "local_dir": "docs/nested/guide",
                    "dingtalk_folder_name": "开发指南",
                    "node_id": node_ids.get("docs_nested_guide_folder.node_id", ""),
                    "doc_url": node_ids.get("docs_nested_guide_folder.doc_url", ""),
                },
            ],
        }

    config_path = os.path.join(ctx["tmpdir"], f"dd-sync-cfg-{group}-{suffix}.json")
    with open(config_path, "w") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    return config_path


def extract_node_ids(config_path: str) -> dict:
    """从已回填的配置文件中提取所有 node_id/doc_url。"""
    with open(config_path) as f:
        cfg = json.load(f)

    ids = {
        "root_folder.node_id": cfg["root_folder"]["node_id"],
        "root_folder.doc_url": cfg["root_folder"]["doc_url"],
    }
    for m in cfg.get("folder_mapping", []):
        key_prefix = m["local_dir"].replace("/", "_").replace(".", "_") + "_folder"
        ids[f"{key_prefix}.node_id"] = m["node_id"]
        ids[f"{key_prefix}.doc_url"] = m["doc_url"]

    return ids


# ─────────────────────────────────────────
# 通用测试用例（两组共用，仅 config 不同）
# ─────────────────────────────────────────


def test_dry_run(ctx: dict, config_path: str, group_label: str):
    """测试 dry-run 模式。"""
    print(f"\n── [{group_label}] 测试: Dry-run 模式 ──")
    r = run_sync(config_path, "--dry-run")
    output = r.stdout

    assert_in("[DRY RUN]", output, "dry-run 标记")
    assert_in("阶段二", output, "阶段二标题")
    assert_in("阶段三", output, "阶段三标题")
    assert_in("[CREATE]", output, "新建操作")

    # dry-run 不应修改任何文件
    docs_dir = ctx["flat_dir"] if group_label.startswith("Group A") else ctx["nested_dir"]
    assert_file_not_contains(os.path.join(docs_dir, "quickstart.md"),
                             "dingding_link", "dry-run 不写 frontmatter")


def test_create_new_docs(ctx: dict, config_path: str, group_label: str):
    """测试首次同步：新建文档。"""
    print(f"\n── [{group_label}] 测试: 首次同步（新建文档）──")
    r = run_sync(config_path)
    output = r.stdout

    assert_in("[CREATE]", output, "新建操作标记")
    assert_in("✅", output, "成功标记")
    assert_not_in("[UPDATE]", output, "不应有更新操作")
    assert_not_in("失败详情", output, "无失败详情区块")

    # 根据 group 决定检查哪个目录
    docs_dir = ctx["flat_dir"] if group_label.startswith("Group A") else ctx["nested_dir"]
    quickstart = os.path.join(docs_dir, "quickstart.md")
    assert_file_contains(quickstart, "dingding_link:", "quickstart.md 写入 dingding_link")
    assert_file_contains(quickstart, "dingding_updated:", "quickstart.md 写入 dingding_updated")

    config_md = os.path.join(docs_dir, "config.md")
    assert_file_contains(config_md, "title: 配置说明", "config.md 保留 title")
    assert_file_contains(config_md, "author: admin", "config.md 保留 author")
    assert_file_contains(config_md, "dingding_link:", "config.md 新增 dingding_link")
    assert_file_contains(config_md, "dingding_updated:", "config.md 新增 dingding_updated")

    # 子目录下的文档也应有 frontmatter
    if group_label.startswith("Group A"):
        api_dir = ctx["flat_api_dir"]
        guide_dir = ctx["flat_guide_dir"]
    else:
        api_dir = ctx["api_dir"]
        guide_dir = ctx["guide_dir"]

    user_api = os.path.join(api_dir, "用户接口.md")
    assert_file_contains(user_api, "dingding_link:", "用户接口.md 写入 dingding_link")
    assert_file_contains(user_api, "dingding_updated:", "用户接口.md 写入 dingding_updated")
    assert_single_frontmatter(user_api, "用户接口.md 无重复 frontmatter")

    manual = os.path.join(guide_dir, "功能手册.md")
    assert_file_contains(manual, "dingding_link:", "功能手册.md 有 dingding_link")
    assert_file_contains(manual, "dingding_updated:", "功能手册.md 有 dingding_updated")
    assert_single_frontmatter(manual, "功能手册.md 新建后无重复 frontmatter")

    # 新建后 frontmatter 不应重复
    assert_single_frontmatter(quickstart, "quickstart.md 无重复 frontmatter")
    assert_single_frontmatter(config_md, "config.md 无重复 frontmatter")

    # 检查配置文件回填
    with open(config_path) as f:
        cfg = json.load(f)
    if group_label.startswith("Group A"):
        # Group A: root_folder.name 为空，但 node_id 应被回填为 workspace_id
        assert_eq(True, bool(cfg["root_folder"]["node_id"]), "root_folder.node_id 已回填 (A)")
    else:
        assert_eq(True, bool(cfg["root_folder"]["node_id"]), "root_folder.node_id 已回填 (B)")


def test_skip_empty(ctx: dict, config_path: str, group_label: str):
    """测试空文档跳过。"""
    print(f"\n── [{group_label}] 测试: 空文档跳过 ──")
    r = run_sync(config_path, "--verbose")
    output = r.stdout

    assert_in("[SKIP]", output, "跳过详情标记")
    assert_in("empty.md", output, "empty.md 出现在跳过信息中")
    assert_in("正文为空", output, "跳过原因")

    # 空文档不应被写入 frontmatter
    docs_dir = ctx["flat_dir"] if group_label.startswith("Group A") else ctx["nested_dir"]
    assert_file_not_contains(os.path.join(docs_dir, "empty.md"),
                             "dingding_link", "empty.md 不应有 dingding_link")


def test_update_docs(ctx: dict, config_path: str, group_label: str):
    """测试修改后更新文档。"""
    print(f"\n── [{group_label}] 测试: 更新文档 ──")

    docs_dir = ctx["flat_dir"] if group_label.startswith("Group A") else ctx["nested_dir"]
    quickstart = os.path.join(docs_dir, "quickstart.md")
    with open(quickstart, "r") as f:
        content = f.read()
    content = content.replace("# 快速开始指南", "# 快速开始指南 (v2.0)")
    content += "\n## 新增章节\n\n这是新内容。\n"
    with open(quickstart, "w") as f:
        f.write(content)

    r = run_sync(config_path)
    output = r.stdout

    assert_in("[UPDATE]", output, "更新操作标记")
    assert_not_in("失败详情", output, "不应有失败详情")
    assert_file_contains(quickstart, "dingding_updated", "quickstart.md 更新了 dingding_updated")
    # 更新后不应出现重复 frontmatter
    assert_single_frontmatter(quickstart, "更新后 quickstart.md 无重复 frontmatter")


def test_folders_reused(ctx: dict, config_path: str, group_label: str):
    """测试文件夹复用。"""
    print(f"\n── [{group_label}] 测试: 文件夹复用 ──")
    r = run_sync(config_path, "--verbose")
    output = r.stdout

    if group_label.startswith("Group A"):
        assert_in("已有 node_id，跳过", output, "Group A 文件夹复用信息")
    else:
        assert_in("已有 node_id，跳过", output, "Group B 文件夹复用信息")


def test_no_failures(ctx: dict, config_path: str, group_label: str):
    """回归测试：全部成功无失败。"""
    print(f"\n── [{group_label}] 测试: 回归验证（无失败）──")
    r = run_sync(config_path)
    output = r.stdout

    assert_not_in("失败详情", output, "无失败详情区块")
    assert_in("❌ 0 失败", output, "失败计数为 0")


# ─────────────────────────────────────────
# Group B 独有测试（大文件分块）
# ─────────────────────────────────────────


def test_chunked_create(ctx: dict, config_path: str, group_label: str):
    """（仅 Group B）大文件分块创建已在 test_create_new_docs 中完成，此处验证结果。"""
    print(f"\n── [{group_label}] 测试: 大文件分块创建验证 ──")
    manual = os.path.join(ctx["guide_dir"], "功能手册.md")
    assert_file_contains(manual, "dingding_link:", "功能手册.md 有 dingding_link")
    assert_file_contains(manual, "dingding_updated:", "功能手册.md 有 dingding_updated")
    assert_single_frontmatter(manual, "功能手册.md 分块创建后无重复 frontmatter")


def test_chunked_update(ctx: dict, config_path: str, group_label: str):
    """（仅 Group B）测试大文件分块更新。"""
    print(f"\n── [{group_label}] 测试: 大文件分块更新 ──")

    manual = os.path.join(ctx["guide_dir"], "功能手册.md")
    with open(manual, "r") as f:
        content = f.read()

    parts = content.split("---", 2)
    new_content = (
        parts[0] + "---" + parts[1] + "---\n\n"
        "> **最后更新**: 测试分块更新\n\n" + parts[2]
    )
    with open(manual, "w") as f:
        f.write(new_content)

    r = run_sync(config_path)
    output = r.stdout

    assert_in("[UPDATE-chunk]", output, "分块更新标记")
    assert_in("功能手册", output, "功能手册出现在输出中")
    assert_not_in("失败详情", output, "不应有失败详情")
    # 验证分块更新后 frontmatter 已更新
    assert_file_contains(manual, "dingding_updated", "功能手册.md 分块更新后 dingding_updated 已刷新")
    # 分块更新后不应出现重复 frontmatter
    assert_single_frontmatter(manual, "功能手册.md 分块更新后无重复 frontmatter")


# ─────────────────────────────────────────
# 清理
# ─────────────────────────────────────────


def cleanup_group_a():
    """清理 Group A：删除知识库根目录下的测试文档及文件夹。"""
    print(f"\n── 清理 Group A 测试数据 ──")
    test_names = {"quickstart", "empty", "config"}
    # 先收集需要删除的 node，避免遍历中修改列表
    nodes = run_dws("doc", "list", "--workspace", WORKSPACE_ID)
    to_delete = []
    for n in nodes.get("nodes", []):
        name = n.get("name", "")
        if name in test_names or name.startswith("快速开始指南") or name.startswith("配置说明") \
           or name.startswith("用户接口") or name.startswith("系统功能模块参考手册"):
            to_delete.append((n["nodeId"], name))
    # 查找 api/ 和 guide/ 文件夹节点
    for n in nodes.get("nodes", []):
        name = n.get("name", "")
        if name in ("API文档", "开发指南"):
            to_delete.append((n["nodeId"], name))

    deleted = 0
    for node_id, name in to_delete:
        run_dws("doc", "delete", "--node", node_id, "--yes")
        deleted += 1
        print(f"  🗑️  已删除: {name}")
    print(f"  🗑️  共删除 {deleted} 个 Group A 文档")


def cleanup_group_b():
    """清理 Group B：删除 root_folder。"""
    print(f"\n── 清理 Group B 测试数据 ──")
    nodes = run_dws("doc", "list", "--workspace", WORKSPACE_ID)
    for n in nodes.get("nodes", []):
        if n.get("name") == TEST_ROOT_FOLDER_B:
            run_dws("doc", "delete", "--node", n["nodeId"], "--yes")
            print(f"  🗑️  已删除: {TEST_ROOT_FOLDER_B}")
            break


def cleanup_all(ctx: dict):
    """清理所有测试数据。"""
    cleanup_group_a()
    cleanup_group_b()
    shutil.rmtree(ctx["tmpdir"])
    print(f"  🗑️  已删除本地临时文件: {ctx['tmpdir']}")


# ─────────────────────────────────────────
# 运行一组测试的流程
# ─────────────────────────────────────────


def run_test_group(ctx: dict, group: str, label: str,
                   tests_empty: list, tests_prefilled: list):
    """运行一组测试（先空配置，后预填配置）。

    tests_empty:    用空配置运行的测试列表 [(name, fn), ...]
    tests_prefilled: 用预填配置运行的测试列表 [(name, fn), ...]
    """
    # ── 阶段1：空配置测试 ──
    empty_config = make_config(ctx, group)  # node_ids=None → 全空
    for name, test_fn in tests_empty:
        try:
            test_fn(ctx, empty_config, label)
        except SystemExit as e:
            if e.code != 0:
                print(f"\n💥 [{label}] 测试中断于: {name}")
                raise

    # ── 提取回填后 node_id，生成预填配置 ──
    node_ids = extract_node_ids(empty_config)

    # Group B 需要重置文件（因为 Group B 有两轮独立测试，每轮都要全新开始）
    # 但 Group A 的 tests_empty 和 tests_prefilled 共享同一批已创建的文件
    # 这里不 reset，保持文件含 frontmatter 状态

    prefilled_config = make_config(ctx, group, node_ids)

    print(f"\n📋 [{label}] 预填配置 node_id:")
    print(f"  root_folder: {node_ids['root_folder.node_id']}")
    for k, v in node_ids.items():
        if "folder" in k and k != "root_folder.node_id":
            print(f"  {k}: {v}")

    # ── 阶段2：预填配置测试 ──
    for name, test_fn in tests_prefilled:
        try:
            test_fn(ctx, prefilled_config, label)
        except SystemExit as e:
            if e.code != 0:
                print(f"\n💥 [{label}] 测试中断于: {name}")
                raise


# ─────────────────────────────────────────
# Main
# ─────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="dd-sync 集成测试")
    parser.add_argument("--keep", action="store_true", help="测试后保留数据（不清理）")
    args = parser.parse_args()

    # 验证前置条件
    print("🔍 检查前置条件...")
    if not SCRIPT_PATH.exists():
        sys.exit(f"❌ 脚本不存在: {SCRIPT_PATH}")

    try:
        subprocess.run(["dws", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.exit("❌ dws CLI 未安装或未登录")

    resp = run_dws("doc", "list", "--workspace", WORKSPACE_ID)
    if "error" in resp:
        sys.exit(f"❌ 知识库 {WORKSPACE_ID} 不可用: {resp.get('error', resp)}")

    print(f"✅ dws OK, 知识库 {WORKSPACE_ID} 可用\n")

    # ── 创建测试目录和文件 ──
    tmpdir = tempfile.mkdtemp(prefix="ddsync_test_")
    ctx = setup_test_dir(tmpdir)

    print(f"📁 测试目录: {tmpdir}")
    total_files = len(list(Path(tmpdir).rglob("*.md")))
    large_file = os.path.join(ctx["guide_dir"], "功能手册.md")
    print(f"📋 文件数: {total_files}")
    print(f"📐 大文件: {os.path.getsize(large_file)} bytes")

    all_tests_passed = 0
    all_tests_total = 0

    # ── Group A：无 root_folder ──
    print(f"\n{'=' * 50}")
    print("🔵 Group A：无 root_folder（上传到知识库根目录）")
    print(f"{'=' * 50}")

    tests_a_empty = [
        ("Dry-run 预览", test_dry_run),
        ("首次同步（新建文档）", test_create_new_docs),
        ("空文档跳过", test_skip_empty),
    ]
    tests_a_prefilled = [
        ("更新文档", test_update_docs),
        ("文件夹复用", test_folders_reused),
        ("回归验证（无失败）", test_no_failures),
    ]

    try:
        run_test_group(ctx, "a", "Group A", tests_a_empty, tests_a_prefilled)
        all_tests_passed += len(tests_a_empty) + len(tests_a_prefilled)
    except SystemExit:
        if not args.keep:
            cleanup_all(ctx)
        sys.exit(1)

    all_tests_total += len(tests_a_empty) + len(tests_a_prefilled)

    # ── Group B：有 root_folder ──
    print(f"\n{'=' * 50}")
    print("🟠 Group B：有 root_folder（上传到知识库下指定文件夹）")
    print(f"{'=' * 50}")

    tests_b_empty = [
        ("Dry-run 预览", test_dry_run),
        ("首次同步（新建文档+大文件分块）", test_create_new_docs),
        ("空文档跳过", test_skip_empty),
        ("大文件分块创建验证", test_chunked_create),
    ]
    tests_b_prefilled = [
        ("更新文档", test_update_docs),
        ("文件夹复用", test_folders_reused),
        ("大文件分块更新", test_chunked_update),
        ("回归验证（无失败）", test_no_failures),
    ]

    try:
        run_test_group(ctx, "b", "Group B", tests_b_empty, tests_b_prefilled)
        all_tests_passed += len(tests_b_empty) + len(tests_b_prefilled)
    except SystemExit:
        if not args.keep:
            cleanup_all(ctx)
        sys.exit(1)

    all_tests_total += len(tests_b_empty) + len(tests_b_prefilled)

    # ── 清理 ──
    if not args.keep:
        cleanup_all(ctx)
    else:
        print(f"\n⚠️  --keep: 保留测试数据于 {tmpdir}")

    print(f"\n{'=' * 50}")
    print(f"🎉 全部 {all_tests_passed}/{all_tests_total} 个测试通过！")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()

