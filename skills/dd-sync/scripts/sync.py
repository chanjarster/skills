#!/usr/bin/env python3
"""
dd-sync: 将本地 Markdown 文档批量同步到钉钉知识库。

读取 dd-sync-cfg.json 配置文件，准备钉钉文件夹，遍历 Markdown 文件，
解析 frontmatter 判定新建/更新策略，调用 dws CLI 执行同步。

Usage:
    python sync.py --config dd-sync-cfg.json
    python sync.py --config dd-sync-cfg.json --dry-run
    python sync.py --config dd-sync-cfg.json --verbose
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import yaml

# ────────────────────────────────────────────────────────────
# 常量
# ────────────────────────────────────────────────────────────

CHUNK_SIZE_THRESHOLD = 9_000   # 字符数，超过此值触发分块
MAX_CHUNK_SIZE = 9_000         # 每块最大 9000 字符（dws API 限制 10000 字符，留 10% 余量）
CST = timezone(timedelta(hours=8))


def now_iso8601() -> str:
    """返回当前时间的 ISO 8601 格式字符串（东八区）。"""
    now = datetime.now(CST)
    tz_str = now.strftime("%z")
    # strftime %z 输出 +0800，插入冒号变为 +08:00
    tz_formatted = tz_str[:3] + ":" + tz_str[3:]
    return now.strftime("%Y-%m-%dT%H:%M:%S") + tz_formatted


# ────────────────────────────────────────────────────────────
# 工具函数
# ────────────────────────────────────────────────────────────

def strip_trailing_slash(path: str) -> str:
    """去掉路径末尾的 /。"""
    return path.rstrip("/") if path else path


def is_subpath(filepath: str, dirpath: str) -> bool:
    """判断 filepath 是否在 dirpath 目录下。"""
    fp = os.path.normpath(filepath)
    dp = os.path.normpath(dirpath)
    return fp.startswith(dp + os.sep) or fp == dp


# ────────────────────────────────────────────────────────────
# Config 模块
# ────────────────────────────────────────────────────────────

def load_config(path: str) -> dict:
    """加载并校验 JSON 配置文件。"""
    if not os.path.isfile(path):
        sys.exit(f"❌ 配置文件不存在: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        sys.exit(f"❌ 配置文件 JSON 格式错误: {e}")

    required = ["source_paths", "knowledge_base", "root_folder"]
    missing = [k for k in required if k not in config]
    if missing:
        sys.exit(f"❌ 配置文件缺少必填字段: {', '.join(missing)}")

    kb = config["knowledge_base"]
    if not kb.get("workspace_id"):
        sys.exit("❌ 配置文件缺少 knowledge_base.workspace_id")

    if "folder_mapping" not in config:
        config["folder_mapping"] = []

    return config


def save_config(config: dict, path: str):
    """回填 node_id / doc_url 后写回配置文件。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ────────────────────────────────────────────────────────────
# DingTalk 模块 — 封装 dws 命令
# ────────────────────────────────────────────────────────────

def run_dws(args: list[str], dry_run: bool = False) -> dict:
    """执行 dws 命令，返回解析后的 JSON 响应。

    dry_run 模式下不实际执行，返回模拟数据。
    """
    cmd = ["dws"] + args

    if dry_run:
        print(f"  [DRY RUN] {' '.join(cmd)}")
        return {"success": True, "_dry_run": True}

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        # 尝试从 stderr 或 stdout 解析错误
        output = result.stderr.strip() or result.stdout.strip()
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return {
                "success": False,
                "error": {"message": output or f"dws 命令退出码 {result.returncode}"},
            }

    # stdout 可能包含 [INFO] 行和 JSON 混合，提取最后一行 JSON
    lines = result.stdout.strip().split("\n")
    json_line = None
    for line in reversed(lines):
        line = line.strip()
        if line.startswith("{"):
            json_line = line
            break

    if json_line:
        try:
            return json.loads(json_line)
        except json.JSONDecodeError:
            pass

    # 整段输出解析
    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {"success": False, "error": {"message": result.stdout.strip()[:500]}}


def list_folder(workspace_id: str = None, parent_node_id: str = None,
                dry_run: bool = False) -> list[dict]:
    """列出指定位置下的所有节点。
    当 parent_node_id == workspace_id 时，表示列出知识库根目录下的节点。
    """
    args = ["doc", "list", "--format", "json"]
    if workspace_id:
        args += ["--workspace", workspace_id]
    if parent_node_id:
        args += ["--folder", parent_node_id]

    resp = run_dws(args, dry_run=dry_run)
    return resp.get("nodes", [])


