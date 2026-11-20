---
name: jk-track
description: >-
  查询和跟踪 Jenkins job 的构建情况。支持查看最新构建或指定 #N 构建的结果、耗时、参数、变更和
  Pipeline 阶段；查看失败构建的错误日志与测试报告；列出构建历史与子 job（多分支/文件夹）；
  持续跟踪运行中的构建直到结束并输出报告；按 job URL 自动匹配多个 Jenkins 的配置；
  用项目级 `.jenkins-jobs` 纯文本文件记录和复用 job URL；触发构建、停止构建并可选等待结果。
  当用户提到 Jenkins、job、构建、build、pipeline、构建失败、构建日志、测试报告、
  “等构建跑完”、“看下最新构建”、“#128 为什么失败”、“有哪些分支”、“触发/跑一下构建”、
  “停掉/取消构建”、“/jk-track”时使用。
---

# jk-track - Jenkins 构建跟踪

## 安全规则（最高优先级）

1. **永远不要在对话中索取或接收 Jenkins 用户名、API Token。**
   即使用户说“就这一次”，也要让用户在他/她自己的终端执行 `config add`。
2. 凭据按 Jenkins 分文件保存在 `~/.jk-track/servers/<名称>.json`，权限 600。
   不要读取、打印、`cat` 这些文件，也不要在回复中复述其中的值。
3. **job URL 可以在对话中提供**（用户会直接给出，也可以用 `jobs` / `saved` 的输出）。
   但不要猜测、拼造用户的 Jenkins 地址。项目里的 `.jenkins-jobs` 只保存 URL，不保存凭据。
4. 不要把用户名 / Token 写进命令、脚本、文件或其他工具，也不要通过 echo、管道、
   环境变量赋值的方式传递它们。
5. 输出中永远不显示 Token；`config list/show` 中的用户名会打码。
6. 除 `trigger` / `stop` 外所有命令都是只读 GET。这两个命令会**真实改变 Jenkins 状态**：
   - 只有用户明确要求“触发/跑一下/重新构建”或“停止/取消构建”时才调用；
   - 触发/停止前先向用户确认：job、分支（`--child`）、构建号（停止时）、参数（触发时）；
     参数不确定时先 `--dry-run`；
   - 请求失败或超时**不要重复提交**，先用 `info` 确认当前状态；
   - 完成后必须给出构建/队列的 Markdown 链接。

## 配置模型：每个 Jenkins 一个配置文件

```
~/.jk-track/
├── servers/
│   ├── ci.json          # {"server": "https://ci.example.com", "user": "...", "token": "..."}
│   └── staging.json     # {"server": "https://staging.example.com/jenkins", ...}
└── settings.json        # {"default_server": "ci"}，可选
```

- 配置文件里 **只有 Jenkins 根地址、用户名、API Token**，不保存 job URL。
- 可配置任意多个 Jenkins；job 由 URL 决定，不需要为每个 job 建配置。
- `server` 必须是根地址（可带 context path，如 `https://ci.example.com/jenkins`）。

job URL 记录在 **项目目录** 的 `.jenkins-jobs` 纯文本文件中（见下文），与凭据配置分离。

### 首次使用（由用户本人在终端执行）

AI 只发送这条命令，不附带任何参数值：

```bash
python <skill_dir>/scripts/jk.py config add ci
```

交互输入：配置名称、Jenkins 根地址、用户名、API Token（不回显）。

**AI 的判断流程：**

1. 先运行 `config list`（安全：token 不显示，用户名打码），了解已配置哪些 Jenkins。
2. 没有配置 → 告诉用户“请在你自己的终端运行 `config add <名称>`”，等待用户确认完成。
3. 有配置 → 直接用用户提供的 job URL 查询，脚本会自动匹配配置。
4. job URL 不属于任何已配置 Jenkins → 把报错里的配置列表和缺失情况告诉用户，
   请用户自己执行 `config add` 添加对应 Jenkins，不要索要用户名或 Token。

## 配置命令（由用户本人在终端执行）

