---
name: jk-track
description: >-
  查询、跟踪、触发和停止 Jenkins 构建。
  支持识别 job 类型：多分支流水线、组织文件夹、文件夹和普通 job。
  支持查看最新构建和指定构建的结果、耗时、参数、变更和 Pipeline 阶段。
  支持查看失败构建的错误日志和测试报告。
  支持列出构建历史、子 job 和项目 job 列表。
  支持持续跟踪运行中的构建，直到构建结束。
  支持按 job URL 自动匹配多个 Jenkins 的 server 配置。
  当用户提到 Jenkins、job、构建、build、pipeline、构建失败、构建日志、测试报告、
  “等构建跑完”、“看下最新构建”、“#128 为什么失败”、“有哪些分支”、
  “多分支流水线”、“这个 job 是什么类型”、“触发构建”、“停止构建”、“/jk-track”时使用。
---

# jk-track - Jenkins 构建跟踪

## 1. 安全规则

这些规则优先级最高。必须遵守。

1. 不要在对话中索取 Jenkins 用户名或 API Token。
2. 让用户在自己的终端运行 `config add`。不要代用户运行。
3. server 配置只保存在用户本机。
4. 不要读取、打印或复制 server 配置文件。
5. job URL 可以在对话中提供。不要猜测 Jenkins 地址。
6. 不要把用户名或 Token 写入命令、脚本或文件。
7. 不要用 echo、管道或环境变量传递用户名或 Token。
8. 输出不显示 Token。`config list` 和 `config show` 对用户名打码。
9. `trigger` 和 `stop` 会改变 Jenkins 状态。只在用户明确要求时使用。
10. 其他命令只读。它们不改变 Jenkins 状态。
11. 写操作失败后不要重复提交。先运行 `info` 确认状态。
12. 回答必须给出 Jenkins 的 Markdown 链接。见 8.2 节。

## 2. 配置模型

一个 Jenkins 对应一个配置文件。

```text
~/.jk-track/
├── servers/
│   ├── ci.json
│   └── staging.json
└── settings.json
```

`servers/ci.json` 的格式：

```json
{"server": "https://ci.example.com", "user": "alice", "token": "api-token"}
```

规则：

1. 配置文件只保存 server 根地址、用户名和 API Token。
2. 配置文件不保存 job URL。
3. `server` 必须是根地址。它可以带 context path，例如 `https://ci.example.com/jenkins`。
4. 可以配置多个 Jenkins。每个 Jenkins 一个文件。
5. `settings.json` 可选。它保存 `default_server`。相对 job 名称使用这个值。

## 3. 首次使用

### 3.1 用户操作

请用户在自己的终端运行：

```bash
python <skill_dir>/scripts/jk.py config add ci
```

脚本依次询问 4 项：配置名称、Jenkins 根地址、用户名、API Token。Token 输入不回显。
脚本保存文件。文件权限为 600。

### 3.2 AI 操作

1. 运行 `config list`。该命令不显示 Token。
2. 如果没有配置，请用户运行 `config add`。等待用户确认完成。
3. 如果有配置，使用用户提供的 job URL 执行查询。
4. 如果 job URL 不匹配任何配置，列出已有配置。请用户添加正确的 Jenkins。不要索取用户名或 Token。

## 4. 配置命令

这些命令由用户在自己的终端运行。

| 命令 | 说明 |
| --- | --- |
| `config add [<名称>]` | 新增或修改一个 server 配置。`set` 是别名。 |
| `config list` | 列出名称、根地址、打码用户名和 Token 状态。 |
| `config show [<名称>]` | 显示一个配置。Token 不显示。 |
| `config use <名称>` | 设置默认 server。相对 job 名称使用该值。 |
| `config remove <名称>` | 删除一个配置。 |

## 5. 项目 job 列表

job URL 保存在项目目录的 `.jenkins-jobs` 文件中。脚本从当前目录向上查找该文件。
可以用 `--jobs-file <路径>` 或 `JK_JOBS_FILE` 指定其他路径。

