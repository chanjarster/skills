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

def find_dir(base_path, *patterns):
    """Find a directory matching any of the given name patterns under base_path."""
    for pattern in patterns:
        full = os.path.join(base_path, pattern)
        if os.path.isdir(full):
            return full
    # Fallback: search for partial match in parent dir
    parent = os.path.dirname(os.path.join(base_path, patterns[0]))
    if os.path.isdir(parent):
        base_name = os.path.basename(patterns[0])
        # Strip -X suffix for matching
        prefix = base_name.split('-')[0] + '-'
        for d in os.listdir(parent):
            if d.startswith(prefix) and os.path.isdir(os.path.join(parent, d)):
                return os.path.join(parent, d)
    return os.path.join(base_path, patterns[0])  # return first pattern as default

def find_file(base_path, *patterns):
    """Find a file matching any of the given path patterns under base_path."""
    for pattern in patterns:
        full = os.path.join(base_path, pattern)
        if os.path.isfile(full):
            return full
    return os.path.join(base_path, patterns[0])  # return first pattern as default

def resolve_tech_dir(base_path):
    """Find the tech docs directory (may be 1-技术文档-X, 1-技术文档, 1-技术文档-前端, etc.)."""
    return find_dir(base_path, 'pin-docs/1-技术文档-X', 'pin-docs/1-技术文档')

def resolve_conv_dir(base_path):
    """Find the convention docs directory."""
    return find_dir(base_path, 'pin-docs/3-规范文档-X', 'pin-docs/3-规范文档')

def grade_eval1(base_path):
    """Eval 1: Project Initialization"""
    results = []

    # 1. top-level-dirs-exist
    pin_docs = os.path.join(base_path, 'pin-docs')
    required_prefixes = ['0-业务知识', '1-技术文档', '2-UI设计', '3-规范文档', '4-任务日志', '5-assets']
    existing_dirs = os.listdir(pin_docs) if os.path.isdir(pin_docs) else []
    found = []
    for prefix in required_prefixes:
        found.append(any(d.startswith(prefix.split('-')[0] + '-') or d == prefix for d in existing_dirs if os.path.isdir(os.path.join(pin_docs, d))))
    all_exist = all(found)
    results.append({
        "text": "top-level-dirs-exist: All 6 numbered top-level directories exist",
        "passed": all_exist,
        "evidence": f"Dirs found: {found}, existing: {existing_dirs}"
    })

    # 2. index-files-created
    tech_dir = resolve_tech_dir(base_path)
    conv_dir = resolve_conv_dir(base_path)
    index_dirs = [
        os.path.join(base_path, 'pin-docs/0-业务知识'),
        tech_dir,
        os.path.join(base_path, 'pin-docs/2-UI设计'),
        conv_dir,
        os.path.join(base_path, 'pin-docs/4-任务日志'),
    ]
    index_exists = [os.path.isfile(os.path.join(d, '0-索引.md')) for d in index_dirs]
    all_exist = all(index_exists)
    results.append({
        "text": "index-files-created: Each top-level directory has a 0-索引.md file",
        "passed": all_exist,
        "evidence": f"Index files exist: {index_exists}"
    })

    # 3. overview-has-education-content
    overview_path = os.path.join(base_path, 'pin-docs/0-业务知识/1-需求文档/0-overview.md')
    overview = read_file(overview_path)
    has_domains = overview is not None and all(kw in overview for kw in ['课程管理', '学员管理', '支付结算', '讲师管理'])
    results.append({
        "text": "overview-has-education-content: 0-overview.md contains the 4 education platform domains",
        "passed": has_domains,
        "evidence": f"overview exists: {overview is not None}, contains domains: {has_domains}"
    })

    # 4. subdirectories-correct
    tech_dir = resolve_tech_dir(base_path)
    conv_dir = resolve_conv_dir(base_path)
    subdirs = [
        os.path.join(base_path, 'pin-docs/0-业务知识/1-需求文档'),
        os.path.join(base_path, 'pin-docs/0-业务知识/2-操作手册'),
        os.path.join(tech_dir, '3-代码地图'),
        os.path.join(tech_dir, '4-研究记录'),
        os.path.join(base_path, 'pin-docs/2-UI设计/1-UI设计图'),
        os.path.join(base_path, 'pin-docs/2-UI设计/2-UI切图'),
        os.path.join(conv_dir, '3-示例'),
    ]
    subdir_exists = [os.path.isdir(d) for d in subdirs]
    all_exist = all(subdir_exists)
    results.append({
        "text": "subdirectories-correct: All required subdirectories exist",
        "passed": all_exist,
        "evidence": f"Subdirs exist: {subdir_exists}"
    })

    # 5. task-status-dirs-exist
    status_dirs = ['pin-docs/4-任务日志/0-未开始', 'pin-docs/4-任务日志/1-进行中', 'pin-docs/4-任务日志/2-已完成']
    all_exist = all(os.path.isdir(os.path.join(base_path, d)) for d in status_dirs)
    results.append({
        "text": "task-status-dirs-exist: 任务日志 has all 3 status subdirs",
        "passed": all_exist,
        "evidence": f"Status dirs exist: {[os.path.isdir(os.path.join(base_path, d)) for d in status_dirs]}"
    })

    return results