| 命令                        | 说明                                             |
| --------------------------- | ------------------------------------------------ |
| `config add [<名称>]`       | 交互式新增或修改一个 Jenkins 配置（`set` 是别名）|
| `config list`               | 列出所有配置：名称、根地址、用户名（打码）、Token 状态 |
| `config show [<名称>]`      | 查看某个配置的详情（Token 不显示）               |
| `config use <名称>`         | 设为默认配置（仅用于非完整 URL 的 job 名称）     |
| `config remove <名称>`      | 删除配置                                         |

## 项目级 job 列表：`.jenkins-jobs`

- **位置**：当前项目目录下的 `.jenkins-jobs`。脚本从当前目录向上查找；可用 `--jobs-file <路径>`
  或环境变量 `JK_JOBS_FILE` 覆盖。
- **格式**：普通文本，每行一个 job URL，`#` 开头为注释。

```text
# jk-track: 项目使用的 Jenkins job URL（每行一个，可用 # 注释）
https://ci.example.com/job/my-app/
https://ci.example.com/job/my-app/job/main/
```

- **自动记录**：每次查询成功（包括 `watch` / `trigger` / `stop` 的退出码 0/1/3）后，把本次最终 job URL 追加进文件，
  自动去重；`--no-save` 可关闭。用了 `--child` 时记录解析后的子 job URL。
- **选择 job**：`--job` 支持完整 URL、序号（`1` 或 `#1`）、短名称（URL 最后一段，如 `main`）；
  文件里只有一个 URL 时可以省略 `--job`。
- **查看**：`saved` 命令（别名 `urls`）列出序号、job 名称、URL 和匹配到的 server。
- 文件只包含 URL，不包含用户名或 Token；是否提交到仓库由用户决定，内部地址可加入 `.gitignore`。

```bash
# 列出项目已记录的 job
python <skill_dir>/scripts/jk.py saved

# 用序号 / 短名称 / 完整 URL 均可
python <skill_dir>/scripts/jk.py info --job 1
python <skill_dir>/scripts/jk.py info --job main --build 128
```

## job URL 如何匹配配置

脚本按 job URL 自动选择配置：

1. 比较 `scheme + host + 端口`，必须与某个配置的根地址一致。
2. 根地址带 context path 时（如 `/jenkins`），job URL 的路径必须在其下。
3. 同一台 Jenkins 配置了多个 context path 时，取路径最长（最具体）的那个。
4. 仍无法唯一确定 → 报错，要求用 `--server <名称>` 指定。
5. 没有任何配置匹配 → 报错并列出已有配置，**不会把凭据发给未匹配的主机**。

也可以显式指定：

```bash
# job 只给名称时，用 --server 指定配置（此时把名称拼到根地址后）
python <skill_dir>/scripts/jk.py info --job my-app --server ci

# 同一台 Jenkins 有多个配置时，明确选择
python <skill_dir>/scripts/jk.py info --job '<JOB_URL>' --server ci
```

`--job` 的几种写法：

| 写法                                       | 说明                                   |
| ------------------------------------------ | -------------------------------------- |
| `https://ci.example.com/job/my-app/`       | 推荐，直接给 job 页面 URL              |
| `https://ci.example.com/job/my-app/128/`   | 构建页 URL，会自动归一化为 job URL     |
| `https://ci.example.com/job/my-app/128/consoleText` | 其他子页面也可以                |
| `my-app` / `folder/my-app`                 | 需要 `--server` 或已 `config use` 默认 |

## 命令速查

```bash
python <skill_dir>/scripts/jk.py <COMMAND> --job '<JOB_URL>'
```

