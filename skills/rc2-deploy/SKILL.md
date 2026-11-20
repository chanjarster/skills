---
name: rc2-deploy
description: >-
  管理 Rancher 2.x 的集群、项目和部署资源。
  支持列出集群、项目、命名空间。
  支持列出、查询、编辑、创建、删除和重新部署 deployment、statefulset、daemonset、job。
  支持管理 configmap、secret、ingress、service、PVC。
  支持用 YAML 清单创建或更新资源。
  支持集群级 scope token，通过 Rancher 的 Kubernetes API 代理访问单个集群。
  当用户提到 Rancher、Rancher 集群、Rancher 项目、命名空间、工作负载、deployment、
  statefulset、daemonset、job、configmap、secret、ingress、service、PVC、
  “重新部署”、“发布到 Rancher”、“/rc2-deploy”时使用。
---

# rc2-deploy - Rancher 2.x 部署助手

## 1. 安全规则

这些规则优先级最高。必须遵守。

1. 不要在对话中索取 Access Key 或 Secret Key。
2. 让用户在自己的终端运行 `config add`。不要代用户运行。
3. 连接配置只保存在用户本机。
4. 不要读取、打印或复制连接配置文件。
5. 项目地址和项目 ID 可以在对话中提供。不要猜测 Rancher 地址。
6. 不要把 Access Key 或 Secret Key 写入命令、脚本或文件。
7. 不要用 echo、管道或环境变量传递密钥。
8. 输出不显示密钥。`config list` 和 `config show` 对 Access Key 打码。
9. Secret 的值默认打码。只有用户明确要求时，才用 `--reveal` 显示。
10. `apply`、`delete` 和 `redeploy` 会改变集群状态。只在用户明确要求时使用。
11. 其他命令只读。它们不改变集群状态。
12. 写操作前，把目标项目、命名空间、资源名称和清单内容给用户看。用户确认后再执行。
13. 写操作失败后不要重复提交。先运行 `get` 确认状态。
14. 回答必须给出 Rancher API 链接。见 7.2 节。
15. 优先使用集群级 scope token。只有用户明确同意时，才使用无 scope 的 token。

## 2. 配置模型

一个 Rancher 对应一个配置文件。配置文件由 `config add` 写入。

```text
~/.rc2-deploy/
├── servers/
│   ├── prod.json
│   └── staging.json
└── settings.json
```

`servers/prod.json` 的格式：

```json
{
  "url": "https://rancher.example.com/v3",
  "access_key": "token-xxxxx",
  "secret_key": "xxxxx",
  "scope": "cluster:c-abc123"
}
```

规则：

1. 配置文件只保存 API 根地址、Access Key、Secret Key 和可选 scope。
2. 配置文件权限为 600。目录权限为 700。
3. `url` 是 API 根地址。它以 `/v3` 结尾。
4. `url` 不是项目地址。项目地址包含 `/projects/<项目ID>`。
5. `scope` 可选。它取值为 `cluster:c-xxxxx`。没有 scope 时字段省略。
6. 有 `scope` 时，脚本使用 Kubernetes API 代理 `/k8s/clusters/<集群ID>`。
7. 没有 `scope` 时，脚本使用 Rancher `/v3` API。
8. 可以配置多个 Rancher。每个 Rancher 一个文件。
9. `settings.json` 保存 `default_server`。省略 `--server` 时使用这个值。

### 2.1 Token 类型

Rancher 支持两种 API Key。两种都受用户角色限制。

| Token 类型 | scope 字段 | 可访问范围 | 脚本使用的 API |
| --- | --- | --- | --- |
| 所有集群 | 省略 | 用户可见的全部资源 | `/v3` |
| 集群级 | `cluster:c-xxxxx` | 指定集群的 Kubernetes API | `/k8s/clusters/<集群ID>` |

规则：

1. 推荐集群级 token。它只对指定集群有效，泄露后的影响范围小。
2. 集群级 token 不能访问 `/v3`。它访问 `/v3` 时 Rancher 返回 401。
3. 使用集群级 token 时，`clusters` 只显示 scope 集群。
4. 使用集群级 token 时，`projects` 不可用。请从 Rancher UI 复制项目地址。
5. 集群级 token 必须配合 `--project` 使用。
6. 无 scope 的 token 拥有该用户的全部权限。只有用户明确同意时才使用。
7. 如果必须使用无 scope 的 token，请为自动化创建专用低权限用户。

### 2.2 获取集群 ID 和项目 ID