def grade_eval2(base_path):
    """Eval 2: Business Requirement and Operation Manual"""
    results = []

    # 1. requirement-file-named-correctly
    req_dir = os.path.join(base_path, 'pin-docs/0-业务知识/1-需求文档')
    if os.path.isdir(req_dir):
        files = [f for f in os.listdir(req_dir) if f.endswith('.md') and f != '0-overview.md']
        doc_exists = len(files) > 0
        doc_path = os.path.join(req_dir, files[0]) if doc_exists else None
        doc_content = read_file(doc_path) if doc_path else None
    else:
        doc_exists = False
        doc_content = None

    has_correct_naming = doc_exists and bool(re.match(r'^\d+-', files[0])) if doc_exists else False
    results.append({
        "text": "requirement-file-named-correctly: Document in 1-需求文档/ with sequential naming",
        "passed": doc_exists and has_correct_naming,
        "evidence": f"Files found: {files if os.path.isdir(req_dir) else 'req_dir not found'}"
    })

    # 2. requirement-has-bounded-context
    has_context = doc_content is not None and any(kw in (doc_content or '') for kw in ['边界上下文', 'Bounded Context', '边界', '范围'])
    results.append({
        "text": "requirement-has-bounded-context: Document contains boundary context section",
        "passed": has_context,
        "evidence": f"Has boundary/context keywords: {has_context}"
    })

    # 3. requirement-has-mermaid-diagram
    has_mermaid_val = has_mermaid(doc_content)
    results.append({
        "text": "requirement-has-mermaid-diagram: Document contains at least one mermaid code block",
        "passed": has_mermaid_val,
        "evidence": f"Has mermaid: {has_mermaid_val}"
    })

    # 4. requirement-covers-refund-audit
    has_refund_audit = doc_content is not None and all(kw in (doc_content or '') for kw in ['退款', '审核'])
    results.append({
        "text": "requirement-covers-refund-audit: Document mentions refund requires admin approval",
        "passed": has_refund_audit,
        "evidence": f"Has refund audit content: {has_refund_audit}"
    })

    # 5. requirement-covers-reconciliation
    has_reconciliation = doc_content is not None and '对账' in (doc_content or '')
    results.append({
        "text": "requirement-covers-reconciliation: Document mentions daily reconciliation",
        "passed": has_reconciliation,
        "evidence": f"Has reconciliation content: {has_reconciliation}"
    })

    # 6. operation-manual-exists
    op_dir = os.path.join(base_path, 'pin-docs/0-业务知识/2-操作手册')
    if os.path.isdir(op_dir):
        op_files = [f for f in os.listdir(op_dir) if f.endswith('.md')]
        op_exists = len(op_files) > 0
    else:
        op_exists = False
    results.append({
        "text": "operation-manual-exists: An operation manual file exists under 2-操作手册/",
        "passed": op_exists,
        "evidence": f"Op manual files: {op_files if os.path.isdir(op_dir) else 'op_dir not found'}"
    })

    # 7. index-updated
    index_path = os.path.join(base_path, 'pin-docs/0-业务知识/0-索引.md')
    index_content = read_file(index_path)
    index_updated = index_content is not None and '支付' in index_content
    results.append({
        "text": "index-updated: 0-索引.md references the payment documents",
        "passed": index_updated,
        "evidence": f"index exists: {index_content is not None}, contains payment ref: {index_updated}"
    })

    return results

