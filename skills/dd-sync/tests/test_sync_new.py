#!/usr/bin/env python3
"""
dd-sync 集成测试套件（pytest 版本）

使用钉钉知识库运行端到端测试。通过环境变量 WORKSPACE_ID 指定目标知识库。

测试分为两组，共享同一套 markdown 测试文件：
  - TestGroupA: root_folder.name 为空 → 文档直接上传到知识库根目录
  - TestGroupB: root_folder.name 有值 → 文档上传到知识库下的指定文件夹

Usage:
    # 运行全部测试
    WORKSPACE_ID=<YOUR_ID> pytest tests/test_sync_new.py -v

    # 只运行 Group A
    WORKSPACE_ID=<YOUR_ID> pytest tests/test_sync_new.py::TestGroupA -v

    # 只运行单个测试
    WORKSPACE_ID=<YOUR_ID> pytest tests/test_sync_new.py::TestGroupA::test_dry_run -v

    # 按名称筛选
    WORKSPACE_ID=<YOUR_ID> pytest tests/test_sync_new.py -k "dry_run" -v
    WORKSPACE_ID=<YOUR_ID> pytest tests/test_sync_new.py -k "update" -v

    # 测试后保留数据（手动检查）
    KEEP=1 WORKSPACE_ID=<YOUR_ID> pytest tests/test_sync_new.py -v
"""

import json
import os
import subprocess
import sys
import time
import shutil
from pathlib import Path

import pytest

# ─────────────────────────────────────────
# 常量
# ─────────────────────────────────────────

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
DOCS_FIXTURES = FIXTURES_DIR / "docs"
CONFIG_A_TEMPLATE = FIXTURES_DIR / "config-a.json"
CONFIG_B_TEMPLATE = FIXTURES_DIR / "config-b.json"
SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "sync.py"

ROOT_FOLDER_B_PREFIX = "_dd_sync_test_B_"


# ─────────────────────────────────────────
# 前置条件检查
# ─────────────────────────────────────────