1. 集群 ID 形如 `c-xxxxx`。项目 ID 形如 `c-xxxxx:p-yyyy`。
2. 集群页地址包含 `/c/c-xxxxx` 或 `/dashboard/c/c-xxxxx`。
3. 项目页地址包含 `/p/c-xxxxx:p-yyyy/`。
4. 详细取值步骤见 [references/api.md](references/api.md) 第 11 节。
5. 集群级 token 的 scope 只填集群 ID，不填项目 ID。
6. Token 创建后 scope 不能修改。填错就删除 Key 并重建。
7. `config add` 可以直接粘贴集群或项目页面 URL。粘贴项目 URL 时会记录项目。
8. 集群级 token 读不到项目名。请用 `remember` 命名，或在 `config add` 时填写项目名。

## 3. 首次使用

### 3.1 用户操作

请用户在自己的终端运行：

```bash
python <skill_dir>/scripts/rc2.py config add prod
```

脚本依次询问 5 项：配置名称、Rancher API 地址、Access Key、Secret Key、Token scope。
Secret Key 输入不回显。
Token scope 可以直接回车（所有集群）。
Token scope 也可以粘贴集群或项目页面 URL，或填 `cluster:c-xxxxx`。
粘贴项目页面 URL 时，脚本自动记录该项目地址，并询问项目名称（可选）。

### 3.2 AI 操作

1. 运行 `config list`。该命令不显示密钥。
2. 如果没有配置，请用户运行 `config add`。等待用户确认完成。
3. 询问用户 Token 的 scope。优先建议集群级 token。
4. 运行 `clusters` 校验连接。
5. 运行 `projects` 获取项目。集群级 token 跳过本步。
6. 把项目地址或项目 ID 写入 `--project`。

## 4. 配置命令

这些命令由用户在自己的终端运行。

| 命令 | 说明 |
| --- | --- |
| `config add [<名称>]` | 新增或修改一个配置。`set` 是别名。 |
| `config list` | 列出名称、地址、打码 Access Key 和 Secret Key 状态。 |
| `config show [<名称>]` | 显示一个配置。Secret Key 不显示。 |
| `config use <名称>` | 设置默认配置。 |
| `config remove <名称>` | 删除一个配置。 |

## 5. 项目记忆

项目地址保存在项目目录的 `.rancher-projects` 文件。脚本从当前目录向上查找该文件。
可以用 `--projects-file <路径>` 或 `RC2_PROJECTS_FILE` 指定其他路径。

文件是普通文本。每行一个项目地址。`#` 开头的行为注释。

```text
# rc2-deploy: 本项目的 Rancher 项目连接
https://rancher.example.com/v3/projects/c-abc123:p-def456  id-gov
```

每行是「项目地址 + 项目名」。两者用空格分隔。项目名可选。

自动记录规则：

1. 命令成功后，脚本记录本次的项目地址。
2. 无 scope token 时，脚本同时记录 API 返回的项目名。
3. 集群级 token 读不到项目名。用 `remember` 或 `config add` 补充。
4. `--no-save` 关闭记录。`--dry-run` 不记录。
5. `--project` 不传时，脚本使用记录中的项目。
6. 文件中只有一个项目时，可以省略 `--project`。
7. 文件中有多个项目时，必须用 `--project` 选择。

命名项目（集群级 token 推荐）：

```bash
python <skill_dir>/scripts/rc2.py remember id-gov --project c-abc123:p-def456
```

`--project` 接受这些格式：

| 格式 | 示例 |
| --- | --- |
| 项目名称 | `id-gov`，来自 `.rancher-projects` |
| 项目页面地址 | `https://rancher.example.com/p/c-abc123:p-def456/` |
| API 项目地址 | `https://rancher.example.com/v3/projects/c-abc123:p-def456` |
| 项目 ID | `c-abc123:p-def456`，需要 `--server` 或默认配置 |
| 序号 | `1` |
| 短 ID | `p-def456` |

规则：

1. 页面地址可以带后面的路径。脚本只提取项目 ID。
2. 集群页地址不是项目地址。脚本报错并提示。
3. 页面地址的主机必须匹配一个 Rancher 配置。
4. 项目名称不区分大小写。同名项目有多个时报错。

用 `saved` 查看列表（别名 `remembered`）。命令输出序号、项目 ID、项目名、集群、配置名称和地址。

```bash
python <skill_dir>/scripts/rc2.py saved
```

## 6. 命令速查

```bash
python <skill_dir>/scripts/rc2.py <COMMAND> [参数]
```