| 命令      | 用途                                                   | 关键参数                                                        |
| --------- | ------------------------------------------------------ | --------------------------------------------------------------- |
| `info`    | 构建概况（默认最新构建）：结果、耗时、触发、参数、变更、阶段、产物 | `--build N`、`--no-stages`、`--json`                      |
| `history` | 构建历史：默认最近 10 次，`--all` 列出全部构建         | `--limit 10`、`--all`                                           |
| `jobs`    | 列出子 job（文件夹/多分支 job 的分支），含各自最新构建 | `--depth 2`、`--json`                                           |
| `log`     | 构建日志                                               | `--tail 80`、`--grep ERROR`、`-A 3`、`-i`、`--save <文件>`     |
| `tests`   | 测试报告：统计 + 失败用例                              | `--max-failures 20`、`--error-lines 8`                          |
| `failure` | 失败汇总：失败阶段 + 测试报告 + 日志错误摘录 + 日志尾部 | `--max-log-blocks 5`、`--log-tail 30`、`--max-failures 10`     |
| `watch`   | 持续跟踪构建直到结束，结束时输出报告                   | `--next`、`--build N`、`--interval 15`、`--timeout 3600`、`--quiet` |
| `trigger` | 触发一次构建（可带参数、可选等待结果）                 | `--param K=V`、`--child`、`--wait`、`--dry-run`、`--queue-timeout 300`、`--watch-timeout 3600` |
| `stop`    | 停止构建（默认最新构建）                               | `--build N`、`--wait`、`--dry-run`、`--watch-timeout 3600`、`--quiet` |
| `saved`   | 列出项目 `.jenkins-jobs` 中记录的 job（别名 `urls`）    | `--jobs-file`、`--json`                                         |

`--build` 支持：编号（`128` 或 `#128`）、`latest`（默认）、`lastCompleted`、`lastFailed`、`lastSuccessful`。

通用参数：`--job`、`--server`、`--child`、`--jobs-file`、`--no-save`、`--insecure`（自签名证书）、
`--http-timeout 30`、`--json`、`--verbose`、`--no-color`。

**`--child` 与子 job URL**：`jobs` 会输出每个子 job 的 URL，可以直接拿 URL 查；也可以保留容器 job
的 URL 并加 `--child <子 job 名称>`。分支名含 `/`（如 `feature/login`）时整体传入。

## 工作流程

### 1. 判断用户意图

| 用户说的话                                   | 执行                                                         |
| -------------------------------------------- | ------------------------------------------------------------ |
| “最新构建怎么样 / 成功了吗”                  | `info --job <URL>`                                            |
| “#128 什么情况 / 这个构建成功没”             | `info --job <URL> --build 128`                                |
| “有哪些构建 / 全部构建列出来 / 构建历史”     | `history --job <URL>` 或 `history --job <URL> --all`          |
| “这个 job 有哪些分支 / 子 job / 选一个看”    | `jobs --job <容器 URL>`（目录层级深时加 `--depth 2`）         |
| “项目里记录过哪些 job / 之前看过哪个”         | `saved`（别名 `urls`）                                         |
| “为什么失败 / 失败原因 / 报什么错”           | `failure --job <URL> --build N`（不带则最新构建）             |
| “看下测试报告 / 哪些用例挂了”                | `tests --job <URL> --build N`                                 |
| “看下日志 / 搜一下日志里的 xxx”              | `log --job <URL> --build N --grep xxx`                        |
| “等构建跑完 / 盯着这个构建 / 跑完告诉我结果” | `watch --job <URL>`（刚触发的新构建用 `--next`）              |
| “帮我跑一下 / 触发一次构建 / 重新构建”       | `trigger --job <URL>`（带参数用 `--param`；要等结果加 `--wait`） |
| “停掉 / 停止 / 取消这个构建”                 | `stop --job <URL> --build <编号>`（不带编号则停最新构建）     |

### 2. 执行并解读

- 先跑命令，再用自然语言总结给用户，不要直接粘贴整段原始输出。
- 总结包含：构建号、结果、耗时、失败阶段（如有）、失败用例数（如有）、关键错误行。
- **必须在回答里附上 Jenkins 链接**，方便用户直接点开：
  - 看的是某次构建 → 给构建 URL，如 `https://ci.example.com/job/my-app/128/`；
  - 看的是 job 概况/最新构建 → 给最新构建的 URL；
  - 需要用户选子 job / 构建历史 → 给每个候选的 URL，或给 job URL 并说明构建链接是 `<job URL>/<编号>/`；
  - 用完整 URL，不要缩写、不要只写 job 名。
