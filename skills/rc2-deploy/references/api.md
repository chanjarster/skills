# Rancher 2.x API 参考

本文档说明 `rc2.py` 使用的 Rancher v3 API。文档按 ASD-STE100 规则编写。

## 1. 地址与认证

1. API 根地址以 `/v3` 结尾，例如 `https://rancher.example.com/v3`。
2. 脚本用 HTTP Basic 认证。用户名是 Access Key，密码是 Secret Key。
3. Access Key 以 `token-` 开头。
4. 集群级 scope 的 token 只能访问该集群的 Kubernetes API。见第 8 节。
5. 项目 ID 的格式是 `c-<集群>:p-<项目>`，例如 `c-abc123:p-def456`。
6. 项目地址的格式是 `<根地址>/projects/<项目ID>`。
7. API Key 在 `https://<Rancher 域名>/apikeys` 创建。
8. 页面不存在时，从右上角头像进入 Account & API Keys。

请求头：

```text
Authorization: Basic base64(<Access Key>:<Secret Key>)
Accept: application/json
Content-Type: application/json
```

## 2. 只读端点

| 用途 | 方法与路径 |
| --- | --- |
| 列出集群 | `GET /v3/clusters` |
| 列出项目 | `GET /v3/projects` |
| 按集群列出项目 | `GET /v3/projects?clusterId=<集群ID>` |
| 读取项目 | `GET /v3/projects/<项目ID>` |
| 列出项目命名空间 | `GET /v3/projects/<项目ID>/namespaces` |
| 列出资源 | `GET /v3/projects/<项目ID>/<集合>` |

`<集合>` 的取值：

| 资源 | 集合 | 备用写法 |
| --- | --- | --- |
| Deployment | `deployments` | `workloads` |
| StatefulSet | `statefulsets` | `statefulSets`、`workloads` |
| DaemonSet | `daemonsets` | `daemonSets`、`workloads` |
| Job | `jobs` | 无 |
| ConfigMap | `configmaps` | `configMaps` |
| Secret | `secrets` | 无 |
| Ingress | `ingresses` | 无 |
| Service | `services` | 无 |
| PersistentVolumeClaim | `persistentvolumeclaims` | `persistentVolumeClaims` |

规则：

1. 脚本按表的顺序逐个尝试集合地址。
2. 第一次返回 HTTP 200 的地址会被使用。
3. 返回 HTTP 404 时，脚本尝试下一个地址。
4. 其他错误立即终止。
5. Rancher 2.4 的旧地址是 `/v3/project/<项目ID>/workloads`。该集合只包含工作负载。
6. 用 `workloads` 集合时，脚本按资源的 `type` 字段过滤。

## 3. 分页

集合响应包含 `data` 和 `pagination`：

```json
{
  "data": [],
  "pagination": {"next": "https://rancher.example.com/v3/clusters?limit=200&marker=2"}
}
```

规则：

1. 脚本第一次请求加 `limit=200`。
2. `pagination.next` 存在时，脚本继续请求。
3. 没有 `pagination.next` 时，集合结束。
4. 脚本用 `seen` 集合防止循环。

## 4. 资源字段

脚本从资源对象读取这些字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 资源 ID，格式是 `<类型>:<命名空间>:<名称>` |
| `name` | 资源名称 |
| `namespaceId` | 命名空间 |
| `state` | 状态，例如 `active` |
| `createdTS` | 创建时间，毫秒时间戳 |
| `created` | 创建时间，ISO 8601 文本 |
| `type` | 资源类型，例如 `deployment` |
| `scale` | 期望副本数 |
| `availableReplicas`、`readyReplicas` | 可用副本数 |
| `desired`、`ready` | DaemonSet 的期望数和就绪数 |
| `completions`、`succeeded` | Job 的完成数和成功数 |
| `containers[].image` | 容器镜像 |
| `data` | ConfigMap 和 Secret 的数据 |
| `binaryData` | ConfigMap 的二进制数据 |
| `_type` | Secret 的 Kubernetes 类型，例如 `Opaque` |
| `clusterIp` | Service 的集群 IP |
| `ports[]` | Service 的端口列表 |
| `rules[].host`、`publicEndpoints[].hostname` | Ingress 的域名 |
| `resources.requests.storage` | PVC 的容量 |
| `accessModes[]` | PVC 的访问模式 |

有些 Rancher 版本使用 `clusterIP`。脚本同时读取 `clusterIp` 和 `clusterIP`。