文件是普通文本。每行一个 job URL。`#` 开头的行为注释。

```text
# jk-track: 项目使用的 Jenkins job URL
https://ci.example.com/job/my-app
https://ci.example.com/job/my-app/job/main
```

自动记录规则：

1. 命令成功后，脚本记录本次的 job URL。
2. 成功的退出码为 0、1 或 3。
3. 脚本自动去重。
4. `--no-save` 关闭记录。`--dry-run` 不记录。
5. 使用 `--child` 时，脚本记录解析后的子 job URL。
6. 文件只包含 URL。文件不包含用户名或 Token。

`--job` 接受这些格式：

| 格式 | 示例 |
| --- | --- |
| 完整 job URL | `https://ci.example.com/job/my-app/` |
| 构建页 URL | `https://ci.example.com/job/my-app/128/` |
| 序号 | `1` 或 `#1` |
| 短名称 | `main` |
| 相对名称 | `my-app`，需要 `--server` 或默认 server |

规则：

1. 文件中只有一个 URL 时，可以省略 `--job`。
2. 文件中有多个 URL 时，必须用 `--job` 选择。
3. 序号指向文件中的有效行。注释和空行不计入序号。
4. 短名称取 URL 的最后一段。

用 `saved` 查看列表（别名 `urls`）。命令输出序号、job 名称、URL 和匹配的 server。

```bash
python <skill_dir>/scripts/jk.py saved
```

## 6. job URL 匹配 server 配置

规则：

1. 脚本比较 job URL 和配置的 scheme、host、port。
2. scheme、host 和 port 必须相同。
3. 配置带 context path 时，job URL 的路径必须在该路径下。
4. 多个配置匹配时，脚本选择路径最长的配置。
5. 多个配置的路径长度相同，脚本报错。此时必须用 `--server` 指定配置。
6. 没有配置匹配时，脚本报错并列出已有配置。脚本不会把凭据发给未匹配的主机。

显式指定：

```bash
python <skill_dir>/scripts/jk.py info --job my-app --server ci
python <skill_dir>/scripts/jk.py info --job '<JOB_URL>' --server ci
```

`--server` 指定的配置和 job URL 不匹配时，脚本拒绝请求。

## 7. 命令速查

```bash
python <skill_dir>/scripts/jk.py <COMMAND> --job '<JOB_URL>'
```

| 命令 | 用途 | 主要参数 |
| --- | --- | --- |
| `info` | 显示构建概况。默认最新构建。 | `--build N`、`--no-stages`、`--json` |
| `history` | 显示构建历史。默认最近 10 次。 | `--limit 10`、`--all` |
| `jobs` | 列出子 job 和子 job 的最新构建。 | `--depth 2`、`--json` |
| `log` | 显示构建日志。 | `--tail 80`、`--grep`、`-A`、`-i`、`--save` |
| `tests` | 显示测试报告和失败用例。 | `--max-failures`、`--error-lines` |
| `failure` | 汇总失败原因。 | `--max-log-blocks`、`--log-tail`、`--max-failures` |
| `watch` | 跟踪构建，直到构建结束。 | `--next`、`--build N`、`--interval`、`--timeout` |
| `trigger` | 触发一次构建。 | `--param K=V`、`--child`、`--wait`、`--dry-run` |
| `stop` | 停止构建。默认最新构建。 | `--build N`、`--wait`、`--dry-run` |
| `saved` | 列出项目 `.jenkins-jobs` 中的 job。 | `--jobs-file`、`--json` |

`--build` 接受 `128`、`#128`、`latest`、`lastCompleted`、`lastFailed` 和 `lastSuccessful`。
`latest` 是默认值。

通用参数：

| 参数 | 说明 |
| --- | --- |
| `--job`、`--server`、`--child` | job 选择 |
| `--jobs-file`、`--no-save` | 项目 job 列表控制 |
| `--insecure`、`--http-timeout` | HTTPS 证书和 HTTP 超时 |
| `--json`、`--verbose`、`--no-color`、`--quiet` | 输出控制 |

