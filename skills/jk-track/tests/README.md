# jk-track 测试

使用本地 mock Jenkins（`http.server`）验证脚本逻辑，不依赖真实 Jenkins。

## 运行

```bash
cd skills/jk-track

# 已安装 pytest
pytest tests/test_jk.py -v

# 未安装 pytest，用 uv 临时准备环境
uv run --with pytest pytest tests/test_jk.py -v
```

## 覆盖范围

测试在临时目录下创建 `~/.jk-track/servers/*.json`（每个 Jenkins 一个配置文件），
命令里通过 `--job <URL>` 提供 job，验证按主机自动匹配配置。

- job URL 归一化（构建页、`api/json`、`consoleText`、`#N`、文件夹、server + job 名称）
- 配置匹配：按根地址自动匹配、context path 最长匹配、同主机多配置歧义、`--server` 强制、
  `--server` 与 URL 不匹配时拒绝、相对 job 名称使用默认 server、无配置时的提示
- 日志错误摘录与去重后的日志尾部
- `info`（最新 / 指定 #N / `--json` / 输出 URL）、`history`（`--limit` / `--all` / 最新标记）、
  `jobs`（子 job 列表 / 叶子 job / `--json`）、`log`（`--tail` / `--grep`）、`tests`、`failure`
- `--child` 选择子 job：按名称定位、分支名含 `/`、不存在的子 job、叶子 job 上误用
- `watch`：最新构建已结束、指定构建失败、运行中→成功、`--next` 排队等待、等待超时（退出码 3）
- `trigger`：无参数 `build`、带参数 `buildWithParameters`、CSRF crumb、队列轮询、`--wait` 后报告、
  `--child` 触发子 job、`--dry-run` 不提交、`--json`、容器 job 拒绝、非法参数
- `stop`：停止运行中构建、已结束构建提示无需停止、`--wait` 等到 `ABORTED`、`--dry-run`、`--json`、
  容器 job 拒绝
- 凭据：`config add` 交互写入与 600 权限、拒绝把 job URL 当根地址、`config list/show` 掩码、
  `config use/remove`、`config set` 别名
- 项目 `.jenkins-jobs`：自动记录与去重、`--no-save`、`--jobs-file`、注释/空行忽略、
  省略 `--job` 时使用唯一 URL、按序号/短名称选择、`saved` 列表
- 错误路径：401 认证失败、不属于任何配置的 job URL、多个配置歧义

mock 服务器实现了这些端点：`/job/demo/api/json`、`/job/demo/{N}/api/json`、
`/job/demo/{N}/wfapi/describe`、`/job/demo/{N}/consoleText`、`/job/demo/{N}/testReport/api/json`、
容器 job `/job/multi/api/json`（含 3 个子 job）及其子 job 端点。
`/jenkins-bad/...` 前缀用于模拟同一主机上的另一套（错误）凭据。