def grade_eval3(base_path):
    """Eval 3: Tech Documents Suite"""
    results = []

    # 1. tech-architecture-exists
    tech_dir = resolve_tech_dir(base_path)
    arch_path = os.path.join(tech_dir, '1-技术架构.md')
    arch_exists = os.path.isfile(arch_path)
    arch_content = read_file(arch_path) if arch_exists else None
    results.append({
        "text": "tech-architecture-exists: 1-技术架构.md exists under 1-技术文档-X/",
        "passed": arch_exists,
        "evidence": f"Architecture file exists: {arch_exists}"
    })

    # 2. architecture-has-tech-stack
    has_tech_stack = arch_content is not None and all(kw in (arch_content or '') for kw in ['React', 'NestJS', 'PostgreSQL'])
    results.append({
        "text": "architecture-has-tech-stack: The architecture doc mentions React, NestJS, PostgreSQL",
        "passed": has_tech_stack,
        "evidence": f"Has tech stack keywords: {has_tech_stack}"
    })

    # 3. architecture-has-mermaid-diagram
    has_mermaid_val = has_mermaid(arch_content)
    results.append({
        "text": "architecture-has-mermaid-diagram: The architecture doc contains at least one mermaid diagram",
        "passed": has_mermaid_val,
        "evidence": f"Has mermaid: {has_mermaid_val}"
    })

    # 4. dev-environment-exists
    dev_env_path = os.path.join(tech_dir, '2-开发环境.md')
    dev_env_exists = os.path.isfile(dev_env_path)
    dev_env_content = read_file(dev_env_path) if dev_env_exists else None
    results.append({
        "text": "dev-environment-exists: 2-开发环境.md exists under 1-技术文档-X/",
        "passed": dev_env_exists,
        "evidence": f"Dev env file exists: {dev_env_exists}"
    })

    # 5. dev-env-has-startup-steps
    has_startup = dev_env_content is not None and ('npm run dev' in (dev_env_content or '') or '启动' in (dev_env_content or ''))
    results.append({
        "text": "dev-env-has-startup-steps: The dev environment doc contains startup instructions",
        "passed": has_startup,
        "evidence": f"Has startup instructions: {has_startup}"
    })

    # 6. code-map-exists
    code_map_dir = os.path.join(tech_dir, '3-代码地图')
    if os.path.isdir(code_map_dir):
        code_map_files = [f for f in os.listdir(code_map_dir) if f.endswith('.md')]
        code_map_exists = len(code_map_files) > 0
        code_map_path = os.path.join(code_map_dir, code_map_files[0]) if code_map_exists else None
        code_map_content = read_file(code_map_path) if code_map_path else None
    else:
        code_map_exists = False
        code_map_content = None
    results.append({
        "text": "code-map-exists: A code map file exists under 3-代码地图/",
        "passed": code_map_exists,
        "evidence": f"Code map files: {code_map_files if os.path.isdir(code_map_dir) else 'code_map_dir not found'}"
    })

    # 7. code-map-has-api-list
    has_api_list = code_map_content is not None and ('API' in (code_map_content or '') or 'API清单' in (code_map_content or ''))
    results.append({
        "text": "code-map-has-api-list: The code map contains an API list section",
        "passed": has_api_list,
        "evidence": f"Has API list: {has_api_list}"
    })

    # 8. tech-index-updated
    index_path = os.path.join(tech_dir, '0-索引.md')
    index_content = read_file(index_path)
    index_updated = index_content is not None and ('技术架构' in index_content or '开发环境' in index_content)
    results.append({
        "text": "tech-index-updated: 0-索引.md references the created documents",
        "passed": index_updated,
        "evidence": f"index exists: {index_content is not None}, has refs: {index_updated}"
    })

    return results