def find_folder_by_name(name: str, workspace_id: str = None,
                        parent_node_id: str = None, dry_run: bool = False) -> Optional[dict]:
    """在指定位置查找同名文件夹。"""
    nodes = list_folder(workspace_id=workspace_id, parent_node_id=parent_node_id,
                        dry_run=dry_run)
    for node in nodes:
        if node.get("nodeType") == "folder" and node.get("name") == name:
            return node
    return None


def create_folder(name: str, workspace_id: str = None,
                  parent_node_id: str = None, dry_run: bool = False) -> Optional[dict]:
    """创建文件夹，返回 {nodeId, docUrl, ...} 或 None。
    当 parent_node_id == workspace_id 时，在知识库根目录下创建文件夹。
    """
    args = ["doc", "folder", "create", "--name", name, "--format", "json"]
    if parent_node_id and parent_node_id != workspace_id:
        args += ["--folder", parent_node_id]
    elif workspace_id:
        args += ["--workspace", workspace_id]

    resp = run_dws(args, dry_run=dry_run)
    if resp.get("_dry_run"):
        # dry-run 模式返回模拟数据
        fake_id = f"DRY_RUN_NODE_{name.replace(' ', '_')}"
        return {"nodeId": fake_id, "docUrl": f"https://{fake_id}"}
    if resp.get("success"):
        return {"nodeId": resp["nodeId"], "docUrl": resp.get("docUrl", "")}
    else:
        err = resp.get("error", {}).get("message", "未知错误")
        print(f"  ❌ 创建文件夹失败: {err}")
        return None


def ensure_folder(name: str, workspace_id: str = None,
                  parent_node_id: str = None, dry_run: bool = False) -> Optional[dict]:
    """确保文件夹存在：先查找，不存在则创建。返回 {nodeId, docUrl} 或 None。"""
    existing = find_folder_by_name(name, workspace_id=workspace_id,
                                   parent_node_id=parent_node_id, dry_run=dry_run)
    if existing:
        return {"nodeId": existing["nodeId"], "docUrl": existing.get("docUrl", "")}
    return create_folder(name, workspace_id=workspace_id,
                         parent_node_id=parent_node_id, dry_run=dry_run)


def create_doc(name: str, content_file: str, folder_node_id: str,
               workspace_id: str = None, dry_run: bool = False) -> Optional[dict]:
    """创建钉钉文档，返回 {nodeId, docUrl} 或 None。
    
    当 folder_node_id == workspace_id 时，表示上传到知识库根目录（无父文件夹），
    使用 --workspace 而非 --folder。
    """
    args = [
        "doc", "create", "--name", name,
        "--content-file", content_file,
    ]
    if workspace_id and folder_node_id == workspace_id:
        args += ["--workspace", workspace_id]
    else:
        args += ["--folder", folder_node_id]
    args += ["--format", "json"]
    resp = run_dws(args, dry_run=dry_run)
    if resp.get("_dry_run"):
        fake_id = f"DRY_RUN_DOC_{name.replace(' ', '_')}"
        return {"nodeId": fake_id, "docUrl": f"https://{fake_id}"}
    if resp.get("success"):
        return {"nodeId": resp["nodeId"], "docUrl": resp.get("docUrl", "")}
    else:
        err = resp.get("error", {}).get("message", "未知错误")
        print(f"    ❌ 创建文档失败: {err}")
        return None


def update_doc_overwrite(node_id_or_url: str, content_file: str,
                         dry_run: bool = False) -> dict:
    """覆盖更新钉钉文档，返回完整响应 dict。调用方检查 resp['success']。"""
    args = [
        "doc", "update", "--node", node_id_or_url,
        "--content-file", content_file,
        "--mode", "overwrite",
        "--content-format", "markdown",
        "--format", "json",
        "--yes",
    ]
    return run_dws(args, dry_run=dry_run)


def update_doc_append(node_id: str, content_file: str,
                      dry_run: bool = False) -> dict:
    """追加内容到钉钉文档末尾，返回完整响应 dict。调用方检查 resp['success']。"""
    args = [
        "doc", "update", "--node", node_id,
        "--content-file", content_file,
        "--mode", "append",
        "--content-format", "markdown",
        "--format", "json",
        "--yes",
    ]
    return run_dws(args, dry_run=dry_run)