def _check_prerequisites():
    """检查运行测试所需的前置条件。"""
    if not SCRIPT_PATH.exists():
        pytest.exit(f"❌ 脚本不存在: {SCRIPT_PATH}")

    try:
        subprocess.run(["dws", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.exit("❌ dws CLI 未安装或未登录")


_check_prerequisites()


# ─────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────

def run_sync(config_path: str, *extra_args: str) -> subprocess.CompletedProcess:
    """运行 sync.py，返回 subprocess 结果。"""
    cmd = [sys.executable, str(SCRIPT_PATH), "--config", config_path, *extra_args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180)


def run_dws(*args: str) -> dict:
    """运行 dws 命令并返回解析后的 JSON。"""
    cmd = ["dws", *args, "--format", "json"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    output = r.stdout.strip()
    # 尝试直接解析
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        pass
    # 从混合输出中提取 JSON 子串
    start = output.find("{")
    end = output.rfind("}")
    if start >= 0 and end >= 0:
        try:
            return json.loads(output[start:end + 1])
        except json.JSONDecodeError:
            pass
    return {"_raw": output[:200], "_stderr": r.stderr.strip()[:200]}


def extract_node_ids(config_path: str) -> dict:
    """从已回填的配置文件中提取所有 node_id/doc_url。"""
    with open(config_path, encoding="utf-8") as f:
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
# Session fixture
# ─────────────────────────────────────────

@pytest.fixture(scope="session")
def workspace_id():
    """从环境变量获取 WORKSPACE_ID 并验证其可用性。"""
    wid = os.environ.get("WORKSPACE_ID", "")
    if not wid:
        pytest.skip("WORKSPACE_ID 环境变量未设置")

    resp = run_dws("doc", "list", "--workspace", wid)
    if "error" in resp:
        pytest.fail(f"❌ 知识库 {wid} 不可用: {resp.get('error', resp)}")

    return wid


@pytest.fixture(scope="session")
def keep():
    """KEEP 环境变量控制测试后是否保留数据。"""
    return os.environ.get("KEEP", "") == "1"


# ─────────────────────────────────────────
# 断言辅助函数
# ─────────────────────────────────────────

def _fail(label: str, msg: str):
    pytest.fail(f"[{label}]: {msg}")


def assert_in(text: str, output: str, label: str, stderr: str = ""):
    if text not in output:
        detail = f"期望包含 '{text}'\n实际输出(尾):\n{output[-1500:]}"
        if stderr:
            detail += f"\nstderr:\n{stderr[-1500:]}"
        _fail(label, detail)


def assert_not_in(text: str, output: str, label: str, stderr: str = ""):
    if text in output:
        detail = f"不应包含 '{text}'\n实际输出(尾):\n{output[-1500:]}"
        if stderr:
            detail += f"\nstderr:\n{stderr[-1500:]}"
        _fail(label, detail)


def assert_file_contains(filepath: str, text: str, label: str):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    if text not in content:
        _fail(label, f"文件 {filepath} 不包含 '{text}'")


def assert_file_not_contains(filepath: str, text: str, label: str):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    if text in content:
        _fail(label, f"文件 {filepath} 不应包含 '{text}'")


def assert_single_frontmatter(filepath: str, label: str):
    """断言文件有且仅有一个 YAML frontmatter，无重复。"""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.startswith("---"):
        _fail(label, f"文件 {filepath} 没有 frontmatter")

    parts = content.split("---", 2)
    if len(parts) < 3:
        _fail(label, f"文件 {filepath} frontmatter 不完整")

    after_fm = parts[2]
    if after_fm.lstrip().startswith("---"):
        _fail(label, f"文件 {filepath} 存在重复 frontmatter")

    count = content.count("dingding_link:")
    if count > 1:
        _fail(label, f"文件 {filepath} 有 {count} 个 dingding_link（重复 frontmatter）")


# ─────────────────────────────────────────
# 工作目录构建
# ─────────────────────────────────────────

def _build_work_dir(tmp_path_factory, workspace_id: str,
                    config_template: Path, group: str,
                    root_folder_name: str = "",
                    root_folder_node_id: str = "") -> dict:
    """创建测试工作目录：复制 fixtures、生成配置文件。

    Args:
        tmp_path_factory: pytest tmp_path_factory
        workspace_id: 钉钉知识库 ID
        config_template: 配置模板文件路径
        group: "a" 或 "b"
        root_folder_name: Group B 的根文件夹名称（仅 group="b" 时有效）
        root_folder_node_id: 预先创建好的根文件夹 node_id（写入配置以跳过 sync.py 的创建）

    Returns:
        dict with work_dir, config_path, docs_dir, api_dir, guide_dir, ...
    """
    work_dir = tmp_path_factory.mktemp(f"ddsync_{group}")

    # 复制 docs fixtures
    docs_dir = work_dir / "docs"
    shutil.copytree(str(DOCS_FIXTURES), str(docs_dir))

    # 生成配置文件（替换占位符）
    config_path = work_dir / f"config-{group}.json"
    with open(config_template, encoding="utf-8") as f:
        config_content = f.read()
    config_content = config_content.replace("{{WORKSPACE_ID}}", workspace_id)
    config_content = config_content.replace("{{ROOT_FOLDER_B}}", root_folder_name)
    config_content = config_content.replace(
        "{{ROOT_FOLDER_B_NODE_ID}}", root_folder_node_id)
    
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(config_content)

    # 如果有预先创建的根文件夹 node_id，回填到配置
    if root_folder_node_id:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["root_folder"]["node_id"] = root_folder_node_id
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")

    return {
        "work_dir": str(work_dir),
        "config_path": str(config_path),
        "docs_dir": str(docs_dir),
        "api_dir": str(docs_dir / "api"),
        "guide_dir": str(docs_dir / "guide"),
        "workspace_id": workspace_id,
        "root_folder_name": root_folder_name,
    }


# ─────────────────────────────────────────
# 清理函数
# ─────────────────────────────────────────

def _setup_group_b_root_folder(workspace_id: str, folder_name: str) -> str:
    """为 Group B 预创建根文件夹，返回 node_id。"""
    nodes = run_dws("doc", "list", "--workspace", workspace_id)
    for n in nodes.get("nodes", []):
        if n.get("name") == folder_name:
            print(f"  ℹ️  Group B 根文件夹已存在 (nodeId: {n['nodeId']})")
            return n["nodeId"]

    result = run_dws("doc", "folder", "create", "--name", folder_name,
                     "--workspace", workspace_id)
    if result.get("success"):
        print(f"  ✅ Group B 根文件夹已创建 (nodeId: {result['nodeId']})")
        return result["nodeId"]
    else:
        pytest.fail(f"❌ 无法创建 Group B 根文件夹: {result}")


def _cleanup_group_a(workspace_id: str):
    """清理 Group A：删除知识库根目录下的测试文档及文件夹。"""
    test_names = {"quickstart", "empty", "config"}
    nodes = run_dws("doc", "list", "--workspace", workspace_id)
    to_delete = []

    for n in nodes.get("nodes", []):
        name = n.get("name", "")
        if (name in test_names
                or name.startswith("快速开始指南")
                or name.startswith("配置说明")
                or name.startswith("用户接口")
                or name.startswith("系统功能模块参考手册")
                or name.startswith("图片测试文档")
                or name.startswith("相对路径图片测试")):
            to_delete.append((n["nodeId"], name))

    for n in nodes.get("nodes", []):
        name = n.get("name", "")
        if name in ("API文档", "开发指南"):
            to_delete.append((n["nodeId"], name))

    for node_id, name in to_delete:
        try:
            run_dws("doc", "delete", "--node", node_id, "--yes")
            print(f"  🗑️  已删除 Group A: {name}")
        except Exception:
            pass


def _cleanup_group_b(workspace_id: str, folder_name: str):
    """清理 Group B：删除 root_folder（连带其下所有子节点）。"""
    nodes = run_dws("doc", "list", "--workspace", workspace_id)
    for n in nodes.get("nodes", []):
        if n.get("name") == folder_name:
            try:
                run_dws("doc", "delete", "--node", n["nodeId"], "--yes")
                print(f"  🗑️  已删除 Group B: {folder_name}")
            except Exception:
                pass
            break


# ═════════════════════════════════════════
# 共享测试方法基类
# ═════════════════════════════════════════

class _TestSyncBase:
    """共享测试方法。子类只需提供 ctx fixture 来区分 config 和清理策略。"""

    # ── 1. Dry-run 预览 ──

    def test_01_dry_run(self, ctx):
        """dry-run 模式：预览操作计划（含图片处理）。"""
        r = run_sync(ctx["config_path"], "--dry-run")
        output, err = r.stdout, r.stderr

        assert_in("[DRY RUN]", output, "dry-run 标记", stderr=err)
        assert_in("阶段二", output, "阶段二标题", stderr=err)
        assert_in("阶段三", output, "阶段三标题", stderr=err)
        assert_in("[CREATE]", output, "新建操作", stderr=err)

        # dry-run 不应修改任何文件
        assert_file_not_contains(
            os.path.join(ctx["docs_dir"], "quickstart.md"),
            "dingding_link", "dry-run 不写 frontmatter")

        # 图片预览
        assert_in("[IMG]", output, "本地图片计划", stderr=err)
        assert_in("[IMG-ext]", output, "外部图片计划", stderr=err)
        assert_in("[IMG-PLACEHOLDER-", output, "占位标记", stderr=err)

    # ── 2. 首次同步 ──

    def test_02_create_new_docs(self, ctx):
        """首次同步：新建所有文档（含大文件分块），验证 frontmatter、空文档跳过、图片、配置回填。"""
        r = run_sync(ctx["config_path"])
        output, err = r.stdout, r.stderr

        # 操作标记
        assert_in("[CREATE]", output, "新建操作", stderr=err)
        assert_in("✅", output, "成功标记", stderr=err)
        assert_not_in("[UPDATE]", output, "不应有更新", stderr=err)

        docs_dir = ctx["docs_dir"]

        # 根目录文档
        qs = os.path.join(docs_dir, "quickstart.md")
        assert_file_contains(qs, "dingding_link:", "quickstart.md 有 link")
        assert_file_contains(qs, "dingding_updated:", "quickstart.md 有 updated")
        assert_single_frontmatter(qs, "quickstart.md 无重复 fm")

        cm = os.path.join(docs_dir, "config.md")
        assert_file_contains(cm, "title: 配置说明", "config.md 保留 title")
        assert_file_contains(cm, "author: admin", "config.md 保留 author")
        assert_file_contains(cm, "dingding_link:", "config.md 有 link")
        assert_single_frontmatter(cm, "config.md 无重复 fm")

        # 图片文档
        image_doc = os.path.join(docs_dir, "image_doc.md")
        assert_file_contains(image_doc, "dingding_link:", "image_doc.md 有 link")
        assert_file_contains(image_doc, "dingding_updated:", "image_doc.md 有 updated")
        assert_single_frontmatter(image_doc, "image_doc.md 无重复 fm")

        # 子目录文档
        ua = os.path.join(ctx["api_dir"], "用户接口.md")
        assert_file_contains(ua, "dingding_link:", "用户接口.md 有 link")
        assert_file_contains(ua, "dingding_updated:", "用户接口.md 有 updated")
        assert_single_frontmatter(ua, "用户接口.md 无重复 fm")

        # 大文件（分块创建）
        mn = os.path.join(ctx["guide_dir"], "功能手册.md")
        assert_file_contains(mn, "dingding_link:", "功能手册.md 有 link")
        assert_file_contains(mn, "dingding_updated:", "功能手册.md 有 updated")
        assert_single_frontmatter(mn, "功能手册.md 分块创建无重复 fm")

        # 空文档不应有 link
        assert_file_not_contains(
            os.path.join(docs_dir, "empty.md"),
            "dingding_link", "empty.md 不应有 link")

        # 配置回填验证：子文件夹 node_id 已回填
        with open(ctx["config_path"], encoding="utf-8") as f:
            cfg = json.load(f)
        for mapping in cfg.get("folder_mapping", []):
            assert mapping["node_id"], f"{mapping['local_dir']} → {mapping['dingtalk_folder_name']} node_id 已回填"

    # ── 3. 更新同步 ──

    def test_03_update_docs(self, ctx):
        """修改 quickstart.md + image_doc.md + 功能手册.md 后同步，验证 [UPDATE]/[UPDATE-chunk]，0 失败。"""
        # 修改 quickstart.md
        quickstart = os.path.join(ctx["docs_dir"], "quickstart.md")
        with open(quickstart, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace("# 快速开始指南", "# 快速开始指南 (v2.0)")
        content += "\n## 新增章节\n\n这是新内容。\n"
        with open(quickstart, "w", encoding="utf-8") as f:
            f.write(content)

        # 修改 image_doc.md
        image_doc = os.path.join(ctx["docs_dir"], "image_doc.md")
        with open(image_doc, "r", encoding="utf-8") as f:
            img_content = f.read()
        img_content = img_content.replace("# 图片测试文档", "# 图片测试文档 (已更新)")
        img_content += "\n## 新增内容\n\n更新后的附加文字。\n"
        with open(image_doc, "w", encoding="utf-8") as f:
            f.write(img_content)

        # 修改功能手册.md（大文件，触发分块更新）
        manual = os.path.join(ctx["guide_dir"], "功能手册.md")
        with open(manual, "r", encoding="utf-8") as f:
            mn_content = f.read()
        parts = mn_content.split("---", 2)
        mn_new = (
            parts[0] + "---" + parts[1] + "---\n\n"
            "> **最后更新**: 测试分块更新\n\n" + parts[2]
        )
        with open(manual, "w", encoding="utf-8") as f:
            f.write(mn_new)

        # 一次 sync 处理三个修改
        r = run_sync(ctx["config_path"])
        output, err = r.stdout, r.stderr

        assert_in("[UPDATE]", output, "更新操作", stderr=err)
        assert_in("[UPDATE-chunk]", output, "分块更新标记", stderr=err)
        assert_in("功能手册", output, "功能手册在输出中", stderr=err)
        assert_not_in("失败详情", output, "无失败", stderr=err)
        assert_single_frontmatter(quickstart, "更新后 quickstart.md 无重复 fm")
        assert_single_frontmatter(image_doc, "更新后 image_doc.md 无重复 fm")
        assert_file_contains(str(manual), "dingding_updated", "分块更新后 updated 已刷新")
        assert_single_frontmatter(str(manual), "分块更新后 功能手册.md 无重复 fm")

        # 文件夹复用 & 0 失败
        assert_in("已有 node_id，跳过", output, "文件夹复用", stderr=err)
        assert_not_in("失败详情", output, "无失败详情", stderr=err)
        assert_in("❌ 0 失败", output, "失败计数为 0", stderr=err)


# ═════════════════════════════════════════
# TestGroupA：无 root_folder（上传到知识库根目录）
# ═════════════════════════════════════════

class TestGroupA(_TestSyncBase):
    """root_folder.name 为空 → 文档直接上传到知识库根目录。"""

    @pytest.fixture(scope="class")
    def ctx(self, tmp_path_factory, workspace_id, keep):
        """Group A 测试工作目录（class 级别，所有测试共享）。"""
        ctx_data = _build_work_dir(
            tmp_path_factory, workspace_id, CONFIG_A_TEMPLATE, "a"
        )
        yield ctx_data
        if not keep:
            _cleanup_group_a(workspace_id)


# ═════════════════════════════════════════
# TestGroupB：有 root_folder（上传到指定文件夹）
# ═════════════════════════════════════════

class TestGroupB(_TestSyncBase):
    """root_folder.name 有值 → 文档上传到知识库下的指定文件夹。"""

    @pytest.fixture(scope="class")
    def ctx(self, tmp_path_factory, workspace_id, keep):
        """Group B 测试工作目录（class 级别，所有测试共享）。"""
        root_folder_name = f"{ROOT_FOLDER_B_PREFIX}{int(time.time())}"
        # 先创建根文件夹，再把 node_id 传给 _build_work_dir 写入配置
        root_node_id = _setup_group_b_root_folder(workspace_id, root_folder_name)
        ctx_data = _build_work_dir(
            tmp_path_factory, workspace_id, CONFIG_B_TEMPLATE, "b",
            root_folder_name=root_folder_name,
            root_folder_node_id=root_node_id,
        )
        yield ctx_data
        if not keep:
            _cleanup_group_b(workspace_id, root_folder_name)