| 命令 | 用途 | 主要参数 |
| --- | --- | --- |
| `clusters` | 列出集群。 | `--json` |
| `projects` | 列出项目。 | `--cluster <集群ID>`、`--json` |
| `namespaces` | 列出项目下的命名空间。 | `--json` |
| `ls <类型>` | 列出资源。`list` 是别名。 | `--namespace`、`--json` |
| `get <类型>/<名称>` | 查询一个资源。 | `--namespace`、`--json`、`--yaml`、`--reveal` |
| `apply --file <路径>` | 用 YAML 清单创建或更新资源。`create`、`edit` 是别名。 | `--dry-run` |
| `delete <类型>/<名称>` | 删除一个资源。 | `--namespace`、`--dry-run` |
| `redeploy <类型>/<名称>` | 重新部署工作负载或 Job。 | `--namespace`、`--dry-run` |
| `saved` | 列出已记录的项目。 | `--projects-file`、`--json` |
| `remember <名称>` | 给项目命名，便于用名称选择。 | `--project`、`--server` |

集群级 token 的限制：

1. `projects` 不可用。请从 Rancher UI 复制项目地址。
2. `clusters` 只显示 scope 集群。
3. `apply` 的清单必须包含 `metadata.namespace`。

通用参数：

| 参数 | 说明 |
| --- | --- |
| `--project`、`--projects-file` | 项目选择与记忆 |
| `--namespace` | 命名空间 |
| `--server` | 配置文件名称 |
| `--insecure`、`--http-timeout` | HTTPS 证书和 HTTP 超时 |
| `--json`、`--verbose`、`--no-save` | 输出与记录控制 |

环境变量：`RC2_SERVER`、`RC2_PROJECT`、`RC2_PROJECTS_FILE`。命令行参数优先于环境变量。

资源写法：

| 写法 | 示例 |
| --- | --- |
| 类型/名称 | `deployment/api-server` |
| 类型/名称加命名空间 | `deployment/api-server --namespace prod` |
| 完整 ID | `deployment:prod:api-server` |

资源类型和别名：

| 类型 | 别名 | Rancher 集合 |
| --- | --- | --- |
| deployment | `deployments`、`deploy`、`dep` | `deployments` |
| statefulset | `statefulsets`、`sts` | `statefulsets` |
| daemonset | `daemonsets`、`ds` | `daemonsets` |
| job | `jobs` | `jobs` |
| configmap | `configmaps`、`cm` | `configmaps` |
| secret | `secrets` | `secrets` |
| ingress | `ingresses`、`ing` | `ingresses` |
| service | `services`、`svc` | `services` |
| pvc | `pvcs`、`persistentvolumeclaims` | `persistentvolumeclaims` |

## 7. 工作流程

### 7.1 选择命令

| 用户需求 | 命令 |
| --- | --- |
| 查看集群 | `clusters` |
| 查看项目 | `projects --cluster <集群ID>` |
| 查看命名空间 | `namespaces` |
| 查看工作负载 | `ls deployments`、`ls sts`、`ls ds`、`ls jobs` |
| 查看配置 | `ls cm`、`ls secrets`、`ls ing`、`ls svc`、`ls pvc` |
| 查看资源详情 | `get deployment/<名称>` |
| 导出资源清单 | `get deployment/<名称> --yaml` |
| 创建或修改资源 | `apply --file <路径>` |
| 删除资源 | `delete <类型>/<名称>` |
| 重新部署 | `redeploy deployment/<名称>` |
| 查看已记录项目 | `saved` |
| 给项目命名 | `remember <名称> --project <项目地址或ID>` |
| 使用集群级 token | 先运行 `config add`，scope 填 `cluster:c-xxxxx`，再见 8.8 节 |

### 7.2 回答规则

1. 先运行命令。再总结结果。
2. 不要粘贴完整原始输出。
3. 总结必须包含项目、命名空间、资源名称和状态。
4. 回答必须包含资源的 API 链接。链接来自 `get` 输出的“链接”行。
5. 链接必须使用 Markdown 格式。不要放入代码块。
6. 不要显示 Secret 的值。只显示键名。

链接写法：