def is_doc_deleted_error(resp: dict) -> bool:
    """判断错误响应是否表示文档已被删除。"""
    error = resp.get("error", {})
    return error.get("message") == "workspace node has been recycled"


# ────────────────────────────────────────────────────────────
# Markdown 模块
# ────────────────────────────────────────────────────────────

def parse_frontmatter(filepath: str) -> tuple[dict, str]:
    """解析 markdown 文件的 YAML frontmatter。

    Returns:
        (frontmatter_dict, body_text)
        无 frontmatter 时 frontmatter_dict 为空 dict。
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        fm = {}

    body = parts[2].lstrip("\n")
    return fm, body


def write_frontmatter(filepath: str, dingding_link: str = None,
                      dingding_updated: str = None):
    """原地更新文件的 frontmatter，只修改 dingding_link / dingding_updated。

    其他字段原样保留。无 frontmatter 则在文件头创建。
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    updates = {}
    if dingding_link is not None:
        updates["dingding_link"] = dingding_link
    if dingding_updated is not None:
        updates["dingding_updated"] = dingding_updated

    if not content.startswith("---"):
        # 无 frontmatter，创建
        new_fm = yaml.dump(updates, allow_unicode=True, default_flow_style=False).strip()
        new_content = f"---\n{new_fm}\n---\n{content}"
    else:
        # 原地替换，保留其他字段不变
        parts = content.split("---", 2)
        fm_block = parts[1].strip()
        new_fm_lines = []
        updated_keys = set()

        for line in fm_block.split("\n"):
            stripped = line.strip()
            if not stripped:
                # 保留 frontmatter 字段间的有意空行
                new_fm_lines.append("")
                continue
            if ":" in stripped and not stripped.startswith("#"):
                key = stripped.split(":", 1)[0].strip()
                if key in updates:
                    new_fm_lines.append(f"{key}: {json.dumps(updates[key], ensure_ascii=False)}")
                    updated_keys.add(key)
                    continue
            new_fm_lines.append(line)

        # 追加未出现在原 frontmatter 中的新字段
        for key, value in updates.items():
            if key not in updated_keys:
                new_fm_lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")

        new_content = f"---\n{chr(10).join(new_fm_lines)}\n---\n{parts[2].lstrip(chr(10))}"

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_content)