- **链接必须写成 Markdown link**，否则在对话里不可点击；不要放进行内代码或代码块：

| 场景 | 推荐写法 |
| --- | --- |
| 某次构建 | [#128 构建详情](https://ci.example.com/job/my-app/128/) |
| 最新构建 | [最新构建 #129](https://ci.example.com/job/my-app/129/) |
| job 主页 | [my-app](https://ci.example.com/job/my-app/) |
| 子 job | - [main](https://ci.example.com/job/my-app/job/main/) |
| 多个构建 | - [#128](https://ci.example.com/job/my-app/128/) 失败 ❌ 4分12秒 |
- 每次成功查询后，脚本会自动把 job URL 记到项目 `.jenkins-jobs`；之后的会话可以先 `saved`
  再按序号/短名称选择，不用用户重复回忆 URL。
- `SUCCESS` = 成功；`UNSTABLE` = 有失败测试但未中断；`FAILURE` = 失败；`ABORTED` = 被中止。
- 阶段信息（`wfapi/describe`）只有 Pipeline job 才有；Freestyle job 不显示阶段，属正常情况。
- 测试报告只有 job 配置了测试结果发布（如 JUnit）才存在，否则提示用户看日志。

## 典型场景

### 查看最新构建

```bash
python <skill_dir>/scripts/jk.py info --job 'https://ci.example.com/job/my-app/'
```

首次查询后该 URL 会记录到项目 `.jenkins-jobs`；之后可直接用序号或短名称（只有一个时甚至可省略 `--job`）：

```bash
python <skill_dir>/scripts/jk.py info --job 1
python <skill_dir>/scripts/jk.py info --job my-app
```

### 查看指定构建

```bash
python <skill_dir>/scripts/jk.py info --job 'https://ci.example.com/job/my-app/' --build 128
```

### 列出构建历史，选择要看的构建

```bash
# 默认最近 10 次，最新的一次会标记为“最新”
python <skill_dir>/scripts/jk.py history --job '<JOB_URL>'

# 列出全部构建（构建很多时输出较长，建议先默认查最近几次）
python <skill_dir>/scripts/jk.py history --job '<JOB_URL>' --all
```

用户选中某个构建后，用 `info --build <编号>` 查看概况，再按需用 `failure` / `tests` / `log`。
不指定 `--build` 时所有命令都默认作用于最新构建。

### 子 job（多分支/文件夹 job）

```bash
# 列出子 job 及其最新构建（默认只展开一层）
python <skill_dir>/scripts/jk.py jobs --job 'https://ci.example.com/job/my-app/'

# 目录嵌套较深时递归到第二层
python <skill_dir>/scripts/jk.py jobs --job 'https://ci.example.com/job/my-app/' --depth 2
```

```
Job: my-app（容器 job，共 3 个子 job，server: ci）

子 job       最新构建   开始时间                    结果      URL
├─ main      #128      2026-11-20 11:22:33 +0800   成功 ✅   https://ci.example.com/job/my-app/job/main/
├─ dev       #42       2026-11-19 09:10:00 +0800   失败 ❌   https://ci.example.com/job/my-app/job/dev/
└─ feature/x -         -                           -         https://ci.example.com/job/my-app/job/feature%2Fx/
```

1. 把列表展示给用户，让用户选择子 job（不要自行猜测）。
2. 直接用子 job 的 URL 执行后续命令，或保留容器 URL 加 `--child`：

```bash
python <skill_dir>/scripts/jk.py info --job 'https://ci.example.com/job/my-app/job/main/'
python <skill_dir>/scripts/jk.py failure --job 'https://ci.example.com/job/my-app/' --child dev --build 42
python <skill_dir>/scripts/jk.py watch --job 'https://ci.example.com/job/my-app/' --child feature/login --next
```

如果直接把容器 URL 用于 `info`/`history` 等命令，脚本会报“容器 job”并列出子 job 名称和 URL。

### 分析失败构建

```bash
python <skill_dir>/scripts/jk.py failure --job '<JOB_URL>' --build 128
```

输出结构：构建概况 → 失败阶段 → 测试统计与失败用例 → 日志错误摘录（带行号）→ 日志尾部。

需要更多细节时按需追加：

```bash
# 完整日志中的某类错误（带上下文）
python <skill_dir>/scripts/jk.py log --job '<JOB_URL>' --build 128 --grep 'Caused by' -A 5

# 保存完整日志后自行分析
python <skill_dir>/scripts/jk.py log --job '<JOB_URL>' --build 128 --tail 0 --save /tmp/build-128.log
```

### 等待构建结束并拿报告

```bash
# 场景 A：刚触发了新构建，等它跑完
python <skill_dir>/scripts/jk.py watch --job '<JOB_URL>' --next

# 场景 B：跟踪当前正在运行的构建（编号已知）
python <skill_dir>/scripts/jk.py watch --job '<JOB_URL>' --build 128

# 场景 C：不指定编号，跟踪最新构建；最新构建已结束时直接输出报告
python <skill_dir>/scripts/jk.py watch --job '<JOB_URL>'
```

行为说明：

- 进度行输出到 **stderr**，最终报告输出到 **stdout**，两者可分开处理。
- 构建失败时，`watch` 自动附带与 `failure` 相同的失败报告；`--no-report` 可关闭。
- 构建成功时只输出构建概况。
- 默认每 15 秒轮询一次，最长等待 1 小时；`--timeout 0` 表示不限制。

**长构建的执行方式（重要）：**

1. 调用终端工具时把超时调大（例如 30 分钟），或
2. 后台运行命令，再周期性查看输出。

推荐后台方式，避免阻塞会话：

```bash
python <skill_dir>/scripts/jk.py watch --job '<JOB_URL>' --next --quiet > /tmp/jk-watch.log 2>&1
```

随后查看 `/tmp/jk-watch.log`，看到“构建结束”即完成。等待超时（退出码 3）时**不要重新触发构建**，
用 `watch --build <N>` 继续跟踪即可。

### 触发构建

```bash
# 触发一次构建
python <skill_dir>/scripts/jk.py trigger --job 'https://ci.example.com/job/my-app/'

# 带参数触发（有参数时使用 buildWithParameters）
python <skill_dir>/scripts/jk.py trigger --job '<JOB_URL>' \
  --param BRANCH=main --param DEPLOY=true

# 多分支：触发某个分支
python <skill_dir>/scripts/jk.py trigger --job 'https://ci.example.com/job/my-app/' --child main

# 只预览请求，不真正触发
python <skill_dir>/scripts/jk.py trigger --job '<JOB_URL>' --param A=1 --dry-run

# 触发并等待构建结束，结束后输出报告
python <skill_dir>/scripts/jk.py trigger --job '<JOB_URL>' --wait
```

输出：

```text
已触发: my-app
队列: https://ci.example.com/queue/item/1234/
构建: #128  https://ci.example.com/job/my-app/128/
```

行为说明：

- 无参数 → POST `.../build`；有 `--param` → POST `.../buildWithParameters`；
- 自动读取 CSRF crumb；**POST 不自动重试**，避免重复构建；
- 默认最多等 300 秒让构建离开队列（`--queue-timeout`）；`--wait` 后再最多等 3600 秒结束（`--watch-timeout`）；
- 触发成功后 job URL 同样记录到项目 `.jenkins-jobs`；
- 403/401 通常是凭据或构建权限问题；400 通常是参数与 job 定义不匹配。

**触发安全流程（AI 必须遵守）：**

1. 先向用户确认 job、分支、参数，不要把猜测的参数直接提交；
2. 参数较多或不确定时，先 `--dry-run` 把将要提交的请求展示给用户；
3. 用户确认后才实际触发；
4. 触发超时或网络错误**不要重试**，先用 `info` 确认是否已经产生新构建；
5. 结果中给构建/队列的 Markdown 链接，不要给裸 URL。

### 停止构建

```bash
# 停止某个构建
python <skill_dir>/scripts/jk.py stop --job '<JOB_URL>' --build 131

# 不带 --build 时停止最新构建（通常是正在运行的那个）
python <skill_dir>/scripts/jk.py stop --job '<JOB_URL>'

# 只预览请求，不真正停止
python <skill_dir>/scripts/jk.py stop --job '<JOB_URL>' --build 131 --dry-run

# 停止并等待真正结束（会输出报告）
python <skill_dir>/scripts/jk.py stop --job '<JOB_URL>' --build 131 --wait
```

输出：

```text
已发送停止请求: my-app #131
构建: https://ci.example.com/job/my-app/131/
```

行为说明：

- 构建已结束：提示“无需停止”，退出码 0，并给出构建 URL；
- 构建仍在队列中（还没有构建号）：无法用 `stop` 取消，说明情况并给出 job URL；
- `--wait` 会等到构建真正结束（结果通常是 `ABORTED`），退出码 1（与 `watch` 一致）；等待超时退出码 3；
- `/stop` 返回 404 时会回退尝试 `/kill`，兼容旧版本 Jenkins；
- 停止是写操作，只应在用户明确要求时执行；**不要停止用户没提到的构建**；涉及部署中/他人共享的构建时先确认。

## 多 Jenkins 环境

```bash
# 用户在自己终端配置两套 Jenkins
python <skill_dir>/scripts/jk.py config add ci
python <skill_dir>/scripts/jk.py config add staging

# 之后 job URL 直接决定用哪套凭据，互不影响
python <skill_dir>/scripts/jk.py info --job 'https://ci.example.com/job/my-app/'
python <skill_dir>/scripts/jk.py info --job 'https://staging.example.com/job/my-app/'
```

- 新增 Jenkins 只需加一个配置文件，不需要为每个 job 建配置。
- 同一台 Jenkins 多个 context path（如 `/jenkins`、`/jenkins-dev`）时，按最长路径自动匹配。
- 同一台 Jenkins 配置了相同根地址的两套凭据 → 报“同时匹配多个”，用 `--server` 指定。

## 退出码

| 退出码 | 含义                                             |
| ------ | ------------------------------------------------ |
| 0      | 查询成功；`watch` 结束时构建结果为 `SUCCESS`      |
| 1      | `watch` / `trigger --wait` / `stop --wait` 结束时构建结果不是 `SUCCESS` |
| 2      | 参数、凭据、匹配失败、网络或 HTTP 错误           |
| 3      | `watch` / `trigger` / `stop --wait` 等待超时，构建仍在运行 |
| 130    | 用户中断（构建在 Jenkins 上继续执行）            |

`info`、`history`、`jobs`、`log`、`tests`、`failure` 只做信息展示，构建失败也返回 0；失败与否看输出内容或 `--json` 中的 `result`。

## JSON 输出

任何命令加 `--json` 都会输出结构化结果，便于进一步处理：

```bash
python <skill_dir>/scripts/jk.py info --job '<JOB_URL>' --json
python <skill_dir>/scripts/jk.py failure --job '<JOB_URL>' --json
python <skill_dir>/scripts/jk.py tests --job '<JOB_URL>' --json
```

主要字段：`job.name`、`number`、`result`、`building`、`duration_ms`、`parameters`、`changes`、
`stages[]`、`tests.{total,fail,pass,skip,failed[]}`、`log_errors[].{line,text}`。
`failure` 还包含 `failed_stages[]`、`exit_codes[]`、`log_tail[]`；`watch --json` 返回
`{"build": ..., "failure_report": ...}`；`history --json` 返回 `{job, total, builds[]}`；
`jobs --json` 返回 `{job, server, children[]}`；`saved --json` 返回 `{file, jobs[]}`；
`trigger --json` 返回 `{job, job_url, queue, build:{number,url}}`（加 `--wait` 时返回 watch 风格的结果 JSON）；
`stop --json` 返回 `{job, number, url, stopped, http_status}`（已结束时 `stopped:false`、`reason:"already finished"`）。
JSON 中不会包含 Token。

## 故障排查

| 现象                                    | 处理                                                                                     |
| --------------------------------------- | ---------------------------------------------------------------------------------------- |
| “缺少 --job”                            | `.jenkins-jobs` 为空或多个 URL；先 `saved` 看列表，再用 `--job <序号|短名称|URL>` 选择     |
| 触发返回 403/401                        | 凭据无构建权限或 CSRF 校验失败；不要盲目重试，确认权限后再试                                |
| 触发返回 400                            | 参数与 job 定义不匹配；检查 `--param` 名称，参数化 job 不存在的参数会报错                  |
| 触发后看不到新构建                      | 构建可能还在队列；用输出里的队列链接或 `--wait`；不要直接重复触发                           |
| 停止返回 403/404                        | 无停止权限，或构建已结束/被清理；先用 `info` 确认构建状态，不要重复提交                     |
| 构建在队列中无法停止                    | 尚未生成构建号，`stop` 不适用；到 Jenkins 队列页面取消，或稍后再试                         |
| “序号超出范围”                          | `saved` 查看实际序号；文件是纯文本，可直接编辑调整顺序                                      |
| `.jenkins-jobs` 无法写入                | 目录无写权限；用 `--jobs-file <可写路径>` 指定，或先 `saved` 手动维护                     |
| “还没有任何 Jenkins 配置”               | 让用户本人执行 `config add`，等待完成后重试                                              |
| “job URL 不属于任何已配置的 Jenkins”     | 把报错中的配置列表告诉用户，请用户 `config add` 对应的 Jenkins 根地址                     |
| “同时匹配多个 server 配置”              | 同一台 Jenkins 有多套配置，加 `--server <名称>` 指定                                     |
| “job URL 与 --server 的根地址不匹配”     | 说明 `--server` 选错了；去掉它让脚本自动匹配，或换正确的配置                              |
| HTTP 401/403                            | 凭据失效，让用户重新执行 `config add <名称>` 更新                                        |
| 报“该 URL 是容器 job”                   | 先跑 `jobs --job <容器URL>` 列出子 job，再用子 job 的 URL 或 `--child`                    |
| 子 job 列表只显示到下一层               | 加 `--depth 2` 递归展开；`jobs` 默认只展开一层                                           |
| “没有测试报告”                          | job 未配置测试结果发布，或构建尚未结束；改用 `log` 分析                                   |
| HTTPS 证书错误                          | 内网自签名证书时加 `--insecure`                                                          |
| 构建一直在排队                          | `watch` 会打印排队原因（`queueItem.why`）；长时间不动通常是执行器不足或被上游任务阻塞     |
| 阶段信息缺失                            | Freestyle job 没有 stage 数据，属正常现象                                                |

## 输出示例

```text
Job   my-app
构建  #128  失败 ❌
开始  2026-11-20 11:22:33 +0800
耗时  4分12秒
触发  Started by user alice
参数  BRANCH=main
变更  1 个提交
        3f2a1b9 fix: order total calc - Alice
阶段  ✅准备  ✅构建  ❌测试  · 部署
  ↳ 测试: gradle test failed
URL   https://ci.example.com/job/my-app/128/

退出码: 1
失败阶段:
  ❌ 测试 (FAILED) — gradle test failed

测试    总计 42  通过 40  失败 2  跳过 0  耗时 1分03秒
失败用例（2 个，最多显示 10 个）:
  1. com.acme.OrderTest.shouldSumItems (<1秒)
     expected 30 but was 29
     at com.acme.OrderTest.shouldSumItems(OrderTest.java:42)
```

回复用户时的推荐格式（链接必须写成 Markdown link，保证可直接点击）：

```markdown
**#128 构建失败**，耗时 4分12秒，失败在「测试」阶段；测试 42 个，失败 2 个。
根因：`java.lang.IllegalStateException: boom`（日志 L5 附近）。

**Jenkins**：[#128 构建详情](https://ci.example.com/job/my-app/128/)
```
