# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is the **pin** skill — a Claude Code skill for managing project intelligence documents (Project Information). It defines a standardized `pin-docs/` directory structure and workflow for organizing project knowledge across five dimensions: business knowledge, technical docs, UI design, conventions, and task logs.

The skill lives alongside other skills in the `chanjarster-skills` repo. This directory (`skills/pin/`) is the skill root.

## Quick Start

### Running Evaluations

```bash
python scripts/grade.py pin-workspace/iteration-N
```

Each evaluation runs eval prompts (defined in `evals/evals.json`) with and without the skill, grading output quality, time, and token usage. Results are aggregated in `pin-workspace/iteration-N/benchmark.json` and `benchmark.md`.

### Skill Definition

- **Entry point**: `SKILL.md` — skill metadata and instructions for Claude Code
- **Full spec**: `references/idea.md` — complete pin-docs directory structure specification
- **Design doc**: `IDEA.md` — extended document structure reference

## Architecture

### Core Design Principles

1. **Index-first navigation**: Every directory has a `0-索引.md` index file. AI should read indexes to locate documents rather than scanning directories — this is a context window optimization.

2. **Template system**: 14 standardized templates in `references/templates/` cover all document types. Each uses YAML frontmatter with `type:` declaration and `{placeholder}` syntax for variable content.

3. **DDD for business docs**: Business requirements are organized by bounded contexts — one file per domain, with consistent naming across requirement docs, operation manuals, and code maps.

4. **AI-oriented content**: Documents should only contain information that cannot be derived from code itself. Exclude: property names, method signatures, detailed logic obvious from source.

5. **Three-state task lifecycle**: Tasks flow through `0-未开始` → `1-进行中` → `2-已完成` as directory moves.

### Directory Structure

```
pin/
├── SKILL.md                 # Skill entry point
├── IDEA.md                  # Design doc
├── evals/evals.json         # 6 eval test cases
├── references/
│   ├── idea.md              # Full pin-docs spec
│   └── templates/           # 14 document templates
├── pin-docs/                # Live docs (self-dogfooding)
├── pin-workspace/           # Eval results (with_skill vs without_skill)
└── scripts/                 # Python grading scripts
```

### Key Templates

| Template | Purpose |
|---|---|
| `overview.md` | Project overview |
| `requirement.md` | Business requirement |
| `tech-architecture.md` | Technical architecture |
| `dev-environment.md` | Dev environment setup |
| `code-map.md` | Code structure map |
| `research.md` | Research record |
| `task.md` / `plan.md` / `result.md` | Task lifecycle docs |
| `git-convention.md` / `code-convention.md` | Conventions |
| `index-template.md` | Directory index |

## Tech Stack

- **Format**: Markdown with YAML frontmatter
- **Diagrams**: Mermaid (flowcharts, graphs)
- **Evaluation**: Python 3 grading scripts
- **Language**: Mixed Chinese/English — templates and instructions in Chinese, eval assertions in English