## 5. 链接

每个资源有 `links` 字段：

| 键 | 用途 |
| --- | --- |
| `self` | 资源地址。查询和动作的基地址。 |
| `remove` | 删除地址。脚本优先用它执行 DELETE。 |
| `yaml` | YAML 清单地址。`get --yaml` 使用它。 |

规则：

1. `get --yaml` 直接请求 `links.yaml`。响应是 YAML 文本。
2. 资源没有 `yaml` 链接时，脚本报错。此时用 `--json` 导出。
3. `delete` 优先使用 `links.remove`。没有时使用 `links.self`。

## 6. 写操作

### 6.1 创建和更新：importYaml

项目资源的 `actions` 包含 `importYaml`。`apply` 使用它。

```text
POST /v3/projects/<项目ID>?action=importYaml
Content-Type: application/json

{"yaml": "<清单文本>"}
```

规则：

1. 请求体只有一个字段 `yaml`。值是清单文件的内容。
2. 清单是 Kubernetes YAML。JSON 也是合法 YAML。
3. 一个请求可以包含多个文档，用 `---` 分隔。
4. 清单必须包含 `metadata.namespace`。缺少时资源进入 `default`。
5. 资源存在时，Rancher 更新它。资源不存在时，Rancher 创建它。
6. 响应为空对象或空响应体时，表示请求成功。
7. HTTP 422 表示清单校验失败。错误消息在响应体。
8. 脚本先读项目动作。项目没有时，脚本读集群的 `/v3/clusters/<集群ID>` 动作。两者都没有时，脚本报错。

### 6.2 删除

```text
DELETE <links.remove>
```

规则：

1. 删除不可恢复。
2. HTTP 200 或 204 表示成功。
3. HTTP 404 表示资源已不存在。请用户确认状态。

### 6.3 重新部署

资源的 `actions` 包含 `redeploy` 时：

```text
POST <actions.redeploy>
```

规则：

1. 请求没有请求体。
2. Deployment、StatefulSet 和 DaemonSet 通常有 `redeploy`。
3. Job 是否有 `redeploy` 取决于 Rancher 版本。
4. 资源没有 `redeploy` 时，脚本报错并列出可用动作。
5. 重建 Job 的步骤：用 `get --yaml` 导出清单，用 `delete` 删除，用 `apply` 重新创建。

## 7. 错误响应

Rancher 的错误响应是 JSON：

```json
{"type": "error", "status": "401", "message": "must authenticate"}
```

| 状态码 | 含义 | 处理 |
| --- | --- | --- |
| 401 | 认证失败 | 检查 Access Key 和 Secret Key |
| 403 | 权限不足 | 检查 API Key 的 scope 和项目权限 |
| 404 | 地址或资源不存在 | 检查项目 ID、集合地址和资源名称 |
| 422 | 清单或参数校验失败 | 查看 `message` 字段 |
| 500 | Rancher 内部错误 | 稍后重试。不要重复提交写操作 |

## 8. 集群级 token

Rancher 的 scope token 只能访问指定集群的 **Kubernetes API**。它不能访问 `/v3` API。
访问 `/v3` 时，Rancher 返回 401，不是 403。

脚本使用 Rancher 的 Kubernetes API 代理：

```text
https://rancher.example.com/k8s/clusters/<集群ID>
```

获取集群 ID 和项目 ID 的步骤见 `SKILL.md` 的 2.2 节。

代理下的端点：

| 资源 | 路径 |
| --- | --- |
| 命名空间 | `/api/v1/namespaces` |
| Deployment | `/apis/apps/v1/[namespaces/<命名空间>/]deployments` |
| StatefulSet | `/apis/apps/v1/[namespaces/<命名空间>/]statefulsets` |
| DaemonSet | `/apis/apps/v1/[namespaces/<命名空间>/]daemonsets` |
| Job | `/apis/batch/v1/[namespaces/<命名空间>/]jobs` |
| ConfigMap | `/api/v1/[namespaces/<命名空间>/]configmaps` |
| Secret | `/api/v1/[namespaces/<命名空间>/]secrets` |
| Service | `/api/v1/[namespaces/<命名空间>/]services` |
| PVC | `/api/v1/[namespaces/<命名空间>/]persistentvolumeclaims` |
| Ingress | `/apis/networking.k8s.io/v1/[namespaces/<命名空间>/]ingresses` |

规则：