def get_doc_title(body: str, filepath: str) -> str:
    """获取文档标题：第一个 H1 标题，无则用文件名（不含 .md）。"""
    match = re.search(r"^# (.+)$", body, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return os.path.splitext(os.path.basename(filepath))[0]


def collect_md_files(source_paths: list[str], base_dir: str,
                     exclude_files: set[str] = None) -> list[str]:
    """从 source_paths 中收集所有 .md 文件。

    - 排除配置文件和 exclude_files 中的文件。
    - 目录会递归遍历。
    """
    exclude = exclude_files or set()
    files = []
    seen = set()

    for sp in source_paths:
        full_path = os.path.join(base_dir, sp) if not os.path.isabs(sp) else sp

        if os.path.isfile(full_path):
            if full_path.endswith(".md") and full_path not in exclude:
                abs_path = os.path.abspath(full_path)
                if abs_path not in seen:
                    seen.add(abs_path)
                    files.append(full_path)
        elif os.path.isdir(full_path):
            for root, _, filenames in os.walk(full_path):
                for fn in filenames:
                    if fn.endswith(".md"):
                        abs_path = os.path.abspath(os.path.join(root, fn))
                        if abs_path not in seen and abs_path not in exclude:
                            seen.add(abs_path)
                            files.append(os.path.join(root, fn))
        else:
            print(f"  ⚠️  路径不存在，跳过: {sp}")

    return sorted(files)


# ────────────────────────────────────────────────────────────
# 分块模块
# ────────────────────────────────────────────────────────────


def _mask_headings_in_code_blocks(content: str, heading_prefix: str) -> tuple[str, dict]:
    """将代码块中以 heading_prefix 开头的行替换为占位符，避免被误切。

    Args:
        content: 原始 markdown 内容
        heading_prefix: 要保护的标题前缀，如 "## " 或 "### "

    Returns:
        (masked_content, placeholder_map)
    """
    lines = content.split('\n')
    result_lines = []
    in_code_block = False
    placeholder_idx = 0
    placeholders: dict[str, str] = {}

    for line in lines:
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code_block = not in_code_block
            result_lines.append(line)
            continue

        if in_code_block and line.startswith(heading_prefix):
            placeholder = f'__HEADING_PLACEHOLDER_{placeholder_idx}__'
            placeholders[placeholder] = line
            result_lines.append(placeholder)
            placeholder_idx += 1
        else:
            result_lines.append(line)

    return '\n'.join(result_lines), placeholders


def _unmask_headings(content: str, placeholders: dict[str, str]) -> str:
    """还原占位符为原始代码块行。"""
    for placeholder, original in placeholders.items():
        content = content.replace(placeholder, original)
    return content


def split_content(content: str, max_chunk_size: int = MAX_CHUNK_SIZE) -> list[str]:
    """按 markdown 标题边界分块。

    优先级：H2 → H3 → 空行 → 硬切。
    保证不切断代码块和表格。
    """
    if len(content) <= max_chunk_size:
        return [content]

    # 保护代码块中的 H2 标题不被误切
    masked, placeholders = _mask_headings_in_code_blocks(content, '## ')

    chunks = []
    # 用 H2 作为首选切分点
    sections = re.split(r"(?=^## )", masked, flags=re.MULTILINE)
    current = ""

    for section in sections:
        if len(current) + len(section) > max_chunk_size:
            if current:
                chunks.append(current)
            # section 自身可能也超大，立即拆分
            if len(section) > max_chunk_size:
                chunks.extend(_split_oversized(section, max_chunk_size))
                current = ""
            else:
                current = section
        else:
            current += section

    if current:
        chunks.extend(_split_oversized(current, max_chunk_size))

    # 还原代码块中的占位符
    if placeholders:
        chunks = [_unmask_headings(c, placeholders) for c in chunks]

    return chunks


def _split_oversized(text: str, max_size: int) -> list[str]:
    """将超大块进一步按 H3 切分，仍太大则按段落边界切。"""
    if len(text) <= max_size:
        return [text]

    # 保护代码块中的 H3 标题不被误切
    masked, placeholders = _mask_headings_in_code_blocks(text, '### ')

    result = []
    # H3 切分
    sub_sections = re.split(r"(?=^### )", masked, flags=re.MULTILINE)
    current = ""
    for sub in sub_sections:
        if len(current) + len(sub) > max_size:
            if current:
                result.extend(_split_by_paragraph(current, max_size))
            # sub 自身可能也超大，立即拆分
            if len(sub) > max_size:
                result.extend(_split_by_paragraph(sub, max_size))
                current = ""
            else:
                current = sub
        else:
            current += sub
    if current:
        result.extend(_split_by_paragraph(current, max_size))

    # 还原代码块中的占位符
    if placeholders:
        result = [_unmask_headings(r, placeholders) for r in result]

    return result


def _split_by_paragraph(text: str, max_size: int) -> list[str]:
    """按空行（段落边界）切分，避免切断代码块和表格。

    单段落仍超大时按行切分，单行仍超大时硬切（字符级）。
    """
    if len(text) <= max_size:
        return [text]

    result = []
    # 按空行切分成段落组
    paragraphs = re.split(r"(\n\n+)", text)
    current = ""
    in_code_block = False

    for para in paragraphs:
        if para.startswith("```"):
            in_code_block = not in_code_block

        if in_code_block:
            current += para
            continue

        if len(current) + len(para) > max_size and current.strip():
            result.extend(_hard_split(current, max_size))
            current = para
        else:
            current += para

    if current.strip():
        result.extend(_hard_split(current, max_size))

    return result


def _hard_split(text: str, max_size: int) -> list[str]:
    """最后手段：按行切分，单行仍超大则字符级硬切。"""
    if len(text) <= max_size:
        return [text]

    result = []
    lines = text.split('\n')
    current = ""

    for line in lines:
        # 单行超大：字符级硬切
        if len(line) > max_size:
            if current:
                result.append(current)
                current = ""
            for i in range(0, len(line), max_size):
                result.append(line[i:i + max_size])
            continue

        candidate = current + '\n' + line if current else line
        if len(candidate) > max_size:
            result.append(current)
            current = line
        else:
            current = candidate

    if current:
        result.append(current)

    return result


# ────────────────────────────────────────────────────────────
# 同步主流程
# ────────────────────────────────────────────────────────────

def phase2_prepare_folders(config: dict, dry_run: bool = False):
    """阶段二：准备钉钉文件夹结构。"""
    print("\n📁 阶段二：准备钉钉文件夹")

    ws_id = config["knowledge_base"]["workspace_id"]
    rf = config["root_folder"]

    # 确保根文件夹
    if rf.get("node_id"):
        print(f"  ✅ root_folder \"{rf.get('name', '根目录')}\" — 已有 node_id，跳过")
    else:
        rf["node_id"] = ws_id
        print(f"  ℹ️  root_folder 无 node_id，文档将上传到知识库根目录")

    # 确保子文件夹
    root_node_id = rf["node_id"]
    for mapping in config.get("folder_mapping", []):
        ddn = mapping["dingtalk_folder_name"] or os.path.basename(
            strip_trailing_slash(mapping["local_dir"])
        )
        if mapping.get("node_id"):
            print(f"  ✅ {mapping['local_dir']} → \"{ddn}\" — 已有 node_id，跳过")
        else:
            result = ensure_folder(ddn, workspace_id=ws_id,
                                   parent_node_id=root_node_id, dry_run=dry_run)
            if result:
                mapping["node_id"] = result["nodeId"]
                mapping["doc_url"] = result["docUrl"]
                print(f"  ✅ {mapping['local_dir']} → \"{ddn}\" (nodeId: {mapping['node_id']})")
            else:
                print(f"  ❌ {mapping['local_dir']} → \"{ddn}\" 创建失败")
                return False

    # 回填配置
    if not dry_run:
        save_config(config, config["_config_path"])
    return True


def find_target_folder(filepath: str, config: dict) -> str:
    """根据文件路径找到对应的钉钉文件夹 node_id。"""
    base_dir = config["_base_dir"]
    rel = os.path.relpath(os.path.abspath(filepath), base_dir)

    # 从最深的映射开始匹配
    sorted_mappings = sorted(
        config.get("folder_mapping", []),
        key=lambda m: len(m["local_dir"]),
        reverse=True,
    )
    for mapping in sorted_mappings:
        ld = strip_trailing_slash(mapping["local_dir"])
        if is_subpath(rel, ld):
            return mapping["node_id"]

    return config["root_folder"]["node_id"]


def sync_one_file(filepath: str, config: dict, dry_run: bool = False,
                  verbose: bool = False) -> str:
    """同步单个文件。返回状态："success" / "skip" / "fallback" / "failed"。"""
    rel_path = os.path.relpath(filepath, config["_base_dir"])
    fm, body = parse_frontmatter(filepath)

    # 正文为空
    if not body.strip():
        if verbose:
            print(f"  [SKIP] {rel_path} — 正文为空")
        return "skip"

    target_node_id = find_target_folder(filepath, config)
    title = get_doc_title(body, filepath)
    content_size = len(body)
    ws_id = config["knowledge_base"]["workspace_id"]

    # 根据大小决定是否分块
    use_chunking = content_size > CHUNK_SIZE_THRESHOLD

    if "dingding_link" in fm and fm["dingding_link"]:
        # ── 更新已有文档 ──
        return _sync_update(filepath, rel_path, fm, body, title, target_node_id,
                            use_chunking, ws_id, dry_run, verbose)
    else:
        # ── 新建文档 ──
        return _sync_create(filepath, rel_path, body, title, target_node_id,
                            use_chunking, ws_id, dry_run, verbose)


def _write_temp_file(content: str) -> str:
    """将内容写入临时文件，返回文件路径。"""
    fd, tmp_path = tempfile.mkstemp(suffix=".md", prefix="ddsync_")
    os.close(fd)
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    return tmp_path


def _sync_create(filepath: str, rel_path: str, body: str, title: str,
                 target_node_id: str, use_chunking: bool,
                 ws_id: str, dry_run: bool, verbose: bool) -> str:
    """新建文档逻辑。"""
    now_ts = now_iso8601()

    if dry_run:
        tag = "[CREATE-chunk]" if use_chunking else "[CREATE]"
        print(f"  {tag} {rel_path} → \"{title}\"")
        return "success"

    if not use_chunking:
        tmp_path = _write_temp_file(body)
        try:
            result = create_doc(title, tmp_path, target_node_id,
                                workspace_id=ws_id, dry_run=False)
        finally:
            os.unlink(tmp_path)

        if result:
            write_frontmatter(filepath, dingding_link=result["docUrl"],
                              dingding_updated=now_ts)
            print(f"  [CREATE] {rel_path} → \"{title}\" ({result['docUrl']})")
            return "success"
        else:
            print(f"  [CREATE] {rel_path} → ❌ 创建失败")
            return "failed"

    # 分块创建
    chunks = split_content(body)
    result = _chunked_create(title, chunks, target_node_id, ws_id)
    if result:
        write_frontmatter(filepath, dingding_link=result["docUrl"],
                          dingding_updated=now_ts)
        print(f"  [CREATE-chunk] {rel_path} → \"{title}\" ({result['docUrl']}, {len(chunks)} 片)")
        return "success"
    else:
        print(f"  [CREATE-chunk] {rel_path} → ❌ 分块创建失败")
        return "failed"


def _sync_update(filepath: str, rel_path: str, fm: dict, body: str, title: str,
                 target_node_id: str, use_chunking: bool,
                 ws_id: str, dry_run: bool, verbose: bool) -> str:
    """更新已有文档逻辑。"""
    now_ts = now_iso8601()
    doc_link = fm["dingding_link"]

    if dry_run:
        tag = "[UPDATE-chunk]" if use_chunking else "[UPDATE]"
        print(f"  {tag} {rel_path} → \"{title}\"")
        return "success"

    if not use_chunking:
        tmp_path = _write_temp_file(body)
        try:
            resp = update_doc_overwrite(doc_link, tmp_path, dry_run=False)
        finally:
            os.unlink(tmp_path)

        if resp.get("success"):
            write_frontmatter(filepath, dingding_updated=now_ts)
            print(f"  [UPDATE] {rel_path} → \"{title}\" (已更新)")
            return "success"
        elif is_doc_deleted_error(resp):
            # 降级为新建（需要重新写临时文件，因为之前的已被 unlink）
            print(f"  [UPDATE] {rel_path} → ⚠️ 文档已删除，降级为新建")
            fallback_tmp = _write_temp_file(body)
            try:
                new_result = create_doc(title, fallback_tmp, target_node_id,
                                        workspace_id=ws_id, dry_run=False)
            finally:
                os.unlink(fallback_tmp)
            if new_result:
                write_frontmatter(filepath,
                                  dingding_link=new_result["docUrl"],
                                  dingding_updated=now_ts)
                print(f"    ✅ 重建成功 ({new_result['docUrl']})")
                return "fallback"
            else:
                print(f"    ❌ 重建失败")
                return "failed"
        else:
            print(f"  [UPDATE] {rel_path} → ❌ 更新失败")
            return "failed"

    # 分块更新
    chunks = split_content(body)
    update_resp = _chunked_update(doc_link, chunks)
    if update_resp is True:
        write_frontmatter(filepath, dingding_updated=now_ts)
        print(f"  [UPDATE-chunk] {rel_path} → \"{title}\" (已更新, {len(chunks)} 片)")
        return "success"
    elif isinstance(update_resp, dict) and is_doc_deleted_error(update_resp):
        # 文档已删除，降级为分块新建
        print(f"  [UPDATE-chunk] {rel_path} → ⚠️ 文档已删除，降级为分块新建")
        new_result = _chunked_create(title, chunks, target_node_id, ws_id)
        if new_result:
            write_frontmatter(filepath,
                              dingding_link=new_result["docUrl"],
                              dingding_updated=now_ts)
            print(f"    ✅ 分块重建成功 ({new_result['docUrl']})")
            return "fallback"
        else:
            print(f"    ❌ 分块重建失败")
            return "failed"
    else:
        print(f"  [UPDATE-chunk] {rel_path} → ❌ 分块更新失败")
        return "failed"


def _chunked_create(title: str, chunks: list[str],
                    folder_node_id: str, ws_id: str) -> Optional[dict]:
    """分块创建：先建空文档，再逐片 append。返回 {docUrl} 或 None。"""
    # 第一步：创建空文档
    empty_file = _write_temp_file("")
    try:
        result = create_doc(title, empty_file, folder_node_id,
                            workspace_id=ws_id, dry_run=False)
    finally:
        os.unlink(empty_file)

    if not result:
        return None

    node_id = result["nodeId"]
    doc_url = result["docUrl"]

    # 第二步：逐片 append
    for i, chunk in enumerate(chunks):
        tmp = _write_temp_file(chunk)
        try:
            r = update_doc_append(node_id, tmp, dry_run=False)
            if not r.get("success"):
                # 重试一次
                r = update_doc_append(node_id, tmp, dry_run=False)
            if not r.get("success"):
                err_msg = r.get("error", {}).get("message", str(r)[:100])
                print(f"    ❌ 分片 {i + 1}/{len(chunks)} append 失败: {err_msg}")
                return None
        finally:
            os.unlink(tmp)

    return {"docUrl": doc_url}


def _chunked_update(doc_link: str, chunks: list[str]):
    """分块更新：第一片 overwrite，后续 append。

    Returns:
        True on success, or the failed response dict on failure.
    """
    # 第一片 overwrite
    tmp_first = _write_temp_file(chunks[0])
    try:
        result = update_doc_overwrite(doc_link, tmp_first, dry_run=False)
    finally:
        os.unlink(tmp_first)

    if not result.get("success"):
        return result  # 返回失败响应，供调用方检查是否因文档被删除

    # 获取 nodeId 用于后续 append
    node_id = result.get("nodeId", doc_link)

    # 后续片 append
    for i, chunk in enumerate(chunks[1:], start=2):
        tmp = _write_temp_file(chunk)
        try:
            r = update_doc_append(node_id, tmp, dry_run=False)
            if not r.get("success"):
                r = update_doc_append(node_id, tmp, dry_run=False)
            if not r.get("success"):
                print(f"    ❌ 分片 {i}/{len(chunks)} append 失败")
                return r
        finally:
            os.unlink(tmp)

    return True


def phase3_sync_documents(config: dict, dry_run: bool = False,
                          verbose: bool = False):
    """阶段三：执行文档同步。"""
    base_dir = config["_base_dir"]
    cfg_path = os.path.abspath(config["_config_path"])

    files = collect_md_files(config["source_paths"], base_dir,
                             exclude_files={cfg_path})
    total = len(files)
    print(f"\n📄 阶段三：同步文档 (共 {total} 个文件)")

    stats = {"success": 0, "skip": 0, "fallback": 0, "failed": 0}
    failed_details = []

    for filepath in files:
        rel = os.path.relpath(filepath, base_dir)
        status = sync_one_file(filepath, config, dry_run=dry_run, verbose=verbose)
        stats[status] = stats.get(status, 0) + 1

        if status == "failed":
            failed_details.append(rel)

    # 汇总报告
    print(f"\n{'=' * 40}")
    print(f"结果: ✅ {stats['success']} 成功   "
          f"⚠️ {stats['fallback']} 降级   "
          f"⏭️ {stats['skip']} 跳过   "
          f"❌ {stats['failed']} 失败")
    if failed_details:
        print("失败详情:")
        for fd in failed_details:
            print(f"  - {fd}")
    print(f"{'=' * 40}")


# ────────────────────────────────────────────────────────────
# CLI 入口
# ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="dd-sync: 将本地 Markdown 文档批量同步到钉钉知识库"
    )
    parser.add_argument("--config", required=True, help="配置文件路径 (dd-sync-cfg.json)")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际执行")
    parser.add_argument("--verbose", action="store_true", help="详细输出")
    args = parser.parse_args()

    # 检测 dws
    try:
        subprocess.run(["dws", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.exit("❌ dws CLI 未安装或未登录。请先安装: https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli")

    config_path = os.path.abspath(args.config)
    config = load_config(config_path)
    config["_config_path"] = config_path
    config["_base_dir"] = os.path.dirname(config_path)  # 配置文件所在目录作为基准

    print(f"{'=' * 40}")
    print(f"dd-sync v1 {' [DRY RUN]' if args.dry_run else ''}")
    print(f"{'=' * 40}")

    # 阶段二
    if not phase2_prepare_folders(config, dry_run=args.dry_run):
        sys.exit("❌ 阶段二失败，终止同步")

    # 阶段三
    phase3_sync_documents(config, dry_run=args.dry_run, verbose=args.verbose)


if __name__ == "__main__":
    main()