`jobs` 命令输出 job 类型和每个子 job 的 URL。可以直接使用这个 URL。也可以使用 `--child <名称>`。
分支名包含 `/` 时，可以整体传入，例如 `--child feature/login`。

## 8. 工作流程

### 8.1 选择命令

用户给出 job URL 时，先确认 job 类型。见 9.3 节。

| 用户需求 | 命令 |
| --- | --- |
| 判断 job 类型 / 是否多分支流水线 | `jobs --job <URL>` |
| 查看最新构建 | `info --job <URL>` |
| 查看指定构建 | `info --job <URL> --build 128` |
| 查看构建历史 | `history --job <URL>` 或 `--all` |
| 查看子 job | `jobs --job <容器URL>` |
| 查看项目 job 列表 | `saved` |
| 查看失败原因 | `failure --job <URL> --build N` |
| 查看测试报告 | `tests --job <URL> --build N` |
| 查看或搜索日志 | `log --job <URL> --build N`，搜索加 `--grep` |
| 等构建结束 | `watch --job <URL>` |
| 触发构建 | `trigger --job <URL>` |
| 停止构建 | `stop --job <URL> --build N` |

### 8.2 回答规则

1. 先运行命令。再总结结果。
2. 不要粘贴完整原始输出。
3. 总结必须包含构建号、结果和耗时。
4. 有失败阶段、失败用例或错误行时，总结必须包含这些内容。
5. 回答必须包含 Jenkins 链接。
6. 链接必须使用 Markdown 格式。
7. 不要把链接放入行内代码或代码块。
8. job URL 是多分支流水线时，必须先说明这是多分支流水线，再列出分支并请用户选择。
9. 用户给的是分支 job URL 时，回答中说明它是哪个多分支流水线的分支。
10. 不要把多分支流水线的根 job 当成“没有构建记录”的 job。

链接写法：