1. 省略命名空间时，脚本请求全部命名空间。
2. 命名空间与项目的对应关系来自注解 `field.cattle.io/projectId`。
3. 脚本用该注解过滤 `namespaces` 的输出。
4. 集群级模式下，脚本不调用 `/v3`。

### 8.1 应用清单

集群级模式使用 server-side apply：

```text
PATCH <集合路径>/<名称>?fieldManager=rc2-deploy&force=true
Content-Type: application/apply-patch+yaml

<清单原文>
```

规则：

1. 清单必须包含 `metadata.name` 和 `metadata.namespace`。
2. 一个请求处理一个文档。多个文档由 `---` 分隔。
3. JSON 清单也是合法 YAML。
4. HTTP 422 表示字段校验失败。
5. Rancher 代理把请求转发给集群的 kube-apiserver。

### 8.2 重新部署

集群级模式使用 JSON merge patch。它在 Pod 模板上加时间戳注解：

```text
PATCH <资源地址>?fieldManager=rc2-deploy
Content-Type: application/merge-patch+json

{"spec":{"template":{"metadata":{"annotations":{"cattle.io/timestamp":"<RFC3339>"}}}}}
```

规则：

1. 该操作对 Deployment、StatefulSet 和 DaemonSet 有效。
2. Job 不支持该操作。用 `delete` 和 `apply` 重建 Job。
3. 注解变化触发新的 rollout。

### 8.3 导出 YAML

1. Kubernetes API 返回 JSON。
2. 脚本删除只读字段，例如 `status`、`metadata.uid`、`metadata.resourceVersion`、
   `metadata.generation`、`metadata.creationTimestamp`、`metadata.managedFields`。
3. 脚本删除 `spec.clusterIP`、`spec.clusterIPs`、`spec.volumeName` 等只读字段。
4. 脚本把结果转成 YAML。

## 9. 版本差异

| 项目 | Rancher 2.4 | Rancher 2.5 及以上 |
| --- | --- | --- |
| 工作负载集合 | `/v3/project/<项目ID>/workloads` | `/v3/projects/<项目ID>/deployments` 等 |
| 命名空间 | 项目或集群范围 | 项目或集群范围 |
| 认证 | Basic | Basic |
| 集群级 token | 使用 `/k8s/clusters/<集群ID>` | 使用 `/k8s/clusters/<集群ID>` |

脚本先尝试新地址，再尝试旧地址。因此两种情况都能工作。

## 10. 命令示例

```bash
# 列出集群
curl -s -u '<Access Key>:<Secret Key>' 'https://rancher.example.com/v3/clusters'

# 列出项目
curl -s -u '<Access Key>:<Secret Key>' 'https://rancher.example.com/v3/projects?clusterId=c-abc123'

# 重新部署
curl -s -u '<Access Key>:<Secret Key>' -X POST \
  'https://rancher.example.com/v3/projects/c-abc123:p-def456/deployments/deployment:prod:api-server?action=redeploy'
```

## 11. 获取集群 ID 和项目 ID

### 11.1 集群 ID

方法 1：从集群页面获取。

1. 登录 Rancher。
2. 点左上角 ☰。
3. 选「集群管理」（Cluster Management）。
4. 点目标集群的名称。
5. 看浏览器地址。地址包含 `/c/c-xxxxx` 或 `/dashboard/c/c-xxxxx`。
6. `c-xxxxx` 就是集群 ID。

方法 2：从 API 地址获取。

1. 在集群行的右侧点 ⋮。
2. 选「在 API 中查看」（View in API）。
3. 页面地址包含 `/v3/clusters/c-xxxxx`。
4. `c-xxxxx` 就是集群 ID。

### 11.2 项目 ID

1. 进入目标项目。
2. 看浏览器地址。地址形如 `https://rancher.example.com/p/c-xxxxx:p-yyyy/`。
3. `/p/` 后面的部分就是项目 ID。
4. 项目 API 地址是 `<Rancher 地址>/v3/projects/<项目 ID>`。

### 11.3 规则

1. 集群级 token 的 scope 只填集群 ID。格式为 `cluster:c-xxxxx`。
2. 不要把项目 ID 填进 scope。
3. Token 创建后 scope 不能修改。填错就删除 Key 并重建。
4. 无 scope token 可以用 `clusters` 和 `projects` 查看 ID。
5. 集群级 token 的 `clusters` 只显示 scope 集群。
6. 集群级 token 不能读取项目名。用 `remember` 命令补充名称。