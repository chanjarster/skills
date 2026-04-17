#!/usr/bin/env python3
"""Grade Pin skill test runs against assertions."""
import json
import os
import re
import sys

def read_file(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return None

def check_dir_exists(path):
    return os.path.isdir(path)

def check_file_exists(path):
    return os.path.isfile(path)

def has_mermaid(content):
    return '```mermaid' in content if content else False

def grade_eval1(base_path):
    """Eval 1: Project Initialization"""
    results = []

    # 1. directory-structure-complete
    dirs = ['pin-docs/业务知识', 'pin-docs/技术文档-X', 'pin-docs/规范文档-X', 'pin-docs/任务日志']
    all_exist = all(os.path.isdir(os.path.join(base_path, d)) for d in dirs)
    results.append({
        "text": "directory-structure-complete: All 4 top-level directories exist",
        "passed": all_exist,
        "evidence": f"Dirs exist: {[os.path.isdir(os.path.join(base_path, d)) for d in dirs]}"
    })

    # 2. index-files-created
    index_files = [
        'pin-docs/业务知识/0-索引.md',
        'pin-docs/技术文档-X/0-索引.md',
        'pin-docs/规范文档-X/0-索引.md',
        'pin-docs/任务日志/0-索引.md'
    ]
    all_exist = all(os.path.isfile(os.path.join(base_path, f)) for f in index_files)
    results.append({
        "text": "index-files-created: Each top-level directory has a 0-索引.md",
        "passed": all_exist,
        "evidence": f"Index files exist: {[os.path.isfile(os.path.join(base_path, f)) for f in index_files]}"
    })

    # 3. overview-initialized
    overview_path = os.path.join(base_path, 'pin-docs/业务知识/1-需求文档/0-overview.md')
    overview = read_file(overview_path)
    has_domains = overview is not None and all(kw in overview for kw in ['商品管理', '订单管理', '用户管理'])
    results.append({
        "text": "overview-initialized: 0-overview.md contains the 3 e-commerce domains",
        "passed": has_domains,
        "evidence": f"overview exists: {overview is not None}, contains domains: {has_domains}"
    })

    # 4. task-log-status-dirs
    status_dirs = ['pin-docs/任务日志/0-未开始', 'pin-docs/任务日志/1-进行中', 'pin-docs/任务日志/2-已完成']
    all_exist = all(os.path.isdir(os.path.join(base_path, d)) for d in status_dirs)
    results.append({
        "text": "task-log-status-dirs: 任务日志 has all 3 status subdirs",
        "passed": all_exist,
        "evidence": f"Status dirs exist: {[os.path.isdir(os.path.join(base_path, d)) for d in status_dirs]}"
    })

    return results

def grade_eval2(base_path):
    """Eval 2: Create Requirement Document"""
    results = []

    # 1. doc-in-correct-path
    req_dir = os.path.join(base_path, 'pin-docs/业务知识/1-需求文档')
    if os.path.isdir(req_dir):
        files = [f for f in os.listdir(req_dir) if f.endswith('.md') and f != '0-overview.md']
        doc_exists = len(files) > 0
        doc_path = os.path.join(req_dir, files[0]) if doc_exists else None
        doc_content = read_file(doc_path) if doc_path else None
    else:
        doc_exists = False
        doc_content = None

    # Check naming convention (starts with digit-)
    has_correct_naming = doc_exists and bool(re.match(r'^\d+-', files[0])) if doc_exists else False
    results.append({
        "text": "doc-in-correct-path: Document in 1-需求文档/ with sequential naming",
        "passed": doc_exists and has_correct_naming,
        "evidence": f"Files found: {files if os.path.isdir(req_dir) else 'req_dir not found'}"
    })

    # 2. index-updated
    index_path = os.path.join(base_path, 'pin-docs/业务知识/0-索引.md')
    index_content = read_file(index_path)
    index_updated = index_content is not None and ('用户管理' in index_content or '用户' in index_content)
    results.append({
        "text": "index-updated: 业务知识/0-索引.md references the new document",
        "passed": index_updated,
        "evidence": f"index exists: {index_content is not None}, contains user ref: {index_updated}"
    })

    # 3. has-bounded-context
    has_context = doc_content is not None and ('边界' in (doc_content or '') or 'Context' in (doc_content or '') or '范围' in (doc_content or ''))
    results.append({
        "text": "has-bounded-context: Document contains boundary context section",
        "passed": has_context,
        "evidence": f"Has boundary/context keywords: {has_context}"
    })

    # 4. has-mermaid-diagram
    has_mermaid_val = has_mermaid(doc_content)
    results.append({
        "text": "has-mermaid-diagram: Document contains at least one mermaid code block",
        "passed": has_mermaid_val,
        "evidence": f"Has mermaid: {has_mermaid_val}"
    })

    # 5. covers-rbac
    has_rbac = doc_content is not None and all(kw in (doc_content or '') for kw in ['超级管理员', '普通管理员', '普通用户'])
    results.append({
        "text": "covers-rbac: Document mentions three-tier RBAC permission model",
        "passed": has_rbac,
        "evidence": f"Has RBAC tiers: {has_rbac}"
    })

    return results

def grade_eval3(base_path):
    """Eval 3: Create Task Log"""
    results = []

    # 1. task-dir-with-timestamp
    task_base = os.path.join(base_path, 'pin-docs/任务日志/0-未开始')
    task_dirs = []
    if os.path.isdir(task_base):
        task_dirs = [d for d in os.listdir(task_base) if os.path.isdir(os.path.join(task_base, d))]
    # Check for yyMMdd pattern (6+ digits)
    has_timestamp_dir = any(bool(re.match(r'^\d{6,8}-\d{4}', d)) for d in task_dirs)
    results.append({
        "text": "task-dir-with-timestamp: Directory with date-based naming under 0-未开始/",
        "passed": has_timestamp_dir,
        "evidence": f"Task dirs: {task_dirs}, has timestamp pattern: {has_timestamp_dir}"
    })

    # 2. task-md-created
    task_md_path = None
    for d in task_dirs:
        p = os.path.join(task_base, d, 'task.md')
        if os.path.isfile(p):
            task_md_path = p
            break
    task_content = read_file(task_md_path)
    has_oauth = task_content is not None and ('OAuth' in (task_content or '') or '第三方登录' in (task_content or '') or '微信' in (task_content or ''))
    results.append({
        "text": "task-md-created: task.md exists with OAuth2 login description",
        "passed": task_md_path is not None and has_oauth,
        "evidence": f"task.md found: {task_md_path is not None}, has OAuth content: {has_oauth}"
    })

    # 3. plan-md-created
    plan_md_path = None
    for d in task_dirs:
        p = os.path.join(task_base, d, 'plan.md')
        if os.path.isfile(p):
            plan_md_path = p
            break
    plan_content = read_file(plan_md_path)
    has_steps = plan_content is not None and ('技术调研' in (plan_content or '') and '接口开发' in (plan_content or '') and '联调测试' in (plan_content or ''))
    results.append({
        "text": "plan-md-created: plan.md exists with 3 development steps",
        "passed": plan_md_path is not None and has_steps,
        "evidence": f"plan.md found: {plan_md_path is not None}, has 3 steps: {has_steps}"
    })

    # 4. index-updated
    index_path = os.path.join(base_path, 'pin-docs/任务日志/0-索引.md')
    index_content = read_file(index_path)
    index_updated = index_content is not None and ('OAuth' in (index_content or '') or '第三方' in (index_content or '') or '登录' in (index_content or ''))
    results.append({
        "text": "index-updated: 任务日志/0-索引.md references the new task",
        "passed": index_updated,
        "evidence": f"index exists: {index_content is not None}, has task ref: {index_updated}"
    })

    return results

def main():
    workspace = sys.argv[1]  # e.g., pin-workspace/iteration-1

    grading_fns = {
        'eval-1': grade_eval1,
        'eval-2': grade_eval2,
        'eval-3': grade_eval3,
    }

    for eval_dir, grade_fn in grading_fns.items():
        for config in ['with_skill', 'without_skill']:
            output_dir = os.path.join(workspace, eval_dir, config, 'outputs')
            if not os.path.isdir(output_dir):
                # Try to find the actual output dir
                base = os.path.join(workspace, eval_dir, config)
                # Check if outputs are directly in the config dir
                if os.path.isdir(os.path.join(base, 'pin-docs')):
                    output_dir = base
                else:
                    # Walk to find pin-docs
                    for root, dirs, files in os.walk(base):
                        if 'pin-docs' in dirs:
                            output_dir = os.path.join(root, 'pin-docs')
                            # We need the parent of pin-docs
                            output_dir = os.path.join(root)
                            break

            # The base should be the directory containing pin-docs
            # Check if output_dir contains pin-docs
            if os.path.isdir(os.path.join(output_dir, 'pin-docs')):
                base_for_grading = os.path.join(output_dir)
            else:
                # output_dir IS the workspace base
                base_for_grading = output_dir

            results = grade_fn(base_for_grading)

            # Count passes
            passed = sum(1 for r in results if r['passed'])
            total = len(results)

            grading = {
                "expectations": results,
                "summary": {
                    "pass_rate": round(passed / total, 4) if total > 0 else 0,
                    "passed": passed,
                    "total": total
                }
            }

            grading_path = os.path.join(workspace, eval_dir, config, 'grading.json')
            os.makedirs(os.path.dirname(grading_path), exist_ok=True)
            with open(grading_path, 'w', encoding='utf-8') as f:
                json.dump(grading, f, ensure_ascii=False, indent=2)

            print(f"{eval_dir}/{config}: {passed}/{total} passed ({grading['summary']['pass_rate']}%)")

if __name__ == '__main__':
    main()
