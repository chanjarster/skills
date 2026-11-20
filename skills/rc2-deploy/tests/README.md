# rc2-deploy 测试

使用本地 mock Rancher（`http.server`）验证脚本逻辑，不依赖真实 Rancher。

## 运行

```bash
cd skills/rc2-deploy

# 已安装 pytest
pytest tests/test_rc2.py -v

# 未安装 pytest，用 uv 临时准备环境
uv run --with pytest pytest tests/test_rc2.py -v
```

## 覆盖范围

测试在临时目录下创建 `~/.rc2-deploy/servers/*.json`，并通过 `--project` 提供项目地址。
mock 服务器实现这些端点：

- `/v3/clusters`（分页）、`/v3/projects`（含 `clusterId` 过滤）
- `/v3/projects/<项目ID>`（项目资源，含 `importYaml` 动作）
- `/v3/projects/<项目ID>/namespaces`
- `/v3/projects/<项目ID>/deployments`、`jobs`、`configmaps`、`secrets`、`ingresses`、
  `services`、`persistentvolumeclaims`
- `/v3/projects/<项目ID>/statefulSets`（验证大小写回退）
- `/v3/project/<项目ID>/workloads`（验证 Rancher 2.4 旧地址回退）
- 资源 `links.self`、`links.remove`、`links.yaml`，以及 `actions.redeploy`、`actions.importYaml`

具体断言：

- `normalize_server_url`：补 `/v3`、去尾斜杠、拒绝项目地址
- `parse_ref`：`类型/名称`、`类型:命名空间:名称`、别名、错误写法
- 配置：`config add` 写入 600 文件且不回显 Secret、拒绝项目地址、`list` 打码、
  `show`、`use`、`remove`、无配置时的提示
- 项目记忆：成功后自动记录、`--no-save`、失败不记录、按序号和短 ID 选择、
  多个项目时要求选择、项目 ID 配合默认配置、地址不匹配报错
- 项目名称：记录/读取名称、名称更新不覆盖、按名称选择（不区分大小写）、
  `remember` 命令、`saved` 显示名称、无 scope token 自动记录 API 项目名
- `clusters`：分页合并、`--json`
- `projects`：表格、`--cluster` 过滤
- `namespaces`：列出命名空间、使用已记录项目
- `ls`：deployment（表格与 `--json`）、statefulset 大小写回退、daemonset 旧地址回退、
  job、configmap、secret（不显示值）、ingress、service、pvc、未知类型、空命名空间
- `get`：摘要、`--json`、`--yaml`、跨命名空间歧义、资源不存在、Secret 打码与 `--reveal`
- `apply`：`--dry-run` 不提交、提交 YAML 文本、文件不存在、文件为空
- `delete`：发送 DELETE、`--dry-run` 不提交
- `redeploy`：发送动作请求、Job 无动作时报错、`--dry-run`
- 错误路径：HTTP 401

## 集群级 scope token

mock 服务器同时实现了 `/k8s/clusters/c-wq9nv/...` 的挂载：

- `/version`、`/api/v1/namespaces`
- `/apis/apps/v1/...`、`/apis/batch/v1/...`、`/apis/networking.k8s.io/v1/...`
- `/api/v1/...` 的 configmap、secret、service、pvc
- `PATCH`（server-side apply 与 merge patch）、`DELETE`

断言：

- `config add` 写入 scope；`config list` 显示 scope
- `clusters` 只显示 scope 集群；`projects` 拒绝并提示
- `namespaces` 按 `field.cattle.io/projectId` 过滤
- `ls` 九种资源、`--namespace` 过滤、Secret 不泄露值
- `get` 摘要、`--json` 打码与 `--reveal`、`--yaml` 删除只读字段
- `apply`：SSA 路径、Content-Type、多文档、缺命名空间报错、未知 kind 报错、`--dry-run`
- `redeploy`：merge patch 的注解与 Content-Type；Job 拒绝；`--dry-run`
- `delete`：DELETE 路径
- 项目不属于 scope 集群时报错；项目地址自动记录；401 提示 scope