def grade_eval4(base_path):
    """Eval 4: Task Creation and Lifecycle"""
    results = []

    # 1. task-directory-timestamp
    task_base = os.path.join(base_path, 'pin-docs/4-任务日志/0-未开始')
    task_dirs = []
    if os.path.isdir(task_base):
        task_dirs = [d for d in os.listdir(task_base) if os.path.isdir(os.path.join(task_base, d))]
    has_timestamp_dir = any(bool(re.match(r'^\d{6,8}-\d{4}', d)) for d in task_dirs)
    results.append({
        "text": "task-directory-timestamp: Directory with date-based naming under 0-未开始/",
        "passed": has_timestamp_dir,
        "evidence": f"Task dirs: {task_dirs}, has timestamp pattern: {has_timestamp_dir}"
    })

    # 2. task-md-exists
    task_md_path = None
    for d in task_dirs:
        p = os.path.join(task_base, d, 'task.md')
        if os.path.isfile(p):
            task_md_path = p
            break
    task_content = read_file(task_md_path) if task_md_path else None
    has_search = task_content is not None and ('搜索' in (task_content or '') or '课程' in (task_content or ''))
    results.append({
        "text": "task-md-exists: task.md exists and describes course search functionality",
        "passed": task_md_path is not None and has_search,
        "evidence": f"task.md found: {task_md_path is not None}, has search content: {has_search}"
    })

    # 3. task-has-acceptance-criteria
    has_criteria = task_content is not None and ('- [ ]' in (task_content or '') or '验收标准' in (task_content or ''))
    results.append({
        "text": "task-has-acceptance-criteria: task.md contains acceptance criteria with checkboxes",
        "passed": has_criteria,
        "evidence": f"Has acceptance criteria: {has_criteria}"
    })

    # 4. plan-md-exists
    plan_md_path = None
    for d in task_dirs:
        p = os.path.join(task_base, d, 'plan.md')
        if os.path.isfile(p):
            plan_md_path = p
            break
    plan_content = read_file(plan_md_path) if plan_md_path else None
    has_steps = plan_content is not None and all(kw in (plan_content or '') for kw in ['API', '前端', '优化'])
    results.append({
        "text": "plan-md-exists: plan.md exists with 3 development steps",
        "passed": plan_md_path is not None and has_steps,
        "evidence": f"plan.md found: {plan_md_path is not None}, has 3 steps: {has_steps}"
    })

    # 5. task-index-updated
    index_path = os.path.join(base_path, 'pin-docs/4-任务日志/0-索引.md')
    index_content = read_file(index_path)
    index_updated = index_content is not None and ('搜索' in index_content or '课程' in index_content)
    results.append({
        "text": "task-index-updated: 0-索引.md references the course search task",
        "passed": index_updated,
        "evidence": f"index exists: {index_content is not None}, has task ref: {index_updated}"
    })

    return results