| 场景 | 写法 |
| --- | --- |
| 一个 Deployment | [api-server](https://rancher.example.com/v3/projects/c-abc123:p-def456/deployments/deployment:prod:api-server) |
| 一个 ConfigMap | [app-config](https://rancher.example.com/v3/projects/c-abc123:p-def456/configmaps/configmap:prod:app-config) |

### 7.3 创建或修改资源

1. 运行 `get <类型>/<名称> --yaml` 导出当前清单。新资源跳过本步。
2. 请用户提供或确认新清单。
3. 运行 `--dry-run` 检查目标项目。
4. 把清单内容和目标项目给用户看。
5. 用户确认后，运行 `apply --file <路径>`。
6. 运行 `get` 或 `ls` 确认结果。

`apply` 使用项目的 `importYaml` 动作。它同时处理创建和更新。
清单必须包含 `metadata.namespace`。如果清单缺少命名空间，资源会进入 `default` 命名空间。

### 7.4 删除资源

1. 运行 `get <类型>/<名称>` 确认资源和命名空间。
2. 运行 `delete <类型>/<名称> --dry-run`。
3. 把目标给用户看。请用户确认。
4. 用户确认后，运行 `delete <类型>/<名称>`。
5. 删除不可恢复。有状态资源要提醒用户备份数据。

### 7.5 重新部署

1. 运行 `get <类型>/<名称>` 确认状态和可用动作。
2. 运行 `redeploy <类型>/<名称> --dry-run`。
3. 把目标给用户看。请用户确认。
4. 用户确认后，运行 `redeploy <类型>/<名称>`。
5. Job 的 `redeploy` 依赖 Rancher 版本。没有该动作时，脚本报错。此时用 `get --yaml` 导出清单，再用 `delete` 和 `apply` 重建。

## 8. 典型场景

### 8.1 查看集群和项目

```bash
python <skill_dir>/scripts/rc2.py clusters
python <skill_dir>/scripts/rc2.py projects --cluster c-abc123
```

把项目地址给用户。用户确认后，用 `--project` 执行后续命令。

### 8.2 查看命名空间和资源

```bash
python <skill_dir>/scripts/rc2.py namespaces --project 'https://rancher.example.com/v3/projects/c-abc123:p-def456'
python <skill_dir>/scripts/rc2.py ls deployments --namespace prod
python <skill_dir>/scripts/rc2.py ls sts --namespace prod
python <skill_dir>/scripts/rc2.py ls jobs --namespace prod
```

### 8.3 查看资源详情

```bash
python <skill_dir>/scripts/rc2.py get deployment/api-server --namespace prod
python <skill_dir>/scripts/rc2.py get deployment/api-server --namespace prod --json
python <skill_dir>/scripts/rc2.py get deployment/api-server --namespace prod --yaml
```

`get` 输出资源状态、副本数、镜像、可用动作和 API 链接。

### 8.4 编辑资源

```bash
# 导出当前清单
python <skill_dir>/scripts/rc2.py get deployment/api-server --namespace prod --yaml > api-server.yaml

# 用户编辑 api-server.yaml

# 检查并提交
python <skill_dir>/scripts/rc2.py apply --file api-server.yaml --dry-run
python <skill_dir>/scripts/rc2.py apply --file api-server.yaml
```

### 8.5 创建资源

```bash
python <skill_dir>/scripts/rc2.py apply --file new-configmap.yaml
```

清单示例：

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
  namespace: prod
data:
  log.level: info
```

### 8.6 删除资源

```bash
python <skill_dir>/scripts/rc2.py delete configmap/app-config --namespace prod --dry-run
python <skill_dir>/scripts/rc2.py delete configmap/app-config --namespace prod
```

### 8.7 重新部署

```bash
python <skill_dir>/scripts/rc2.py redeploy deployment/api-server --namespace prod
python <skill_dir>/scripts/rc2.py redeploy sts/db --namespace prod
python <skill_dir>/scripts/rc2.py redeploy job/nightly --namespace prod
```

### 8.8 使用集群级 token

1. 获取集群 ID。见 2.2 节。
2. 在 Rancher 创建 API Key。Scope 选择该集群。
3. 运行 `config add prod`。Token scope 直接粘贴项目页面 URL。
4. 脚本从 URL 提取集群，写入 scope，并记录项目。
5. 输入项目名称（可选）。建议填写，例如 `id-gov`。
6. 运行 `clusters --server prod` 校验连接。输出只有该集群。
7. 用 `--project` 执行命令。项目已记录时可以省略。

```bash
python <skill_dir>/scripts/rc2.py config add prod
# Token scope: https://rancher.example.com/p/c-abc123:p-def456/
# 项目名称（可选，回车跳过）: id-gov

python <skill_dir>/scripts/rc2.py clusters --server prod
python <skill_dir>/scripts/rc2.py ls deployments
python <skill_dir>/scripts/rc2.py get deployment/api --namespace prod --yaml
python <skill_dir>/scripts/rc2.py redeploy deployment/api --namespace prod
```

规则：

1. 集群级模式下，脚本使用 `/k8s/clusters/<集群ID>`。
2. 查询、删除和重新部署使用 Kubernetes API。
3. `apply` 使用 server-side apply。清单必须包含 `metadata.namespace`。
4. 省略 `--namespace` 时，脚本查询全部命名空间。
5. 集群级 token 不能列项目。`projects` 报错是正常行为。
6. Job 不支持 redeploy。用 `delete` 和 `apply` 重建。

## 9. 输出示例

```text
NAME       NAMESPACE  READY  IMAGES                       STATE   AGE
api        default    2/3    registry.local/api:1.0       active  5d
api        prod       1/1    registry.local/api:1.0       active  5d
worker     prod       2/2    registry.local/worker:2.1    active  5d

项目：web（c-demo:p-web）  共 3 个 Deployment
```

`get` 输出示例：

```text
类型      Deployment
名称      api
命名空间  default
项目      web（c-demo:p-web）
状态      active
副本      2/3
镜像      registry.local/api:1.0
动作      pause, redeploy, rollback
创建      5d
链接      https://rancher.example.com/v3/projects/c-demo:p-web/deployments/deployment:default:api
```

回答示例：

```markdown
**api** 在 `prod` 命名空间有 2 个实例，2 个可用，镜像 `registry.local/api:1.0`。

**Rancher**：[api-server](https://rancher.example.com/v3/projects/c-abc123:p-def456/deployments/deployment:prod:api-server)
```

## 10. 退出码

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功。查询完成，或写操作已提交。 |
| 2 | 参数、配置、认证、权限、网络或 HTTP 错误。 |
| 130 | 用户中断。 |

`ls`、`get`、`clusters`、`projects` 和 `namespaces` 只显示信息。资源状态异常时，这些命令仍返回 0。

## 11. 故障排查

| 现象 | 处理 |
| --- | --- |
| “还没有任何 Rancher 配置” | 请用户运行 `config add`。等待用户确认。 |
| 不知道集群 ID | 按 2.2 节从集群页面或 API 地址获取。不要猜测。 |
| HTTP 401 | 凭据无效或过期。请用户重新运行 `config add`。 |
| HTTP 401（集群级 token） | 确认 scope 集群与项目集群一致。集群级 token 不能访问 `/v3`。确认 token 未过期。 |
| `projects` 报“不能列项目” | 集群级 token 的正常行为。从 Rancher UI 复制项目地址。 |
| “不属于 scope 集群” | 项目 ID 的集群部分必须等于 `cluster:c-xxxxx` 中的集群。 |
| “无法识别 scope” | 粘贴集群或项目页面 URL，或填 `cluster:c-xxxxx`。 |
| 项目名显示为 `-` | 集群级 token 读不到项目名。用 `remember <名称> --project <地址>` 补充。 |
| “这是集群地址，不是项目地址” | `--project` 需要项目页面 URL（含 `/p/c-xxx:p-yyy/`）或项目 ID。 |
| `apply` 报缺少 `metadata.namespace` | 集群级模式必须写命名空间。清单缺少时，加上 `metadata.namespace`。 |
| HTTP 403 | API Key 对目标资源没有权限。检查用户的项目角色和集群角色。 |
| 项目地址不属于任何配置 | 列出已有配置。请用户提供正确的项目地址，或添加对应 Rancher。 |
| 同时匹配多个配置 | 用 `--server <名称>` 指定配置。 |
| “多个已记录的项目” | 用 `saved` 查看列表。再用 `--project <序号>` 选择。 |
| “在多个命名空间存在” | 用 `--namespace` 指定命名空间。 |
| 找不到集合地址 | 项目 ID 错误，或 API Key 没有该项目的权限。先运行 `projects` 确认项目 ID。 |
| `importYaml` 动作缺失 | 项目和集群都没有该动作。检查 Rancher 版本和 API Key 权限。 |
| 应用清单后没有变化 | 清单缺少 `metadata.namespace`，或名称和现有资源不一致。检查清单并用 `ls` 确认。 |
| Job 不支持 redeploy | 该 Rancher 版本的 Job 没有 `redeploy` 动作。导出 YAML，再用 `delete` 和 `apply` 重建。 |
| Job 不支持 redeploy（集群级） | 这是设计行为。导出 YAML，再用 `delete` 和 `apply` 重建。 |
| HTTPS 证书错误 | 加 `--insecure`。 |
| 项目记忆文件无法写入 | 用 `--projects-file` 指定可写路径。 |

## 12. 参考

Rancher API 端点、字段和动作的说明见 [references/api.md](references/api.md)。