| 场景 | 写法 |
| --- | --- |
| 某次构建 | [#128 构建详情](https://ci.example.com/job/my-app/128/) |
| 最新构建 | [最新构建 #129](https://ci.example.com/job/my-app/129/) |
| job 主页 | [my-app](https://ci.example.com/job/my-app/) |
| 子 job | [main](https://ci.example.com/job/my-app/job/main/) |
| 多个构建 | [#128](https://ci.example.com/job/my-app/128/) 失败，4分12秒 |

结果含义：

| 结果 | 含义 |
| --- | --- |
| `SUCCESS` | 成功 |
| `UNSTABLE` | 有失败测试，但构建未中断 |
| `FAILURE` | 失败 |
| `ABORTED` | 已中止 |

阶段信息来自 Pipeline API。Freestyle job 没有阶段信息。这是正常情况。
测试报告需要 job 配置测试结果发布。没有测试报告时，请用户查看日志。

## 9. 典型场景

### 9.1 查看构建

```bash
# 最新构建
python <skill_dir>/scripts/jk.py info --job 'https://ci.example.com/job/my-app/'

# 指定构建
python <skill_dir>/scripts/jk.py info --job '<JOB_URL>' --build 128
```

命令成功后，脚本记录该 URL。下次可以使用序号或短名称：

```bash
python <skill_dir>/scripts/jk.py info --job 1
python <skill_dir>/scripts/jk.py info --job my-app
```

### 9.2 查看构建历史

```bash
python <skill_dir>/scripts/jk.py history --job '<JOB_URL>'
python <skill_dir>/scripts/jk.py history --job '<JOB_URL>' --all
```

最新构建在结果列标记为“最新”。用户选择构建后，运行 `info --build <编号>`。

### 9.3 确认 job 类型并查看子 job

用户给出 job URL 时，先确认它是不是多分支流水线。运行：

```bash
python <skill_dir>/scripts/jk.py jobs --job '<JOB_URL>'
python <skill_dir>/scripts/jk.py jobs --job '<JOB_URL>' --depth 2
```

`jobs` 的第一行给出 job 类型：

| 类型 | 含义 | 后续操作 |
| --- | --- | --- |
| 多分支流水线 | 根 job 不产生构建。它的子 job 是分支。 | 请用户选择分支。用分支 URL 或 `--child <分支名>`。 |
| 组织文件夹 | 根 job 不产生构建。子 job 由仓库扫描产生。 | 请用户选择子 job。 |
| 文件夹 | 根 job 不产生构建。 | 请用户选择子 job。 |
| 流水线 job / 自由风格 job 等 | 可构建的普通 job。 | 直接运行 `info`。 |

多分支流水线的根 URL 用于 `info`、`history`、`watch`、`trigger` 或 `stop` 时，
脚本报错并列出分支。不要把这个报错当成“没有构建记录”。

只有多分支流水线的子 job 是分支。分支名包含 `/` 时，用 `--child` 整体传入。

步骤：

1. 把类型和子 job 列表给用户。请用户选择。
2. 使用子 job 的 URL，或者使用 `--child <名称>`。
3. 继续运行 `info`、`history`、`failure` 等命令。

```bash
python <skill_dir>/scripts/jk.py info --job 'https://ci.example.com/job/my-app/job/main/'
python <skill_dir>/scripts/jk.py failure --job '<JOB_URL>' --child dev --build 42
```

容器 URL 用于 `info` 或 `history` 时，脚本报错。报错信息给出类型、子 job 的名称和 URL。
分支 job 没有构建记录时，脚本报“还没有任何构建记录”。这是正常情况。不要改用根 URL 查询。

### 9.4 分析失败构建

```bash
python <skill_dir>/scripts/jk.py failure --job '<JOB_URL>' --build 128
```

输出顺序：

1. 构建概况。
2. 失败阶段。
3. 测试统计和失败用例。
4. 日志错误摘录。摘录带行号。
5. 日志尾部。

需要更多信息时，运行这些命令：

```bash
python <skill_dir>/scripts/jk.py log --job '<JOB_URL>' --build 128 --grep 'Caused by' -A 5
python <skill_dir>/scripts/jk.py log --job '<JOB_URL>' --build 128 --tail 0 --save /tmp/build-128.log
```

### 9.5 等构建结束

```bash
python <skill_dir>/scripts/jk.py watch --job '<JOB_URL>' --next
python <skill_dir>/scripts/jk.py watch --job '<JOB_URL>' --build 128
python <skill_dir>/scripts/jk.py watch --job '<JOB_URL>'
```

行为：

1. 进度写到 stderr。最终报告写到 stdout。
2. 构建失败时，报告包含失败原因。`--no-report` 关闭失败报告。
3. 默认轮询间隔为 15 秒。默认最长等待为 1 小时。
4. `--timeout 0` 表示不限制等待时间。
5. 等待超时的退出码为 3。

长构建使用后台方式：

```bash
python <skill_dir>/scripts/jk.py watch --job '<JOB_URL>' --next --quiet > /tmp/jk-watch.log 2>&1
```

等待超时后，不要重新触发构建。运行 `watch --build <N>` 继续跟踪。

### 9.6 触发构建

```bash
python <skill_dir>/scripts/jk.py trigger --job 'https://ci.example.com/job/my-app/'
python <skill_dir>/scripts/jk.py trigger --job '<JOB_URL>' \
  --param BRANCH=main --param DEPLOY=true
python <skill_dir>/scripts/jk.py trigger --job 'https://ci.example.com/job/my-app/' --child main
python <skill_dir>/scripts/jk.py trigger --job '<JOB_URL>' --param A=1 --dry-run
python <skill_dir>/scripts/jk.py trigger --job '<JOB_URL>' --wait
```

输出示例：

```text
已触发: my-app
队列: https://ci.example.com/queue/item/1234/
构建: #128  https://ci.example.com/job/my-app/128/
```

行为：

1. 无参数时，脚本 POST `.../build`。有 `--param` 时，脚本 POST `.../buildWithParameters`。
2. 脚本自动读取 CSRF crumb。脚本不对 POST 自动重试。
3. 默认最多等待 300 秒，让构建离开队列。默认轮询间隔为 5 秒。用 `--queue-timeout` 修改等待时间。
4. `--wait` 最多再等 3600 秒。用 `--watch-timeout` 修改。
5. 触发成功后，脚本记录 job URL 到 `.jenkins-jobs`。
6. HTTP 403 或 401 表示凭据或权限问题。HTTP 400 表示参数和 job 定义不匹配。

触发流程：

1. 向用户确认 job、分支和参数。
2. 参数较多或不确定时，先运行 `--dry-run`。
3. 把请求内容给用户看。
4. 用户确认后，再提交构建。
5. 请求超时或网络错误时，不要重试。先运行 `info` 确认状态。
6. 回答中给出构建或队列的 Markdown 链接。

### 9.7 停止构建

```bash
python <skill_dir>/scripts/jk.py stop --job '<JOB_URL>' --build 131
python <skill_dir>/scripts/jk.py stop --job '<JOB_URL>'
python <skill_dir>/scripts/jk.py stop --job '<JOB_URL>' --build 131 --dry-run
python <skill_dir>/scripts/jk.py stop --job '<JOB_URL>' --build 131 --wait
```

输出示例：

```text
已发送停止请求: my-app #131
构建: https://ci.example.com/job/my-app/131/
```

行为：

1. 构建已结束时，脚本提示“无需停止”。退出码为 0。
2. 构建在队列中时，脚本报错。此时构建还没有构建号。
3. `--wait` 等待构建真正结束。停止后的结果通常为 `ABORTED`。退出码为 1。
4. 等待超时的退出码为 3。默认轮询间隔为 5 秒。默认最长等待为 1 小时，用 `--watch-timeout` 修改。
5. `/stop` 返回 404 时，脚本回退到 `/kill`。
6. 停止是写操作。只在用户明确要求时使用。
7. 不要停止用户没有提到的构建。
8. 构建用于部署或多人共享时，先向用户确认。

## 10. 多 Jenkins 环境

```bash
python <skill_dir>/scripts/jk.py config add ci
python <skill_dir>/scripts/jk.py config add staging
python <skill_dir>/scripts/jk.py info --job 'https://ci.example.com/job/my-app/'
python <skill_dir>/scripts/jk.py info --job 'https://staging.example.com/job/my-app/'
```

规则：

1. 新增 Jenkins 时，只添加一个配置文件。不需要为每个 job 添加配置。
2. 一台 Jenkins 有多个 context path 时，脚本按最长路径匹配。
3. 两个配置的根地址相同时，脚本报“同时匹配多个”。此时使用 `--server`。

## 11. 退出码

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功。`watch` 结束时构建结果为 `SUCCESS`。 |
| 1 | `watch`、`trigger --wait` 或 `stop --wait` 的结果不是 `SUCCESS`。 |
| 2 | 参数、凭据、配置匹配、网络或 HTTP 错误。 |
| 3 | 等待超时。构建仍在运行。 |
| 130 | 用户中断。构建在 Jenkins 上继续运行。 |

`info`、`history`、`jobs`、`log`、`tests` 和 `failure` 只显示信息。构建失败时，这些命令仍返回 0。

## 12. JSON 输出

查询命令和写操作支持 `--json`。

```bash
python <skill_dir>/scripts/jk.py info --job '<JOB_URL>' --json
python <skill_dir>/scripts/jk.py failure --job '<JOB_URL>' --json
python <skill_dir>/scripts/jk.py tests --job '<JOB_URL>' --json
```

公共字段：`job.name`、`number`、`result`、`building`、`duration_ms`、`parameters`、`changes`、
`stages[]`、`tests`、`log_errors[]`。

其他字段：

1. `failure` 包含 `failed_stages[]`、`exit_codes[]` 和 `log_tail[]`。
2. `watch --json` 返回 `build` 和 `failure_report`。
3. `history --json` 返回 `job`、`total` 和 `builds[]`。
4. `jobs --json` 返回 `job`、`kind`、`kind_label`、`server` 和 `children[]`。
   `kind` 为 `multibranch`、`organization-folder`、`folder`、`container` 或 `leaf`。
   普通 job 的 `children` 为 null。
5. `saved --json` 返回 `file` 和 `jobs[]`。
6. `trigger --json` 返回 `job`、`job_url`、`queue` 和 `build`。
7. `trigger --wait --json` 返回与 `watch --json` 相同的结构。
8. `stop --json` 返回 `job`、`number`、`url`、`stopped` 和 `http_status`。
9. JSON 不包含 Token。

## 13. 故障排查

| 现象 | 处理 |
| --- | --- |
| “还没有任何 Jenkins 配置” | 请用户运行 `config add`。等待用户确认。 |
| job URL 不属于任何配置 | 列出已有配置。请用户添加正确的 Jenkins 根地址。 |
| 同时匹配多个 server 配置 | 用 `--server <名称>` 指定配置。 |
| job URL 和 `--server` 不匹配 | 去掉 `--server`，让脚本自动匹配。 |
| HTTP 401 或 403 | 凭据失效或没有权限。请用户重新运行 `config add`。 |
| 报“多分支流水线”或“文件夹” | 先运行 `jobs`。再用子 job URL 或 `--child` 选择分支或子 job。 |
| 多分支流水线的分支列表为空 | 在 Jenkins 上执行 Scan Multibranch Pipeline。等扫描完成再运行 `jobs`。 |
| 分支 job 报“还没有任何构建记录” | 该分支还没有构建。先触发构建，或换一个分支。 |
| 子 job 只显示一层 | 加 `--depth 2`。 |
| 缺少 `--job` | 用 `saved` 查看列表。再用 `--job` 选择。 |
| 序号超出范围 | 用 `saved` 查看实际序号。可以编辑 `.jenkins-jobs`。 |
| `.jenkins-jobs` 无法写入 | 用 `--jobs-file` 指定可写路径。 |
| 没有测试报告 | job 未发布测试结果。请用户查看日志。 |
| HTTPS 证书错误 | 加 `--insecure`。 |
| 构建一直在排队 | `watch` 显示排队原因。常见原因是执行器不足。 |
| 阶段信息缺失 | Freestyle job 没有阶段信息。这是正常情况。 |
| 触发返回 400 | 参数不匹配。检查 `--param` 的名称。 |
| 触发返回 403 或 401 | 凭据无构建权限，或 CSRF 校验失败。不要重复提交。 |
| 触发后没有新构建 | 构建在队列中。使用队列链接或 `--wait`。 |
| 停止返回 403 或 404 | 无停止权限，或构建已结束。先用 `info` 确认状态。 |
| 构建在队列中无法停止 | 构建还没有构建号。请用户到 Jenkins 队列页面取消。 |

## 14. 输出示例

```text
Job   my-app
构建  #128  失败 ❌
开始  2026-11-20 11:22:33 +0800
耗时  4分12秒
阶段  ✅准备  ✅构建  ❌测试  · 部署
  ↳ 测试: gradle test failed
URL   https://ci.example.com/job/my-app/128/

测试    总计 42  通过 40  失败 2  跳过 0
  1. com.acme.OrderTest.shouldSumItems (<1秒)
     expected 30 but was 29
```

回答示例：

```markdown
**#128 构建失败**，耗时 4分12秒，失败在「测试」阶段；测试 42 个，失败 2 个。
根因：`java.lang.IllegalStateException: boom`（日志 L5 附近）。

**Jenkins**：[#128 构建详情](https://ci.example.com/job/my-app/128/)
```