def grade_eval5(base_path):
    """Eval 5: Convention and Research"""
    results = []

    # 1. git-convention-exists
    conv_dir = resolve_conv_dir(base_path)
    git_conv_path = os.path.join(conv_dir, '1-git规范.md')
    git_conv_exists = os.path.isfile(git_conv_path)
    git_conv_content = read_file(git_conv_path) if git_conv_exists else None
    results.append({
        "text": "git-convention-exists: 1-git规范.md exists under 3-规范文档-X/",
        "passed": git_conv_exists,
        "evidence": f"Git convention exists: {git_conv_exists}"
    })

    # 2. git-has-branch-model
    has_branch_model = git_conv_content is not None and any(kw in (git_conv_content or '') for kw in ['Git Flow', 'main', 'develop', 'feature', '分支'])
    results.append({
        "text": "git-has-branch-model: The git convention mentions Git Flow or branch model",
        "passed": has_branch_model,
        "evidence": f"Has branch model: {has_branch_model}"
    })

    # 3. git-has-mermaid-diagram
    has_mermaid_val = has_mermaid(git_conv_content)
    results.append({
        "text": "git-has-mermaid-diagram: The git convention contains at least one mermaid diagram",
        "passed": has_mermaid_val,
        "evidence": f"Has mermaid: {has_mermaid_val}"
    })

    # 4. code-convention-exists
    code_conv_path = os.path.join(conv_dir, '2-代码规范.md')
    code_conv_exists = os.path.isfile(code_conv_path)
    code_conv_content = read_file(code_conv_path) if code_conv_exists else None
    results.append({
        "text": "code-convention-exists: 2-代码规范.md exists under 3-规范文档-X/",
        "passed": code_conv_exists,
        "evidence": f"Code convention exists: {code_conv_exists}"
    })

    # 5. code-convention-has-naming
    has_naming = code_conv_content is not None and any(kw in (code_conv_content or '') for kw in ['camelCase', 'PascalCase', '命名'])
    results.append({
        "text": "code-convention-has-naming: The code convention mentions naming conventions",
        "passed": has_naming,
        "evidence": f"Has naming conventions: {has_naming}"
    })

    # 6. research-record-exists
    tech_dir = resolve_tech_dir(base_path)
    research_dir = os.path.join(tech_dir, '4-研究记录')
    research_exists = False
    research_content = None
    if os.path.isdir(research_dir):
        subdirs = [d for d in os.listdir(research_dir) if os.path.isdir(os.path.join(research_dir, d))]
        for subdir in subdirs:
            research_md = os.path.join(research_dir, subdir, 'research.md')
            if os.path.isfile(research_md):
                research_exists = True
                research_content = read_file(research_md)
                break
    results.append({
        "text": "research-record-exists: A research directory exists under 4-研究记录/ containing research.md",
        "passed": research_exists,
        "evidence": f"Research exists: {research_exists}"
    })

    # 7. research-has-findings
    has_findings = research_content is not None and any(kw in (research_content or '') for kw in ['Next.js', 'App Router', '结论', '发现'])
    results.append({
        "text": "research-has-findings: The research document contains core findings about Next.js routers",
        "passed": has_findings,
        "evidence": f"Has findings: {has_findings}"
    })

    # 8. convention-index-updated
    conv_index_path = os.path.join(conv_dir, '0-索引.md')
    conv_index_content = read_file(conv_index_path)
    conv_index_updated = conv_index_content is not None and ('git' in conv_index_content.lower() or '代码' in conv_index_content)
    results.append({
        "text": "convention-index-updated: 0-索引.md references the convention documents",
        "passed": conv_index_updated,
        "evidence": f"index exists: {conv_index_content is not None}, has refs: {conv_index_updated}"
    })

    # 9. research-index-updated
    tech_index_path = os.path.join(tech_dir, '0-索引.md')
    tech_index_content = read_file(tech_index_path)
    research_index_updated = tech_index_content is not None and ('研究' in tech_index_content or 'Next.js' in tech_index_content)
    results.append({
        "text": "research-index-updated: 0-索引.md references the research record",
        "passed": research_index_updated,
        "evidence": f"index exists: {tech_index_content is not None}, has research ref: {research_index_updated}"
    })

    return results

def main():
    workspace = sys.argv[1]  # e.g., pin-workspace/iteration-1

    grading_fns = {
        'eval-1': grade_eval1,
        'eval-2': grade_eval2,
        'eval-3': grade_eval3,
        'eval-4': grade_eval4,
        'eval-5': grade_eval5,
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
