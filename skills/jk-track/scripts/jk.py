#!/usr/bin/env python3
"""jk-track: 查询和跟踪 Jenkins job 的构建情况。

子命令:
    info      查看构建概况（最新构建或指定 #N）
    history   查看构建历史（--all 列出全部）
    jobs      列出 job 类型和子 job（多分支流水线/组织文件夹/文件夹）
    log       查看构建日志（支持 grep / tail / 保存到文件）
    tests     查看测试报告
    failure   汇总失败原因（失败阶段 + 失败用例 + 日志错误摘录）
    watch     持续跟踪构建，直到结束并输出报告
    config    管理 server 配置（add/list/show/use/remove）
    token-url 生成个人设置页地址（用于配置 API Token，别名 token）
    saved     列出项目 .jenkins-jobs 中记录的 job（别名 urls）

配置与 job 选择:
    每个 Jenkins 一个配置文件：~/.jk-track/servers/<名称>.json，只存根地址、用户名和 API Token。
    由用户在自己的终端执行 `config add` 交互写入，不要在对话中传用户名/API Token。
    job URL 通过 --job 提供，脚本按主机（及根路径）自动匹配对应配置；
    查询成功后 URL 会自动记录到项目目录的 .jenkins-jobs（纯文本，可用序号/短名称复用）。

job 类型:
    多分支流水线的根 job 不产生构建。先运行 jobs 确认类型，再用分支 job 查询。

用法示例:
    python jk.py config add ci        # 由用户本人在终端执行
    python jk.py token-url --job https://ci.example.com/job/my-app/
    python jk.py info --job https://ci.example.com/job/my-app/
    python jk.py history --job https://ci.example.com/job/my-app/ --all
    python jk.py jobs --job https://ci.example.com/job/my-app/
    python jk.py info --job https://ci.example.com/job/my-app/job/main/ --build 128
    python jk.py info --job https://ci.example.com/job/my-app/ --child main
    python jk.py watch --job https://ci.example.com/job/my-app/ --next
    python jk.py log --job https://ci.example.com/job/my-app/ --grep ERROR

退出码:
    0  成功 / 查询完成
    1  watch 结束时构建结果不是 SUCCESS
    2  参数、认证或网络错误
    3  watch 等待超时（构建仍在运行）
"""

import argparse
import base64
import functools
import getpass
import json
import os
import re
import ssl
import stat
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

VERSION = "1.0"
CONFIG_DIR = Path.home() / ".jk-track"
SERVERS_DIR = CONFIG_DIR / "servers"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
JOBS_FILE_NAME = ".jenkins-jobs"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

DEFAULT_INTERVAL = 15.0
DEFAULT_WATCH_TIMEOUT = 3600.0

JOB_TREE = (
    "_class,name,displayName,fullName,url,color,inQueue,queueItem[id,why,blocked,stuck],"
    "nextBuildNumber,"
    "lastBuild[number,url,building,result,duration,estimatedDuration,timestamp],"
    "lastCompletedBuild[number,url,building,result,duration,estimatedDuration,timestamp],"
    "lastFailedBuild[number],lastSuccessfulBuild[number]"
)
WATCH_TREE = (
    "_class,name,displayName,fullName,inQueue,queueItem[id,why],"
    "lastBuild[number,url,building,result,duration,estimatedDuration,timestamp]"
)
RUNNING_BUILD_TREE = (
    "number,building,result,duration,estimatedDuration,timestamp,url"
)

RESULT_LABELS = {
    "SUCCESS": ("✅", "成功"),
    "UNSTABLE": ("⚠️", "不稳定"),
    "FAILURE": ("❌", "失败"),
    "ABORTED": ("⛔", "已中止"),
    "NOT_BUILT": ("⏭️", "未执行"),
}
STAGE_ICONS = {
    "SUCCESS": "✅",
    "FAILED": "❌",
    "IN_PROGRESS": "▶",
    "NOT_EXECUTED": "·",
    "PAUSED": "⏸",
    "UNSTABLE": "⚠️",
    "ABORTED": "⛔",
}
REF_KEYS = {
    "latest": "lastBuild",
    "last": "lastBuild",
    "lastbuild": "lastBuild",
    "lastcompleted": "lastCompletedBuild",
    "lastcompletedbuild": "lastCompletedBuild",
    "lastfailed": "lastFailedBuild",
    "lastfailedbuild": "lastFailedBuild",
    "lastsuccessful": "lastSuccessfulBuild",
    "lastsuccessfulbuild": "lastSuccessfulBuild",
}

ERROR_LINE_RE = re.compile(
    r"(?i)("
    r"traceback \(most recent call last\)|caused by:|build failed|\[error\]|npm err!|"
    r"error ts\d+|assertionerror|exception in thread|java\.lang\.[\w.$]*exception|"
    r"\bfailed\b|\bfailure\b|\bfatal\b|panic:|script returned exit code|"
    r"##\[error\]|\berror\b"
    r")"
)

COLOR_ENABLED = True


# ────────────────────────────────────────────────────────────
# 异常与工具函数
# ────────────────────────────────────────────────────────────


class JenkinsError(Exception):
    """可预期的错误，携带进程退出码。"""

    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


def colorize(text, code, stream=None):
    stream = stream or sys.stdout
    if not COLOR_ENABLED or not stream.isatty():
        return text
    return f"\x1b[{code}m{text}\x1b[0m"


def redact(url):
    """去掉 URL 中的用户信息和 token 参数，避免泄露凭据。"""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    netloc = p.netloc.split("@", 1)[-1]
    query = re.sub(r"(?i)(token|api[_-]?token|password)=[^&]*", r"\1=***", p.query)
    path = urllib.parse.quote(p.path, safe="/%:@!$&'()*+,;=~-._")
    return urllib.parse.urlunsplit((p.scheme, netloc, path, query, ""))


def strip_ansi(text):
    return ANSI_RE.sub("", text)


def disp_width(text):
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def pad(text, width):
    return text + " " * max(0, width - disp_width(text))


def fmt_duration(ms):
    if ms is None:
        return "-"
    if 0 < ms < 1000:
        return "<1秒"
    seconds = max(0, int(ms / 1000))
    if seconds < 60:
        return f"{seconds}秒"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}分{seconds:02d}秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}时{minutes:02d}分"


def fmt_clock(seconds):
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def fmt_time(ms):
    if not ms:
        return "-"
    return datetime.fromtimestamp(ms / 1000).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def iso_time(ms):
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000).astimezone().isoformat(timespec="seconds")


def result_label(result, building=False):
    if building:
        return "▶️", "运行中"
    return RESULT_LABELS.get(result, ("❔", result or "未知"))


def first_line(text, limit=200):
    text = (text or "").strip()
    if not text:
        return ""
    line = text.splitlines()[0].strip()
    return line[:limit] + ("…" if len(line) > limit else "")


def job_display(url):
    """只保留 job 名称，用于在输出中代替完整 URL。"""
    if not url:
        return "-"
    path = urllib.parse.urlsplit(url).path
    parts = [urllib.parse.unquote(p) for p in path.split("/") if p and p != "job"]
    return "/".join(parts[-2:]) if parts else "(已配置)"


# ────────────────────────────────────────────────────────────
# 认证与 Job URL
# ────────────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────
# Jenkins server 配置（每个 server 一个配置文件）
# ────────────────────────────────────────────────────────


SCRIPT_HINT = "python " + str(Path(__file__).resolve())
SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def config_instruction(name=None, action="config add"):
    target = f" {name}" if name else ""
    return (
        "请让用户在他/她自己的终端执行下面的命令完成配置（不要把令牌贴到对话里）：\n"
        f"  {SCRIPT_HINT} {action}{target}"
    )


def server_file(name):
    return SERVERS_DIR / f"{name}.json"


def validate_server_name(name):
    if not name or not SERVER_NAME_RE.match(name):
        raise JenkinsError("server 配置名称只能包含字母、数字、点、下划线和短横线")
    return name


def user_config_url(server_base, user):
    """个人设置页地址：<根地址>/user/<用户名>/configure（API Token 入口）。"""
    return f"{server_base.rstrip('/')}/user/{urllib.parse.quote(user, safe='')}/configure"


@functools.lru_cache(maxsize=1)
def load_server_configs():
    """读取 ~/.jk-track/servers/*.json，返回 {名称: {server,user,token}}。"""
    configs = {}
    if not SERVERS_DIR.is_dir():
        return configs
    for path in sorted(SERVERS_DIR.glob("*.json")):
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"警告: 无法读取 {path}: {exc}", file=sys.stderr)
            continue
        if not isinstance(raw, dict) or not raw.get("server"):
            print(f"警告: {path} 缺少 server 字段，已忽略", file=sys.stderr)
            continue
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            print(
                f"警告: {path} 权限过宽（{oct(mode)}），建议执行 chmod 600",
                file=sys.stderr,
            )
        configs[path.stem] = {
            "server": raw["server"].rstrip("/"),
            "user": raw.get("user"),
            "token": raw.get("token"),
        }
    return configs


def save_server_config(name, server, user, token):
    validate_server_name(name)
    SERVERS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass
    path = server_file(name)
    tmp = path.with_name(path.name + ".tmp")
    payload = {"server": server.rstrip("/"), "user": user, "token": token}
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    load_server_configs.cache_clear()


def delete_server_config(name):
    path = server_file(name)
    if not path.exists():
        raise JenkinsError(f"server 配置「{name}」不存在")
    path.unlink()
    load_server_configs.cache_clear()


@functools.lru_cache(maxsize=1)
def load_settings():
    try:
        data = json.loads(SETTINGS_PATH.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(data):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass
    SETTINGS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    os.chmod(SETTINGS_PATH, 0o600)
    load_settings.cache_clear()


def default_server_name():
    return os.environ.get("JK_SERVER") or load_settings().get("default_server")


def select_server_config(name):
    configs = load_server_configs()
    if not configs:
        raise JenkinsError("还没有任何 Jenkins 配置。\n" + config_instruction())
    config = configs.get(name)
    if not config:
        raise JenkinsError(
            f"server 配置「{name}」不存在，现有配置：{'、'.join(configs)}\n"
            + config_instruction(name)
        )
    return config


def resolve_auth(server_name, config):
    user = config.get("user") or os.environ.get("JENKINS_USER")
    token = config.get("token") or os.environ.get("JENKINS_TOKEN")
    if not user or not token:
        raise JenkinsError(
            f"server 配置「{server_name}」缺少用户名或 API Token。\n"
            + config_instruction(server_name)
        )
    return user, token


SUFFIX_PATTERNS = [
    r"/(?:lastBuild|lastCompletedBuild|lastFailedBuild|lastSuccessfulBuild|"
    r"lastStableBuild|lastUnstableBuild|lastUnsuccessfulBuild|firstBuild)$",
    r"/\d+$",
    r"/(?:console|consoleText|consoleFull)$",
    r"/(?:logText|progressiveText|progressiveHtml)(?:/.*)?$",
    r"/(?:api|wfapi)(?:/.*)?$",
    r"/(?:testReport|pipeline-graph|flowGraph|stageView|artifact)(?:/.*)?$",
    r"/display/(?:redirect|console)$",
]


def normalize_job_url(raw, server=None):
    """把 job 页面/构建页面 URL 归一化为 job URL。"""
    raw = (raw or "").strip()
    if not raw:
        raise JenkinsError("缺少 job URL：请提供 job 的完整 URL")

    # 支持 "my-app" / "folder/my-app" 形式 + server
    if "://" not in raw:
        base = (server or "").strip()
        if not base:
            raise JenkinsError(
                f"job「{raw}」不是完整 URL，请提供完整 URL，或配合 --server 使用 job 名称"
            )
        segments = [s for s in re.split(r"[/\\]+", raw) if s]
        raw = base.rstrip("/") + "".join(
            f"/job/{urllib.parse.quote(s)}" for s in segments
        )

    raw = raw.split("#", 1)[0]
    parsed = urllib.parse.urlsplit(raw)
    path = parsed.path.rstrip("/")
    for _ in range(10):
        stripped = False
        for pattern in SUFFIX_PATTERNS:
            candidate = re.sub(pattern, "", path)
            # 保留 ".../job" 层级的路径，避免把名字叫 api/console/123 的 job 误删
            if candidate != path and candidate and not candidate.endswith("/job"):
                path, stripped = candidate, True
                break
        if not stripped:
            break
    if not path:
        raise JenkinsError("无法从 job URL 中解析路径")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


# ────────────────────────────────────────────────────────────
# Jenkins HTTP 客户端
# ────────────────────────────────────────────────────────────


class JenkinsClient:
    def __init__(
        self,
        user,
        token,
        insecure=False,
        http_timeout=30.0,
        verbose=False,
        server_name=None,
        server_base=None,
    ):
        self.user = user
        self.server_name = server_name
        self.server_base = (server_base or "").rstrip("/")
        self._auth = "Basic " + base64.b64encode(f"{user}:{token}".encode()).decode()
        self.http_timeout = http_timeout
        self.verbose = verbose
        self._crumb_loaded = False
        self._crumb = None
        context = ssl.create_default_context()
        if insecure:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context)
        )

    @staticmethod
    def _to_uri(url):
        p = urllib.parse.urlsplit(url)
        path = urllib.parse.quote(p.path, safe="/%:@!$&'()*+,;=~-._")
        query = urllib.parse.quote(p.query, safe="=&%:@!$'()*+,;/?[]{}~-._")
        return urllib.parse.urlunsplit((p.scheme, p.netloc, path, query, ""))

    def _open(self, url):
        request = urllib.request.Request(
            self._to_uri(url),
            headers={
                "Authorization": self._auth,
                "Accept": "application/json, text/plain, */*",
                "User-Agent": f"jk-track/{VERSION}",
            },
        )
        if self.verbose:
            print(f"  HTTP GET {redact(url)}", file=sys.stderr)
        return self._opener.open(request, timeout=self.http_timeout)

    def get(self, url, ok404=False, retries=2):
        last_error = None
        for attempt in range(retries + 1):
            try:
                with self._open(url) as response:
                    return response.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as exc:
                if exc.code == 404 and ok404:
                    return None
                if exc.code in (401, 403):
                    server = (
                        f"server 配置「{self.server_name}」的"
                        if self.server_name
                        else ""
                    )
                    raise JenkinsError(
                        f"认证失败（HTTP {exc.code}）：{server}用户名或 API Token 可能已失效。\n"
                        + config_instruction(self.server_name)
                        + f"\n  URL: {redact(url)}"
                    )
                body = exc.read().decode("utf-8", errors="replace").strip()[:300]
                if exc.code >= 500 and attempt < retries:
                    last_error = exc
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise JenkinsError(
                    f"HTTP {exc.code}: {redact(url)}" + (f"\n  {body}" if body else "")
                )
            except urllib.error.URLError as exc:
                last_error = exc
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise JenkinsError(f"网络错误: {exc.reason}\n  URL: {redact(url)}")
            except TimeoutError:
                last_error = "timeout"
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise JenkinsError(f"请求超时: {redact(url)}")
        raise JenkinsError(f"请求失败: {redact(url)}（{last_error}）")

    def get_json(self, url, ok404=False):
        text = self.get(url, ok404=ok404)
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise JenkinsError(
                "返回内容不是 JSON，job URL 可能配错或指向登录页"
                f": {redact(url)}"
            )

    def _get_crumb(self):
        """读取 CSRF crumb（Jenkins 开启 CSRF 保护时需要）。"""
        if self._crumb_loaded:
            return self._crumb
        self._crumb_loaded = True
        self._crumb = None
        if not self.server_base:
            return None
        try:
            data = self.get_json(f"{self.server_base}/crumbIssuer/api/json", ok404=True)
        except JenkinsError:
            return None
        if isinstance(data, dict) and data.get("crumb"):
            field = data.get("crumbRequestField") or "Jenkins-Crumb"
            self._crumb = (field, data["crumb"])
        return self._crumb

    def post(self, url, data=None, _retried=False):
        """发送 POST。注意：不做自动重试，避免重复触发构建。"""
        headers = {
            "Authorization": self._auth,
            "Accept": "application/json, text/plain, */*",
            "User-Agent": f"jk-track/{VERSION}",
        }
        body = b""
        if data:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = urllib.parse.urlencode(data).encode("utf-8")
        crumb = self._get_crumb()
        if crumb:
            headers[crumb[0]] = crumb[1]
        request = urllib.request.Request(
            self._to_uri(url), data=body, headers=headers, method="POST"
        )
        if self.verbose:
            print(f"  HTTP POST {redact(url)}", file=sys.stderr)
        try:
            with self._opener.open(request, timeout=self.http_timeout) as response:
                headers_out = {k: v for k, v in response.headers.items()}
                response.read()
                return response.status, headers_out
        except urllib.error.HTTPError as exc:
            if exc.code == 403 and not _retried:
                # crumb 可能过期，刷新后只重试一次
                self._crumb_loaded = False
                self._crumb = None
                return self.post(url, data, _retried=True)
            body_text = exc.read().decode("utf-8", errors="replace").strip()[:300]
            if exc.code in (401, 403):
                raise JenkinsError(
                    f"触发失败（HTTP {exc.code}）：凭据无效、无构建权限，或 CSRF 校验未通过。\n"
                    + config_instruction(self.server_name)
                    + f"\n  URL: {redact(url)}"
                    + (f"\n  {body_text}" if body_text else "")
                )
            raise JenkinsError(
                f"触发失败（HTTP {exc.code}）: {redact(url)}"
                + (f"\n  {body_text}" if body_text else "")
            )
        except urllib.error.URLError as exc:
            raise JenkinsError(
                f"网络错误: {exc.reason}。构建请求可能已经提交，请先用 info 确认最新构建，不要直接重复触发。"
            )
        except TimeoutError:
            raise JenkinsError(
                "触发请求超时。构建可能已经提交，请先用 info 确认最新构建，不要直接重复触发。"
            )


# ────────────────────────────────────────────────────────────
# Jenkins 数据读取
# ────────────────────────────────────────────────────────────


JOB_KIND_MULTIBRANCH = "multibranch"
JOB_KIND_ORGANIZATION_FOLDER = "organization-folder"
JOB_KIND_FOLDER = "folder"
JOB_KIND_CONTAINER = "container"
JOB_KIND_LEAF = "leaf"
JOB_KIND_LABELS = {
    JOB_KIND_MULTIBRANCH: "多分支流水线",
    JOB_KIND_ORGANIZATION_FOLDER: "组织文件夹",
    JOB_KIND_FOLDER: "文件夹",
    JOB_KIND_CONTAINER: "容器 job",
    JOB_KIND_LEAF: "普通 job",
}


def job_kind(job):
    """判定 job 类型，返回 JOB_KIND_* 之一。

    多分支流水线的根 job 不产生构建，优先用 _class 识别。
    _class 缺失或无法识别时，退回用 jobs 字段判断是否为容器。
    """
    if not isinstance(job, dict):
        return JOB_KIND_LEAF
    cls = str(job.get("_class") or "").lower()
    if "multibranch" in cls:
        return JOB_KIND_MULTIBRANCH
    if "organizationfolder" in cls:
        return JOB_KIND_ORGANIZATION_FOLDER
    if cls.endswith("folder"):
        return JOB_KIND_FOLDER
    if isinstance(job.get("jobs"), list):
        # 有子 job 但 _class 无法识别，归为容器
        return JOB_KIND_CONTAINER
    return JOB_KIND_LEAF


def is_container_job(job):
    return job_kind(job) != JOB_KIND_LEAF


def leaf_job_label(job):
    """为可构建的叶子 job 给出更具体的类型名称。"""
    cls = str((job or {}).get("_class") or "").lower()
    if "workflowjob" in cls:
        return "流水线 job"
    if "freestyleproject" in cls:
        return "自由风格 job"
    if "matrixproject" in cls:
        return "矩阵 job"
    if "externaljob" in cls:
        return "外部 job"
    return JOB_KIND_LABELS[JOB_KIND_LEAF]


def container_error_message(client, job_url, job=None):
    """为多分支流水线/组织文件夹/文件夹生成带子 job 列表的提示信息。"""
    kind = job_kind(job or {})
    parent = (job or {}).get("fullName") or (job or {}).get("name") or job_display(job_url)
    children = []
    try:
        data = client.get_json(
            f"{job_url}/api/json?"
            + urllib.parse.urlencode(
                {"tree": "_class,name,fullName,url,jobs[name,displayName,url]"}
            )
        )
        if isinstance(data, dict):
            if kind == JOB_KIND_LEAF:
                kind = job_kind(data)
            parent = data.get("fullName") or data.get("name") or parent
            children = data.get("jobs") or []
    except JenkinsError:
        pass

    if kind == JOB_KIND_MULTIBRANCH:
        lines = [
            f"「{parent}」是多分支流水线（Multibranch Pipeline）的根 job，它本身不产生构建。",
            f"它的子 job 是各个分支，共 {len(children)} 个：",
        ]
    elif kind == JOB_KIND_ORGANIZATION_FOLDER:
        lines = [
            f"「{parent}」是组织文件夹（Organization Folder），按仓库自动扫描子 job，本身不产生构建。",
            f"它的子 job 共 {len(children)} 个：",
        ]
    elif kind == JOB_KIND_FOLDER:
        lines = [
            f"「{parent}」是文件夹（Folder），它本身不产生构建，包含 {len(children)} 个子 job：",
        ]
    else:
        lines = [
            f"「{parent}」是容器 job（文件夹或多分支），它本身不产生构建，包含 {len(children)} 个子 job：",
        ]

    for child in children[:10]:
        lines.append(f"  - {child.get('name')}")
    if len(children) > 10:
        lines.append(f"  … 其余 {len(children) - 10} 个")

    if not children:
        lines.append("  （暂无子 job）")
        if kind == JOB_KIND_MULTIBRANCH:
            lines.append("")
            lines.append(
                "分支列表为空：可能还没有扫描到分支。"
                "请到该 job 页面执行 Scan Multibranch Pipeline 后重试。"
            )
        elif kind == JOB_KIND_ORGANIZATION_FOLDER:
            lines.append("")
            lines.append(
                "子 job 列表为空：可能还没有扫描到仓库。"
                "请到该 job 页面执行 Scan Organization Folder 后重试。"
            )
        lines.append(f'再次确认：{SCRIPT_HINT} jobs --job "{job_url}"')
        return "\n".join(lines)

    lines.append("")
    if kind in (JOB_KIND_MULTIBRANCH, JOB_KIND_ORGANIZATION_FOLDER):
        first_url = children[0].get("url")
        first_name = children[0].get("name")
        lines.append("请先选择分支，用分支的 URL 查询：")
        lines.append(f'  {SCRIPT_HINT} info --job "{first_url}"')
        lines.append(
            "或用 --child 指定分支名（分支名含 / 时整体传入，例如 --child feature/login）："
        )
        lines.append(f'  {SCRIPT_HINT} info --job "{job_url}" --child "{first_name}"')
    else:
        lines.append("请选择一个子 job，用 --child 指定，或直接使用子 job 的 URL：")
        lines.append(f'  {SCRIPT_HINT} info --job "{children[0].get("url")}"')
    lines.append(f'列出全部子 job：{SCRIPT_HINT} jobs --job "{job_url}"')
    server_name = getattr(client, "server_name", None) or "<server>"
    lines.append(f"（当前匹配到的 server 配置：{server_name}）")
    return "\n".join(lines)


def fetch_job(client, job_url, tree=None):
    url = job_url.rstrip("/") + "/api/json"
    if tree:
        url += "?" + urllib.parse.urlencode({"tree": tree})
    data = client.get_json(url)
    if not isinstance(data, dict):
        raise JenkinsError(f"无法读取 job 信息: {redact(url)}")
    if is_container_job(data):
        raise JenkinsError(container_error_message(client, job_url, data))
    return data


def fetch_build(client, job_url, number):
    return client.get_json(f"{job_url}/{number}/api/json", ok404=True)


BUILD_FIELDS = "number,result,building,timestamp,duration,displayName,url"


def fetch_builds(client, job_url, limit=None):
    """读取构建列表（新→旧）。limit 为 None 时返回全部构建。"""
    tree = f"builds[{BUILD_FIELDS}]"
    if limit:
        tree += f"{{0,{limit}}}"
    data = client.get_json(
        f"{job_url}/api/json?" + urllib.parse.urlencode({"tree": tree})
    )
    builds = (data or {}).get("builds") or []
    return builds[:limit] if limit else builds


def child_jobs_tree(depth=1):
    tree = (
        "_class,name,displayName,fullName,url,color,"
        "lastBuild[number,result,building,timestamp,duration,estimatedDuration]"
    )
    if depth > 1:
        tree += f",jobs[{child_jobs_tree(depth - 1)}]"
    return tree


def fetch_child_jobs(client, job_url, depth=1):
    """读取 job 详情（含子 job）；用 job_kind 判断 job 类型。"""
    data = client.get_json(
        f"{job_url}/api/json?"
        + urllib.parse.urlencode({"tree": child_jobs_tree(depth)})
    )
    if not isinstance(data, dict):
        raise JenkinsError(f"无法读取 job 信息: {redact(job_url)}")
    return data


def resolve_build_number(job, ref):
    if ref is None or str(ref).strip() == "":
        key = "lastBuild"
    else:
        value = str(ref).strip().lstrip("#").lower()
        if value.isdigit():
            return int(value)
        key = REF_KEYS.get(value)
        if not key:
            raise JenkinsError(
                f"无法识别的构建引用「{ref}」，可用：编号（如 128）、latest、"
                "lastCompleted、lastFailed、lastSuccessful"
            )
    build = job.get(key) or {}
    if not build.get("number"):
        name = job.get("fullName") or job.get("name") or "该 job"
        if key == "lastBuild":
            raise JenkinsError(
                f"「{name}」还没有任何构建记录（{leaf_job_label(job)}）。"
                "先触发一次构建，或列出子 job 确认是否选错了分支"
            )
        raise JenkinsError(f"「{name}」没有 {key} 对应的构建")
    return int(build["number"])


def extract_parameters(build):
    params = {}
    for action in build.get("actions") or []:
        if not isinstance(action, dict):
            continue
        if isinstance(action.get("parameters"), list):
            for param in action["parameters"]:
                if isinstance(param, dict) and "name" in param:
                    params[param["name"]] = param.get("value")
    return params


def extract_causes(build):
    causes = []
    for action in build.get("actions") or []:
        if isinstance(action, dict) and isinstance(action.get("causes"), list):
            for cause in action["causes"]:
                if isinstance(cause, dict):
                    text = cause.get("shortDescription") or cause.get("description")
                    if text:
                        causes.append(text)
    return causes


def extract_changes(build):
    items = ((build.get("changeSet") or {}).get("items")) or []
    changes = []
    for item in items:
        if not isinstance(item, dict):
            continue
        message = (item.get("msg") or "").strip()
        changes.append(
            {
                "commit": (item.get("commitId") or "")[:8],
                "message": first_line(message, 100),
                "author": ((item.get("author") or {}).get("fullName") or "").strip(),
                "timestamp": item.get("timestamp"),
            }
        )
    return changes


def fetch_stages(client, job_url, number):
    """读取 Pipeline 阶段信息；非 Pipeline job 返回 None。"""
    data = client.get_json(f"{job_url}/{number}/wfapi/describe", ok404=True)
    if not isinstance(data, dict):
        return None
    stages = []
    for stage in data.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        error = stage.get("error") or {}
        stages.append(
            {
                "name": stage.get("name"),
                "status": stage.get("status"),
                "duration_ms": stage.get("durationMillis"),
                "error": error.get("message") if isinstance(error, dict) else None,
            }
        )
    return stages


def fetch_test_report(client, job_url, number):
    data = client.get_json(f"{job_url}/{number}/testReport/api/json", ok404=True)
    if not isinstance(data, dict) or "totalCount" not in data:
        return None
    failed = []
    for suite in data.get("suites") or []:
        if not isinstance(suite, dict):
            continue
        for case in suite.get("cases") or []:
            if not isinstance(case, dict):
                continue
            if case.get("status") not in ("FAILED", "REGRESSION"):
                continue
            failed.append(
                {
                    "suite": suite.get("name"),
                    "class": case.get("className"),
                    "name": case.get("name"),
                    "status": case.get("status"),
                    "duration_ms": case.get("duration"),
                    "age": case.get("age"),
                    "error": (case.get("errorDetails") or "").strip(),
                    "stack_trace": (case.get("errorStackTrace") or "").strip(),
                }
            )
    return {
        "total": data.get("totalCount"),
        "fail": data.get("failCount"),
        "pass": data.get("passCount"),
        "skip": data.get("skipCount"),
        "duration_ms": data.get("duration"),
        "failed": failed,
    }


def fetch_console(client, job_url, number):
    text = client.get(f"{job_url}/{number}/consoleText", ok404=True)
    return strip_ansi(text) if text else ""


def build_payload(client, job_url, job_name, number, with_stages=True):
    build = fetch_build(client, job_url, number)
    if build is None:
        raise JenkinsError(
            f"构建 #{number} 不存在（job: {job_display(job_url)}）"
        )
    building = bool(build.get("building"))
    payload = {
        "job": {"name": job_name, "url": job_url},
        "number": build.get("number", number),
        "display_name": build.get("displayName"),
        "result": build.get("result"),
        "building": building,
        "url": build.get("url") or f"{job_url}/{number}/",
        "started_at": fmt_time(build.get("timestamp")),
        "started_at_iso": iso_time(build.get("timestamp")),
        "duration_ms": build.get("duration"),
        "estimated_ms": build.get("estimatedDuration"),
        "built_on": build.get("builtOn") or None,
        "causes": extract_causes(build),
        "parameters": extract_parameters(build),
        "changes": extract_changes(build),
        "artifacts": [
            a.get("relativePath")
            for a in build.get("artifacts") or []
            if isinstance(a, dict) and a.get("relativePath")
        ],
        "stages": fetch_stages(client, job_url, number) if with_stages else None,
    }
    return payload


def parse_server_base(url):
    """解析 Jenkins 根地址，返回 scheme/host/port/path/netloc。"""
    raw = (url or "").strip()
    if not raw:
        raise JenkinsError("缺少 Jenkins 根地址")
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise JenkinsError(f"无效的 Jenkins 地址: {redact(url)}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return {
        "scheme": parsed.scheme,
        "host": parsed.hostname,
        "port": port,
        "path": parsed.path.rstrip("/"),
        "netloc": parsed.netloc,
    }


def normalize_server_base(raw):
    """校验并归一化 server 根地址（只允许根地址，不允许 job URL）。"""
    base = parse_server_base(raw)
    looks_like_job_url = "/job/" in base["path"] or re.search(r"/\d+$", base["path"])
    if looks_like_job_url:
        raise JenkinsError(
            "这里要填 Jenkins 根地址（例如 https://ci.example.com），不要填具体 job 的 URL"
        )
    return urllib.parse.urlunsplit(
        (base["scheme"], base["netloc"], base["path"], "", "")
    )


def job_under_server(job_url, server_url):
    job = parse_server_base(job_url)
    base = parse_server_base(server_url)
    if (job["scheme"], job["host"], job["port"]) != (
        base["scheme"],
        base["host"],
        base["port"],
    ):
        return False
    if not base["path"]:
        return True
    return job["path"] == base["path"] or job["path"].startswith(base["path"] + "/")


def match_server_config(job_url, configs):
    """根据 job URL 自动匹配 server 配置；同主机多配置时取路径最长者。"""
    matches = []
    for name, config in configs.items():
        if job_under_server(job_url, config["server"]):
            matches.append((len(parse_server_base(config["server"])["path"]), name, config))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        names = "、".join(item[1] for item in matches if item[0] == matches[0][0])
        raise JenkinsError(
            f"job URL 同时匹配多个 server 配置（{names}），请用 --server 指定其中之一"
        )
    return matches[0][1], matches[0][2]


def match_server_config_by_host(url, configs):
    """只按 scheme/host/port 匹配（用户只给了主机名、配置带 context path 时使用）。"""
    target = parse_server_base(url)
    matches = []
    for name, config in configs.items():
        base = parse_server_base(config["server"])
        if (base["scheme"], base["host"], base["port"]) == (
            target["scheme"],
            target["host"],
            target["port"],
        ):
            matches.append((len(base["path"]), name, config))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0])
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        names = "、".join(item[1] for item in matches if item[0] == matches[0][0])
        raise JenkinsError(
            f"同一主机有多个 server 配置（{names}），请用 --server 指定其中之一或在地址中带上路径"
        )
    return matches[0][1], matches[0][2]


def build_job_url(server_base, raw):
    base = server_base.rstrip("/")
    raw = (raw or "").strip()
    if raw.startswith("/"):
        return base + raw
    segments = [s for s in re.split(r"[/\\]+", raw) if s]
    return base + "".join(f"/job/{urllib.parse.quote(s)}" for s in segments)


def job_short_name(url):
    path = urllib.parse.urlsplit(url).path.rstrip("/")
    name = path.rsplit("/", 1)[-1]
    return urllib.parse.unquote(name)


def find_jobs_file(override=None):
    """找项目里的 .jenkins-jobs：优先 --jobs-file / 环境变量，否则从当前目录向上查找。"""
    if override:
        return Path(override).expanduser()
    env = os.environ.get("JK_JOBS_FILE")
    if env:
        return Path(env).expanduser()
    current = Path.cwd()
    for directory in [current, *current.parents]:
        candidate = directory / JOBS_FILE_NAME
        if candidate.is_file():
            return candidate
    return current / JOBS_FILE_NAME


def read_job_urls(path):
    try:
        lines = path.read_text("utf-8").splitlines()
    except FileNotFoundError:
        return []
    except OSError as exc:
        print(f"警告: 无法读取 {path}: {exc}", file=sys.stderr)
        return []
    urls = []
    for line in lines:
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        try:
            normalized = normalize_job_url(value)
        except JenkinsError:
            print(f"警告: 忽略 {path} 中的无效行: {value}", file=sys.stderr)
            continue
        if normalized not in urls:
            urls.append(normalized)
    return urls


def record_job_url(path, url):
    """把一个 job URL 追加到项目的 .jenkins-jobs（去重）。"""
    normalized = (url or "").rstrip("/")
    if not normalized:
        return False
    urls = read_job_urls(path)
    if normalized in urls:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not path.exists()
        with path.open("a", encoding="utf-8") as handle:
            if is_new:
                handle.write(
                    "# jk-track: 项目使用的 Jenkins job URL（每行一个，可用 # 注释）\n"
                )
            handle.write(normalized + "\n")
    except OSError as exc:
        print(f"警告: 无法写入 {path}: {exc}", file=sys.stderr)
        return False
    print(f"已记录 job URL 到 {path}", file=sys.stderr)
    return True


def resolve_saved_job(raw, saved, jobs_file, allow_names=True):
    """把 --job 的值解析为已保存 job 的 URL：支持序号和短名称。"""
    index = re.fullmatch(r"#?(\d+)", raw)
    if index:
        if not saved:
            raise JenkinsError(
                f"没有已保存的 job（{jobs_file} 为空或不存在），请用 --job <完整 URL>"
            )
        position = int(index.group(1))
        if not 1 <= position <= len(saved):
            raise JenkinsError(
                f"序号 {position} 超出范围（共 {len(saved)} 个），文件: {jobs_file}"
            )
        return saved[position - 1]

    if "://" in raw or not saved or not allow_names:
        return raw

    by_name = [url for url in saved if job_short_name(url) == raw]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        listing = "\n".join(
            f"  {i}. {url}" for i, url in enumerate(saved, 1) if url in by_name
        )
        raise JenkinsError(
            f"「{raw}」匹配到多个已保存 job，请用序号指定：\n{listing}"
        )
    suffix = raw.strip("/")
    by_suffix = [url for url in saved if url.rstrip("/").endswith("/" + suffix)]
    if len(by_suffix) == 1:
        return by_suffix[0]
    return raw


def resolve_job_target(args):
    """根据 --job / --server / 项目 .jenkins-jobs 确定 job URL 和 server 配置。"""
    jobs_path = find_jobs_file(getattr(args, "jobs_file", None))
    saved = read_job_urls(jobs_path)
    raw = (getattr(args, "job", None) or "").strip()
    configs = load_server_configs()
    if not configs:
        raise JenkinsError("还没有任何 Jenkins 配置。\n" + config_instruction())
    forced = getattr(args, "server", None)
    if not raw:
        if len(saved) == 1 and not forced:
            raw = saved[0]
        elif saved and not forced:
            listing = "\n".join(
                f"  {i}. {url}" for i, url in enumerate(saved, 1)
            )
            raise JenkinsError(
                "缺少 --job。项目 .jenkins-jobs 中已有：\n"
                + listing
                + "\n请用 --job <序号|短名称|URL> 选择，例如 --job 1"
            )
        else:
            raise JenkinsError(
                "缺少 --job：请提供 job 的完整 URL，或配合 --server 使用 job 名称"
            )
    else:
        raw = resolve_saved_job(raw, saved, jobs_path, allow_names=not forced)

    if "://" in raw:
        job_url = normalize_job_url(raw)
        if forced:
            config = select_server_config(forced)
            if not job_under_server(job_url, config["server"]):
                raise JenkinsError(
                    f"job URL 与 --server「{forced}」的根地址不匹配，已拒绝发送凭据"
                )
            return job_url, forced, config
        matched = match_server_config(job_url, configs)
        if not matched:
            listing = "\n".join(
                f"  - {name}: {config['server']}" for name, config in configs.items()
            )
            raise JenkinsError(
                "job URL 不属于任何已配置的 Jenkins：\n"
                + listing
                + "\n新增配置：\n"
                + config_instruction()
            )
        return job_url, matched[0], matched[1]

    # 相对路径：需要 --server，或在用户终端执行过 config use 设置默认 server
    name = forced or default_server_name()
    if not name:
        raise JenkinsError(
            "job 不是完整 URL，且没有默认 server。请用 --server <名称>，或让用户执行：\n"
            f"  {SCRIPT_HINT} config use <名称>"
        )
    config = select_server_config(name)
    job_url = normalize_job_url(build_job_url(config["server"], raw), config["server"])
    return job_url, name, config


def resolve_child_url(client, job_url, spec):
    """在容器 job 中按名称查找子 job URL，支持 folder/branch 逐层定位。"""
    spec = (spec or "").strip().strip("/")
    if not spec:
        return job_url
    data = fetch_child_jobs(client, job_url, 1)
    children = data.get("jobs")
    parent = data.get("fullName") or data.get("name") or job_display(job_url)
    if not isinstance(children, list) or not children:
        if job_kind(data) != JOB_KIND_LEAF:
            raise JenkinsError(container_error_message(client, job_url, data))
        raise JenkinsError(
            f"「{parent}」是{leaf_job_label(data)}，没有子 job，不能使用 --child {spec}"
        )

    def matches(child, value):
        return value in {child.get("name"), child.get("displayName")}

    def clean(url):
        return (url or "").rstrip("/")

    # 完整名称优先，避免分支名中的 / 被错误拆分
    for child in children:
        if matches(child, spec):
            return clean(child.get("url"))

    segments = [s for s in re.split(r"[/\\]+", spec) if s]
    first, rest = segments[0], segments[1:]
    for child in children:
        if not matches(child, first):
            continue
        if not rest:
            return clean(child.get("url"))
        return resolve_child_url(client, child.get("url"), "/".join(rest))

    available = "、".join(str(c.get("name")) for c in children)
    raise JenkinsError(
        f"在「{parent}」下找不到子 job「{spec}」。可用子 job：{available}\n"
        f'可运行 jobs 查看完整列表：{SCRIPT_HINT} jobs --job "{job_url}"'
    )


def resolve_job(args, client, tree=None):
    job_url = args._job_url
    child = getattr(args, "child", None)
    if child:
        job_url = resolve_child_url(client, job_url, child)
        args._job_url = job_url
    job = fetch_job(client, job_url, tree=tree or JOB_TREE)
    name = job.get("fullName") or job.get("name") or job_display(job_url)
    return job_url, job, name


# ────────────────────────────────────────────────────────────
# 输出渲染
# ────────────────────────────────────────────────────────────


def print_rows(rows, out=None):
    out = out or sys.stdout
    width = max((disp_width(str(k)) for k, _ in rows), default=0)
    for key, value in rows:
        print(f"{pad(str(key), width)}  {value}", file=out)


def render_build(payload, out=None):
    out = out or sys.stdout
    icon, label = result_label(payload["result"], payload["building"])
    rows = [
        ("Job", payload["job"]["name"]),
        ("构建", f"#{payload['number']}  {label} {icon}"),
        ("开始", payload["started_at"] or "-"),
    ]
    if payload["building"]:
        rows.append(
            (
                "已运行",
                f"{fmt_duration(payload['duration_ms'])}"
                + (
                    f"（预估 {fmt_duration(payload['estimated_ms'])}）"
                    if payload.get("estimated_ms")
                    else ""
                ),
            )
        )
    else:
        rows.append(("耗时", fmt_duration(payload["duration_ms"])))
    if payload.get("built_on"):
        rows.append(("节点", payload["built_on"]))
    if payload.get("causes"):
        rows.append(("触发", "; ".join(payload["causes"])))
    if payload.get("parameters"):
        params = "  ".join(f"{k}={v}" for k, v in payload["parameters"].items())
        rows.append(("参数", params[:400] + ("…" if len(params) > 400 else "")))

    changes = payload.get("changes") or []
    if changes:
        rows.append(("变更", f"{len(changes)} 个提交"))
        for item in changes[:5]:
            commit = item.get("commit") or "-"
            author = f" - {item['author']}" if item.get("author") else ""
            rows.append(("", f"  {commit} {item.get('message') or ''}{author}"))
        if len(changes) > 5:
            rows.append(("", f"  … 其余 {len(changes) - 5} 个提交"))
    print_rows(rows, out)

    width = max((disp_width(str(k)) for k, _ in rows), default=0)
    stages = payload.get("stages")
    if stages:
        line = "  ".join(
            f"{STAGE_ICONS.get(s['status'], '·')}{s['name']}" for s in stages
        )
        print(f"{pad('阶段', width)}  {line}", file=out)
        for stage in stages:
            if stage.get("error"):
                print(f"  ↳ {stage['name']}: {first_line(stage['error'], 300)}", file=out)
    if payload.get("artifacts"):
        listed = ", ".join(payload["artifacts"][:5])
        more = f" 等 {len(payload['artifacts'])} 个" if len(payload["artifacts"]) > 5 else ""
        print(f"{pad('产物', width)}  {listed}{more}", file=out)
    print(f"{pad('URL', width)}  {payload['url']}", file=out)


def extract_log_errors(text, max_blocks=5, before=3, after=8, max_line_len=300):
    lines = text.splitlines()
    blocks = []
    covered_until = -1
    for index, line in enumerate(lines):
        if index <= covered_until:
            continue
        if not ERROR_LINE_RE.search(line):
            continue
        start = max(0, index - before)
        end = min(len(lines), index + after + 1)
        snippet = [l[:max_line_len] for l in lines[start:end]]
        blocks.append({"line": start + 1, "text": snippet})
        covered_until = end
        if len(blocks) >= max_blocks:
            break
    return blocks


def tail_lines(text, limit, blocks):
    """返回日志尾部，跳过已经被错误摘录覆盖的行，避免重复。"""
    if not limit:
        return []
    lines = text.splitlines()
    covered = set()
    for block in blocks:
        covered.update(range(block["line"], block["line"] + len(block["text"])))
    remaining = [line for i, line in enumerate(lines, 1) if i not in covered]
    return [line[:300] for line in remaining[-limit:]]


def render_test_summary(tests, out=None, max_failures=10, error_lines=6):
    out = out or sys.stdout
    print(
        f"测试    总计 {tests['total']}  通过 {tests['pass']}  "
        f"失败 {tests['fail']}  跳过 {tests['skip']}  "
        f"耗时 {fmt_duration(tests.get('duration_ms'))}",
        file=out,
    )
    if not tests["failed"]:
        return
    print(f"失败用例（{len(tests['failed'])} 个，最多显示 {max_failures} 个）:", file=out)
    for index, case in enumerate(tests["failed"][:max_failures], start=1):
        full_name = ".".join(
            part for part in (case.get("class"), case.get("name")) if part
        )
        duration = (
            f" ({fmt_duration(case['duration_ms'])})" if case.get("duration_ms") else ""
        )
        print(f"  {index}. {colorize(full_name, '31')}{duration}", file=out)
        lines = [
            line
            for line in (case.get("error") or case.get("stack_trace") or "").splitlines()
            if line.strip()
        ]
        for line in lines[:error_lines]:
            print(f"     {line[:300]}", file=out)
        if len(lines) > error_lines:
            print(f"     … 共 {len(lines)} 行", file=out)


def failure_payload(client, job_url, job_name, number, args):
    payload = build_payload(
        client, job_url, job_name, number, with_stages=not args.no_stages
    )
    tests = fetch_test_report(client, job_url, number)
    log = fetch_console(client, job_url, number)
    blocks = extract_log_errors(log, args.max_log_blocks)
    tail = tail_lines(log, args.log_tail, blocks)
    failed_stages = [
        stage
        for stage in (payload.get("stages") or [])
        if stage.get("status") in ("FAILED", "UNSTABLE", "ABORTED")
    ]
    exit_codes = re.findall(r"script returned exit code (\d+)", log)
    result = {
        "build": payload,
        "failed_stages": failed_stages,
        "tests": tests,
        "log_errors": blocks,
        "log_tail": tail,
        "exit_codes": exit_codes,
    }
    return result


def render_failure(result, args, out=None):
    out = out or sys.stdout
    build = result["build"]
    if build["building"]:
        print("注意: 构建仍在运行，以下信息可能不完整", file=out)
    if result.get("exit_codes"):
        print(f"退出码: {', '.join(result['exit_codes'])}", file=out)

    if result["failed_stages"]:
        print("失败阶段:", file=out)
        for stage in result["failed_stages"]:
            print(
                f"  {STAGE_ICONS.get(stage['status'], '·')} {stage['name']} ({stage['status']})"
                + (f" — {first_line(stage.get('error'))}" if stage.get("error") else ""),
                file=out,
            )

    if result["tests"]:
        print(file=out)
        render_test_summary(
            result["tests"], out, args.max_failures, args.error_lines
        )
    else:
        print("测试    没有测试报告（未发布测试结果，或构建尚未结束）", file=out)

    print(file=out)
    if result["log_errors"]:
        print("日志错误摘录（行号对应完整日志）:", file=out)
        for block in result["log_errors"]:
            print(f"  ── L{block['line']} ──", file=out)
            for line in block["text"]:
                print(f"  {line}", file=out)
    else:
        print("日志中未匹配到明显错误关键字", file=out)
    if result["log_tail"]:
        print(file=out)
        print(f"日志尾部（最后 {len(result['log_tail'])} 行）:", file=out)
        for line in result["log_tail"]:
            print(f"  {line}", file=out)


# ────────────────────────────────────────────────────────────
# 子命令
# ────────────────────────────────────────────────────────────


def cmd_info(args, client):
    job_url, job, name = resolve_job(args, client)
    number = resolve_build_number(job, args.build)
    payload = build_payload(
        client, job_url, name, number, with_stages=not args.no_stages
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    render_build(payload)
    return 0


def cmd_history(args, client):
    job_url, _, name = resolve_job(args, client)
    builds = fetch_builds(client, job_url, None if args.all else args.limit)
    rows = []
    for index, build in enumerate(builds):
        icon, label = result_label(build.get("result"), build.get("building"))
        if build.get("building"):
            status = "运行中"
        elif index == 0:
            status = "最新"
        else:
            status = ""
        rows.append(
            {
                "number": build.get("number"),
                "result": f"{label} {icon}",
                "started_at": fmt_time(build.get("timestamp")),
                "duration": fmt_duration(build.get("duration")),
                "status": status,
            }
        )
    if args.json:
        print(
            json.dumps(
                {"job": name, "total": len(rows), "builds": rows},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not rows:
        print(f"{name}: 没有构建记录")
        return 0
    scope = "全部" if args.all else f"最近 {len(rows)} 次"
    print(f"Job: {name}（{scope}构建，共 {len(rows)} 条）\n")
    header = ("#", "结果", "开始时间", "耗时", "")
    widths = [6, 14, 26, 12, 8]
    print("  ".join(pad(str(h), w) for h, w in zip(header, widths)).rstrip())
    for row in rows:
        values = (
            f"#{row['number']}",
            row["result"],
            row["started_at"],
            row["duration"],
            row["status"],
        )
        print("  ".join(pad(str(v), w) for v, w in zip(values, widths)).rstrip())
    print(f"\nJob URL: {job_url}")
    print("查看某个构建: info --job <JOB_URL> --build <编号>")
    return 0


def mask_secret(value):
    if not value:
        return "未设置"
    if len(value) <= 2:
        return "***"
    return value[:2] + "***"


def prompt_value(
    prompt,
    default=None,
    secret=False,
    allow_empty=False,
    fallback=None,
    header=None,
    details=None,
):
    """先打印步骤标题和明细，再用短提示读输入，避开长行折行。"""
    lines = list(details or [])
    if default and not secret:
        lines.append(f"回车使用 {default}")
    if header:
        print(header)
    for line in lines:
        print(f"  {line}")
    while True:
        try:
            raw = (
                getpass.getpass(f"{prompt}> ")
                if secret
                else input(f"{prompt}> ")
            )
        except EOFError:
            raise JenkinsError(
                "config add 需要在交互式终端中执行，请让用户本人在自己的终端运行。"
            )
        value = raw.strip()
        if value:
            return value
        if fallback:
            return fallback
        if default is not None:
            return default
        if allow_empty:
            return ""
        print("不能为空，请重新输入")


def config_add(args):
    configs = load_server_configs()
    name = args.name
    total = 3 if name else 4
    step = 1
    print(f"配置 Jenkins server（共 {total} 步）")
    print(f"内容只保存在本机：{SERVERS_DIR}")
    print("请由用户本人在终端执行，不要把用户名、API Token 贴到对话里")
    if not name:
        name = prompt_value(
            "配置名称",
            default="default",
            header=f"\n步骤 {step}/{total}  配置名称（供 --server 使用）",
            details=["例如 ci"],
        )
        step += 1
    validate_server_name(name)
    existing = configs.get(name) or {}
    raw_server = prompt_value(
        "根地址",
        default=existing.get("server"),
        header=f"\n步骤 {step}/{total}  Jenkins 根地址",
        details=["不要填 job URL", "例如 https://ci.example.com"],
    )
    step += 1
    server = normalize_server_base(raw_server)
    user = prompt_value(
        "用户名",
        default=existing.get("user"),
        header=f"\n步骤 {step}/{total}  Jenkins 用户名",
        details=["用户 ID，通常等于登录名"],
    )
    step += 1
    token_details = [
        "需要新建 Token 时，先在浏览器打开个人设置页：",
        f"  {user_config_url(server, user)}",
        "登录后：右上角用户名 → Configure → API Token → Add new Token",
    ]
    if existing.get("token"):
        token_details.append("已有保存的 token 时，直接回车沿用")
    token = prompt_value(
        "API Token",
        secret=True,
        fallback=existing.get("token"),
        header=f"\n步骤 {step}/{total}  API Token（输入不回显）",
        details=token_details,
    )
    save_server_config(name, server, user, token)
    settings = load_settings()
    if not settings.get("default_server"):
        settings["default_server"] = name
        save_settings(settings)
    print(f"\n已保存 server 配置「{name}」→ {server_file(name)}（权限 600）")
    print(f"之后可直接用 job URL，脚本会按主机自动匹配；也可用 --server {name} 指定")
    return 0


def config_list(args):
    configs = load_server_configs()
    default = load_settings().get("default_server")
    if not configs:
        print("还没有配置任何 Jenkins server\n" + config_instruction())
        return 0
    if args.json:
        print(
            json.dumps(
                {
                    "default_server": default,
                    "servers": [
                        {
                            "name": name,
                            "server": config.get("server"),
                            "default": name == default,
                            "user": mask_secret(config.get("user")),
                            "token_configured": bool(config.get("token")),
                        }
                        for name, config in configs.items()
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    header = ("Name", "Server", "User", "Token")
    rows = []
    for name, config in configs.items():
        label = f"{name} *" if name == default else name
        rows.append(
            (
                label,
                config.get("server") or "-",
                mask_secret(config.get("user")),
                "已配置" if config.get("token") else "未配置",
            )
        )
    widths = [
        max(disp_width(header[i]), max(disp_width(str(r[i])) for r in rows))
        for i in range(len(header))
    ]
    print("  ".join(pad(h, w) for h, w in zip(header, widths)))
    for row in rows:
        print("  ".join(pad(str(v), w) for v, w in zip(row, widths)))
    print(f"\n* 为默认 server（仅用于非完整 URL 的 job 名称）；token 永远不会显示")
    print(f"配置目录: {SERVERS_DIR}")
    return 0


def config_show(args):
    configs = load_server_configs()
    default = load_settings().get("default_server")
    name = args.name or default
    if not name or name not in configs:
        raise JenkinsError(
            f"server 配置「{name or ''}」不存在，现有配置：{'、'.join(configs) or '无'}"
        )
    config = configs[name]
    if args.json:
        print(
            json.dumps(
                {
                    "name": name,
                    "default": name == default,
                    "server": config.get("server"),
                    "user": mask_secret(config.get("user")),
                    "token_configured": bool(config.get("token")),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    print(f"Name: {name}" + ("（默认）" if name == default else ""))
    print(f"Server: {config.get('server') or '-'}")
    print(f"User: {mask_secret(config.get('user'))}")
    print(f"Token: {'已配置（不显示）' if config.get('token') else '未配置'}")
    print(f"文件: {server_file(name)}")
    return 0


def cmd_config(args):
    action = args.action or "list"
    if action in ("set", "add"):
        return config_add(args)
    if action == "list":
        return config_list(args)
    if action == "show":
        return config_show(args)
    if action in ("use", "remove"):
        configs = load_server_configs()
        name = args.name
        if not name:
            raise JenkinsError(f"用法: {SCRIPT_HINT} config {action} <名称>")
        if name not in configs:
            raise JenkinsError(
                f"server 配置「{name}」不存在，现有配置：{'、'.join(configs) or '无'}"
            )
        if action == "use":
            settings = load_settings()
            settings["default_server"] = name
            save_settings(settings)
            print(f"已将默认 server 设为「{name}」（仅在 job 不是完整 URL 时使用）")
            return 0
        delete_server_config(name)
        settings = load_settings()
        if settings.get("default_server") == name:
            remaining = [n for n in configs if n != name]
            settings["default_server"] = remaining[0] if remaining else None
            save_settings(settings)
        print(f"已删除 server 配置「{name}」")
        return 0
    raise JenkinsError(f"未知的 config 子命令: {action}")


USER_PATH_RE = re.compile(r"/user/([^/]+)")


def _looks_like_address(raw):
    """区分地址与普通单词：地址带 scheme、路径、端口或点号。"""
    return (
        "://" in raw
        or "/" in raw
        or "." in raw
        or ":" in raw
        or raw.lower().startswith("localhost")
    )


def parse_token_target(raw):
    """把用户输入解析为 (Jenkins 根地址, 用户名或 None)。

    支持根地址、job URL、构建页 URL 和个人设置页 URL。context path 保留。
    """
    base = parse_server_base(raw)
    path = base["path"]
    user = None
    match = USER_PATH_RE.search(path)
    if match:
        user = urllib.parse.unquote(match.group(1))
        path = path[: match.start()]
    else:
        job_index = path.find("/job/")
        if job_index >= 0:
            path = path[:job_index]
    root = urllib.parse.urlunsplit(
        (base["scheme"], base["netloc"], path.rstrip("/"), "", "")
    )
    return root, user


def resolve_token_target(args):
    """确定个人设置页所需的根地址和用户名，返回 (根地址, 用户名, 配置名)。"""
    configs = load_server_configs()
    forced = (getattr(args, "server", None) or "").strip()
    raw = ((getattr(args, "target", None) or getattr(args, "target_job", None)) or "").strip()
    name, config, root, url_user = None, None, None, None

    if raw and raw in configs:
        name, config = raw, configs[raw]
        root = config["server"]
    elif raw:
        if not _looks_like_address(raw):
            raise JenkinsError(
                f"「{raw}」不是 server 配置名，也不像 Jenkins 地址。\n"
                "请提供 Jenkins 根地址或 job URL，或用 --server <名称> 指定配置。"
                + (f"\n现有配置：{'、'.join(configs)}" if configs else "")
            )
        root, url_user = parse_token_target(raw)
    elif forced:
        name, config = forced, select_server_config(forced)
        root = config["server"]
    elif not configs:
        raise JenkinsError(
            "还没有任何 Jenkins 配置，也没有提供 Jenkins 地址。\n"
            "请给出地址（用户名由用户在自己的终端补上）：\n"
            f"  {SCRIPT_HINT} token-url <Jenkins 根地址> --user <用户名>"
        )
    else:
        default = load_settings().get("default_server")
        if len(configs) == 1:
            name = next(iter(configs))
        elif default in configs:
            name = default
        else:
            listing = "\n".join(
                f"  - {n}: {c['server']}" for n, c in configs.items()
            )
            raise JenkinsError(
                "有多个 server 配置，请用 --server 指定其中之一：\n" + listing
            )
        config = configs[name]
        root = config["server"]

    if forced:
        config = select_server_config(forced)
        if root and not job_under_server(root, config["server"]):
            raise JenkinsError(
                f"地址与 --server「{forced}」的根地址不匹配，请去掉 --server 或修改地址"
            )
        name, root = forced, config["server"]
    elif config is None and configs:
        matched = match_server_config(root, configs)
        if matched:
            name, config = matched
        else:
            matched = match_server_config_by_host(root, configs)
            if matched:
                name, config = matched
                root = config["server"]

    user = (getattr(args, "user", None) or "").strip() or url_user or (
        (config or {}).get("user") or ""
    ).strip()
    if not user:
        raise JenkinsError(
            "无法确定用户名，不能生成个人设置页地址。\n"
            "请让用户在他/她自己的终端补上用户名：\n"
            f"  {SCRIPT_HINT} token-url {root} --user <用户名>"
        )
    return root, user, name


def cmd_token_url(args):
    root, user, name = resolve_token_target(args)
    url = user_config_url(root, user)
    if args.json:
        print(
            json.dumps(
                {"server": name, "server_base": root, "url": url},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    print("Jenkins 个人设置页（配置 API Token）")
    print(url)
    print()
    print(f"Server  {name or '-'}  {root}")
    print("下一步:")
    print("1. 浏览器打开上面的地址，先登录 Jenkins。")
    print("2. 在「API Token」区域点「Add new Token」，生成后立即复制。")
    print("3. 在自己的终端运行 config add 填入用户名和 Token（Token 输入不回显）:")
    print(f"   {SCRIPT_HINT} config add {name or '<配置名称，如 ci>'}")
    return 0


def collect_job_rows(children, depth, prefix="", parent_path=""):
    rows = []
    valid = [c for c in children if isinstance(c, dict)]
    for index, child in enumerate(valid):
        is_last = index == len(valid) - 1
        name = child.get("name") or child.get("displayName") or "?"
        label = child.get("displayName") or name
        path = f"{parent_path}/{name}" if parent_path else name
        rows.append(
            {
                "name": f"{prefix}{'└─ ' if is_last else '├─ '}{label}",
                "path": path,
                "job": child,
            }
        )
        nested = child.get("jobs")
        if depth > 1 and isinstance(nested, list) and nested:
            rows.extend(
                collect_job_rows(
                    nested,
                    depth - 1,
                    prefix + ("   " if is_last else "│  "),
                    path,
                )
            )
    return rows


def cmd_jobs(args, client):
    server_name = getattr(args, "_server_name", None)
    job_url = args._job_url
    if getattr(args, "child", None):
        job_url = resolve_child_url(client, job_url, args.child)
        args._job_url = job_url
    data = fetch_child_jobs(client, job_url, max(1, args.depth))
    kind = job_kind(data)
    kind_label = JOB_KIND_LABELS.get(kind, JOB_KIND_LABELS[JOB_KIND_CONTAINER])
    name = data.get("fullName") or data.get("name") or job_display(job_url)
    children = data.get("jobs")
    if not isinstance(children, list):
        if args.json:
            print(
                json.dumps(
                    {
                        "job": name,
                        "kind": kind,
                        "kind_label": leaf_job_label(data),
                        "server": server_name,
                        "children": None,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(f"{name} 没有子 job（{leaf_job_label(data)}，不是文件夹也不是多分支流水线）")
            print(f"查看最新构建: info --job '{job_url}'")
            print(f"列出全部构建: history --job '{job_url}' --all")
        return 0

    rows = collect_job_rows(children, max(1, args.depth))
    if args.json:
        print(
            json.dumps(
                {
                    "job": name,
                    "kind": kind,
                    "kind_label": kind_label,
                    "server": server_name,
                    "children": children,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"Job: {name}（{kind_label}，共 {len(children)} 个子 job，server: {server_name}）\n")
    if not rows:
        print("没有子 job")
        return 0
    header = ("子 job", "最新构建", "开始时间", "结果", "URL")
    widths = [
        max(disp_width(header[0]), max(disp_width(r["name"]) for r in rows)),
        12,
        26,
        10,
    ]
    print(
        "  ".join(
            [
                pad(header[0], widths[0]),
                pad(header[1], widths[1]),
                pad(header[2], widths[2]),
                pad(header[3], widths[3]),
                header[4],
            ]
        ).rstrip()
    )
    for row in rows:
        child = row["job"]
        last = child.get("lastBuild") or {}
        if last.get("number"):
            number = f"#{last['number']}"
            started = fmt_time(last.get("timestamp"))
            icon, label = result_label(last.get("result"), last.get("building"))
            result = f"{label} {icon}"
        else:
            number, started, result = "-", "-", "-"
        values = (row["name"], number, started, result)
        print(
            "  ".join(pad(str(v), w) for v, w in zip(values, widths)).rstrip()
            + "  "
            + (child.get("url") or "")
        )
    print(
        "\n选择子 job 后: info --job <URL>；也可用 --child <子 job 名称>；"
        "列出全部构建: history --job <URL> --all"
    )
    return 0


def cmd_saved(args):
    path = find_jobs_file(getattr(args, "jobs_file", None))
    urls = read_job_urls(path)
    if args.json:
        print(
            json.dumps(
                {"file": str(path), "jobs": urls},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not urls:
        print(f"{path} 中没有记录 job URL")
        print("首次查询后会自动记录；也可以手动把 job URL 每行一个写入该文件")
        return 0
    configs = load_server_configs()
    rows = []
    for index, url in enumerate(urls, 1):
        server = "-"
        if configs:
            try:
                matched = match_server_config(url, configs)
                if matched:
                    server = matched[0]
            except JenkinsError:
                server = "多个匹配"
        rows.append((str(index), job_short_name(url), url, server))
    print(f"Job 列表: {path}\n")
    header = ("#", "Job", "URL", "Server")
    widths = [
        max(disp_width(header[i]), max(disp_width(str(r[i])) for r in rows))
        for i in range(len(header))
    ]
    print("  ".join(pad(h, w) for h, w in zip(header, widths)))
    for row in rows:
        print("  ".join(pad(str(v), w) for v, w in zip(row, widths)))
    print("\n用法: info --job <序号|短名称|URL>；该文件是纯文本，可直接编辑")
    return 0


def cmd_log(args, client):
    job_url, job, name = resolve_job(args, client)
    number = resolve_build_number(job, args.build)
    print(f"Job URL: {job_url}/{number}/", file=sys.stderr)
    log = fetch_console(client, job_url, number)
    if not log:
        raise JenkinsError(f"构建 #{number} 没有可用日志")
    if args.save:
        path = Path(args.save).expanduser()
        path.write_text(log, "utf-8")
        print(f"完整日志已保存: {path}（{len(log.splitlines())} 行）", file=sys.stderr)
    lines = log.splitlines()
    if args.grep:
        try:
            regex = re.compile(args.grep, re.IGNORECASE if args.ignore_case else 0)
        except re.error as exc:
            raise JenkinsError(f"无效的正则表达式: {exc}")
        selected = set()
        for index, line in enumerate(lines):
            if regex.search(line):
                for offset in range(-args.context, args.context + 1):
                    if 0 <= index + offset < len(lines):
                        selected.add(index + offset)
        for index in sorted(selected):
            marker = ":" if regex.search(lines[index]) else "-"
            print(f"{index + 1:>6}{marker} {lines[index]}")
        if not selected:
            print(f"未匹配到: {args.grep}", file=sys.stderr)
        return 0
    if args.tail and args.tail > 0:
        lines = lines[-args.tail :]
    for index, line in enumerate(lines, start=len(log.splitlines()) - len(lines) + 1):
        print(f"{index:>6}  {line}")
    return 0


def cmd_tests(args, client):
    job_url, job, name = resolve_job(args, client)
    number = resolve_build_number(job, args.build)
    tests = fetch_test_report(client, job_url, number)
    if tests is None:
        message = f"构建 #{number} 没有测试报告（该 job 未发布测试结果，或构建尚未结束）"
        if args.json:
            print(json.dumps({"job": name, "number": number, "tests": None}, ensure_ascii=False))
        else:
            print(message)
        return 0
    if args.json:
        print(
            json.dumps(
                {"job": name, "number": number, "tests": tests},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    print(f"Job: {name}  #{number}")
    print(f"URL: {job_url}/{number}/")
    render_test_summary(tests, max_failures=args.max_failures, error_lines=args.error_lines)
    return 0


def cmd_failure(args, client):
    job_url, job, name = resolve_job(args, client)
    number = resolve_build_number(job, args.build)
    result = failure_payload(client, job_url, name, number, args)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    render_build(result["build"])
    print()
    render_failure(result, args)
    return 0


def parse_build_parameters(values):
    params = []
    for item in values or []:
        if "=" not in item:
            raise JenkinsError(f"参数格式应为 KEY=VALUE：{item}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise JenkinsError(f"参数名不能为空：{item}")
        params.append((key, value))
    return params


def wait_for_queue(client, queue_url, interval, timeout):
    """等待队列项变成可执行的构建。"""
    deadline = time.monotonic() + timeout if timeout and timeout > 0 else None
    last_why = None
    while True:
        data = client.get_json(
            f"{queue_url.rstrip('/')}/api/json?"
            + urllib.parse.urlencode(
                {"tree": "id,why,cancelled,executable[number,url]"}
            ),
            ok404=True,
        )
        if not isinstance(data, dict):
            raise JenkinsError(f"无法读取队列信息: {queue_url}")
        if data.get("cancelled"):
            raise JenkinsError(f"构建在队列中被取消: {queue_url}")
        executable = data.get("executable") or {}
        if executable.get("number"):
            return executable
        why = (data.get("why") or "").strip()
        if why and why != last_why:
            print(f"排队中: {why}", file=sys.stderr)
            last_why = why
        if deadline and time.monotonic() >= deadline:
            raise JenkinsError(
                f"等待构建开始超时（{fmt_duration(timeout * 1000)}），队列: {queue_url}",
                code=3,
            )
        time.sleep(interval)


def wait_for_build_finish(client, job_url, job_name, number, args):
    """等待构建结束，结束后输出报告。"""
    deadline = (
        time.monotonic() + args.watch_timeout if args.watch_timeout > 0 else None
    )
    started = time.monotonic()
    while True:
        build = client.get_json(
            f"{job_url}/{number}/api/json?"
            + urllib.parse.urlencode(
                {"tree": "number,building,result,timestamp,estimatedDuration"}
            ),
            ok404=True,
        )
        if build is None:
            if not args.quiet:
                print(f"等待 #{number} 创建...", file=sys.stderr)
        elif not build.get("building"):
            return finish_watch(args, client, job_url, job_name, number)
        elif not args.quiet:
            timestamp = build.get("timestamp")
            elapsed = (
                int(time.time() * 1000) - int(timestamp)
                if timestamp
                else int((time.monotonic() - started) * 1000)
            )
            estimated = build.get("estimatedDuration")
            suffix = f"（预估 {fmt_duration(estimated)}）" if estimated else ""
            print(
                f"[{fmt_clock(time.monotonic() - started)}] "
                f"#{number} 运行中 {fmt_duration(elapsed)}{suffix}",
                file=sys.stderr,
            )
        if deadline and time.monotonic() >= deadline:
            raise JenkinsError(
                f"等待构建结束超时（{fmt_duration(args.watch_timeout * 1000)}），"
                f"#{number} 仍在运行: {job_url}/{number}/",
                code=3,
            )
        time.sleep(args.interval)


def cmd_trigger(args, client):
    job_url, job, name = resolve_job(args, client)
    params = parse_build_parameters(args.param)
    endpoint = f"{job_url}/{'buildWithParameters' if params else 'build'}"

    if args.dry_run:
        print(f"[dry-run] POST {endpoint}")
        print(f"job: {name}")
        for key, value in params:
            print(f"参数: {key}={value}")
        print("未实际触发；去掉 --dry-run 才会提交构建")
        return 0

    status, headers = client.post(endpoint, data=params or None)
    location = headers.get("Location") or headers.get("location")
    if not location:
        print(f"已提交构建请求（HTTP {status}），但 Jenkins 未返回队列地址")
        print(f"请稍后用 info 查看最新构建: {job_url}")
        return 0

    queue_url = urllib.parse.urljoin(job_url, location)
    executable = wait_for_queue(client, queue_url, args.interval, args.queue_timeout)
    number = int(executable["number"])
    build_url = executable.get("url") or f"{job_url}/{number}/"

    if args.json and not args.wait:
        print(
            json.dumps(
                {
                    "job": name,
                    "job_url": job_url,
                    "queue": queue_url,
                    "build": {"number": number, "url": build_url},
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif not args.json:
        print(f"已触发: {name}")
        print(f"队列: {queue_url}")
        print(f"构建: #{number}  {build_url}")

    if args.wait:
        return wait_for_build_finish(client, job_url, name, number, args)
    return 0


def stop_build_request(client, job_url, number):
    """发送停止请求；个别旧版本只支持 /kill，作为 404 时的兜底。"""
    try:
        return client.post(f"{job_url}/{number}/stop")
    except JenkinsError as exc:
        if "HTTP 404" not in str(exc):
            raise
        return client.post(f"{job_url}/{number}/kill")


def cmd_stop(args, client):
    job_url, job, name = resolve_job(args, client)
    number = resolve_build_number(job, args.build)
    build = fetch_build(client, job_url, number)
    if build is None:
        raise JenkinsError(
            f"构建 #{number} 不存在或尚未开始（job: {job_display(job_url)}）；"
            "仍在队列中的构建无法用 stop 停止"
        )

    build_url = f"{job_url}/{number}/"
    if not build.get("building"):
        icon, label = result_label(build.get("result"))
        if args.json:
            print(
                json.dumps(
                    {
                        "job": name,
                        "number": number,
                        "url": build_url,
                        "stopped": False,
                        "result": build.get("result"),
                        "reason": "already finished",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(f"构建 #{number} 已结束（{label} {icon}），无需停止")
            print(f"URL: {build_url}")
        return 0

    if args.dry_run:
        print(f"[dry-run] POST {build_url}stop")
        print(f"job: {name}  #{number}")
        print("未实际停止；去掉 --dry-run 才会提交停止请求")
        return 0

    status, _ = stop_build_request(client, job_url, number)
    if args.json and not args.wait:
        print(
            json.dumps(
                {
                    "job": name,
                    "number": number,
                    "url": build_url,
                    "stopped": True,
                    "http_status": status,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif not args.json:
        print(f"已发送停止请求: {name} #{number}")
        print(f"构建: {build_url}")

    if args.wait:
        return wait_for_build_finish(client, job_url, name, number, args)
    return 0


class WatchProgress:
    """把 watch 的进度行写到 stderr，stdout 留给最终报告。"""

    def __init__(self, args, started):
        self.args = args
        self.started = started
        self.signature = None
        self.last_shown = 0.0

    def show(self, signature, text):
        now = time.monotonic()
        if (
            self.args.quiet
            and signature == self.signature
            and now - self.last_shown < 60
        ):
            return
        self.signature = signature
        self.last_shown = now
        print(f"[{fmt_clock(now - self.started)}] {text}", file=sys.stderr, flush=True)


def finish_watch(args, client, job_url, job_name, number):
    payload = build_payload(
        client, job_url, job_name, number, with_stages=not args.no_stages
    )
    result = payload["result"]
    report = None
    if (result and result != "SUCCESS") and not args.no_report:
        log = fetch_console(client, job_url, number)
        tests = fetch_test_report(client, job_url, number)
        blocks = extract_log_errors(log, args.max_log_blocks)
        report = {
            "build": payload,
            "failed_stages": [
                stage
                for stage in (payload.get("stages") or [])
                if stage.get("status") in ("FAILED", "UNSTABLE", "ABORTED")
            ],
            "tests": tests,
            "log_errors": blocks,
            "log_tail": tail_lines(log, args.log_tail, blocks),
            "exit_codes": re.findall(r"script returned exit code (\d+)", log),
        }
    if args.json:
        print(
            json.dumps(
                {"build": payload, "failure_report": report},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        icon, label = result_label(result, payload["building"])
        print(f"\n构建结束: #{number} {label} {icon}\n")
        render_build(payload)
        if report:
            print()
            render_failure(report, args)
    return 0 if result == "SUCCESS" else 1


def cmd_watch(args, client):
    job_url, job, name = resolve_job(args, client)
    number = None
    baseline = None
    if args.build is not None:
        number = resolve_build_number(job, args.build)
    elif args.next:
        baseline = (job.get("lastBuild") or {}).get("number") or 0
    else:
        last = job.get("lastBuild") or {}
        if last.get("number") and not last.get("building") and not job.get("inQueue"):
            # 最新构建已经结束，直接出报告
            return finish_watch(args, client, job_url, name, int(last["number"]))
        if job.get("inQueue") and not last.get("building"):
            # 有构建正在排队，等它真正开始
            baseline = last.get("number") or 0
        if not last.get("number") and not job.get("inQueue"):
            raise JenkinsError(
                f"{name} 还没有任何构建。触发构建后，用 watch --next 等待新构建"
            )

    started = time.monotonic()
    deadline = started + args.timeout if args.timeout and args.timeout > 0 else None
    progress = WatchProgress(args, started)
    stages_supported = not args.no_stages

    while True:
        if number is None:
            job = fetch_job(client, job_url, tree=WATCH_TREE)
            last = job.get("lastBuild") or {}
            latest = last.get("number") or 0
            waiting_next = baseline is not None and latest <= baseline
            if waiting_next or (not latest and baseline is None):
                queue = job.get("queueItem") or {}
                reason = queue.get("why") or ("等待可用执行器" if job.get("inQueue") else "等待构建开始")
                progress.show(
                    ("queued", latest),
                    f"排队中 — {reason}（job 最新构建: #{latest or '-'}）",
                )
            else:
                number = int(latest)
        else:
            build = client.get_json(
                f"{job_url}/{number}/api/json?"
                + urllib.parse.urlencode({"tree": RUNNING_BUILD_TREE}),
                ok404=True,
            )
            if build is None:
                progress.show(("pending", number), f"等待 #{number} 创建（队列中）")
            elif build.get("building"):
                stages = []
                if stages_supported:
                    stages = fetch_stages(client, job_url, number)
                    if stages is None:
                        stages_supported = False
                        stages = []
                elapsed = (
                    int(time.time() * 1000) - int(build.get("timestamp") or 0)
                    if build.get("timestamp")
                    else 0
                )
                parts = [f"#{number} 运行中 {fmt_duration(elapsed)}"]
                if build.get("estimatedDuration"):
                    parts.append(f"≈{fmt_duration(build['estimatedDuration'])}")
                running_stage = next(
                    (s for s in stages if s.get("status") == "IN_PROGRESS"), None
                )
                if running_stage:
                    parts.append(f"阶段: {running_stage['name']}")
                if stages:
                    parts.append(
                        "("
                        + " ".join(
                            f"{STAGE_ICONS.get(s['status'], '·')}{s['name']}"
                            for s in stages
                        )
                        + ")"
                    )
                signature = (
                    number,
                    running_stage["name"] if running_stage else None,
                    tuple((s["name"], s["status"]) for s in stages),
                )
                progress.show(signature, " ".join(parts))
            else:
                return finish_watch(args, client, job_url, name, number)

        if deadline and time.monotonic() >= deadline:
            current = f"#{number}" if number else "队列中的构建"
            raise JenkinsError(
                f"等待超时（{fmt_duration(args.timeout * 1000)}），{current} 仍未结束。"
                f"可继续通过 watch --build {number} 跟踪，构建在 Jenkins 上会继续执行",
                code=3,
            )
        time.sleep(args.interval)


# ────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────


def add_common_arguments(parser):
    parser.add_argument(
        "--job",
        help="job 的完整 URL（推荐），或配合 --server 使用 job 名称；脚本按主机自动匹配 server 配置",
    )
    parser.add_argument(
        "--server",
        help="server 配置名称（同主机多配置或 job 只给名称时使用）",
    )
    parser.add_argument(
        "--child", help='子 job 名称（多分支/文件夹 job 下的分支），如 "main"、"feature/login"'
    )
    parser.add_argument(
        "--jobs-file",
        help="项目 job 列表文件路径（默认从当前目录向上查找 .jenkins-jobs）",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="不把本次 job URL 记录到 .jenkins-jobs",
    )
    parser.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验")
    parser.add_argument(
        "--http-timeout", type=float, default=30.0, help="单次 HTTP 超时秒数（默认 30）"
    )
    parser.add_argument("--no-color", action="store_true", help="禁用颜色输出")
    parser.add_argument("--json", action="store_true", help="输出 JSON 结构")
    parser.add_argument("--verbose", action="store_true", help="打印 HTTP 请求明细")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="jk.py",
        description="jk-track: 查询和跟踪 Jenkins 构建情况",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--version", action="version", version=f"jk-track {VERSION}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    def new_parser(name, help_text, with_build=True):
        sub = subparsers.add_parser(name, help=help_text, description=help_text)
        add_common_arguments(sub)
        if with_build:
            sub.add_argument(
                "--build",
                default=None,
                help="构建编号（如 128 / #128），或 latest、lastCompleted、lastFailed、lastSuccessful",
            )
        return sub

    p_info = new_parser("info", "查看构建概况（默认最新构建）")
    p_info.add_argument("--no-stages", action="store_true", help="不查询 Pipeline 阶段")
    p_info.set_defaults(func=cmd_info, build="latest", no_stages=False)

    p_history = new_parser("history", "查看构建历史", with_build=False)
    p_history.add_argument("--limit", type=int, default=10, help="显示条数（默认 10）")
    p_history.add_argument("--all", action="store_true", help="列出全部构建（不限条数）")
    p_history.set_defaults(func=cmd_history)

    p_jobs = subparsers.add_parser(
        "jobs",
        help="列出 job 类型和子 job（多分支流水线/组织文件夹/文件夹）的最新构建",
        description="列出 job 类型和子 job（多分支流水线/组织文件夹/文件夹）的最新构建",
    )
    add_common_arguments(p_jobs)
    p_jobs.add_argument("--depth", type=int, default=1, help="子 job 递归层数（默认 1）")
    p_jobs.set_defaults(func=cmd_jobs)

    p_config = subparsers.add_parser(
        "config",
        help="管理 Jenkins server 配置（由用户本人在本机终端交互执行）",
        description="管理 server 配置：add / list / show / use / remove",
    )
    p_config.add_argument(
        "action",
        nargs="?",
        default="list",
        choices=["add", "set", "list", "show", "use", "remove"],
        help="操作类型（默认 list）",
    )
    p_config.add_argument("name", nargs="?", help="server 配置名称（add / show / use / remove）")
    p_config.add_argument("--json", action="store_true", help="输出 JSON 结构")
    p_config.set_defaults(func=cmd_config)

    p_token = subparsers.add_parser(
        "token-url",
        aliases=["token"],
        help="生成 Jenkins 个人设置页地址（用于配置 API Token）",
        description="生成个人设置页地址：<根地址>/user/<用户名>/configure",
    )
    p_token.add_argument(
        "target",
        nargs="?",
        help="Jenkins 地址、job URL 或 server 配置名（默认使用默认 server）",
    )
    p_token.add_argument("--job", dest="target_job", help="同 target，可传 job URL")
    p_token.add_argument("--server", help="server 配置名称")
    p_token.add_argument(
        "--user", help="用户名（默认取匹配配置中的用户名或输入 URL 中的 /user/<用户名>）"
    )
    p_token.add_argument("--json", action="store_true", help="输出 JSON 结构")
    p_token.set_defaults(func=cmd_token_url)

    p_saved = subparsers.add_parser(
        "saved",
        aliases=["urls"],
        help="列出项目 .jenkins-jobs 中记录的 job URL",
        description="列出项目 .jenkins-jobs 中记录的 job URL",
    )
    p_saved.add_argument(
        "--jobs-file",
        help="项目 job 列表文件路径（默认从当前目录向上查找 .jenkins-jobs）",
    )
    p_saved.add_argument("--json", action="store_true", help="输出 JSON 结构")
    p_saved.set_defaults(func=cmd_saved)

    p_log = new_parser("log", "查看构建日志")
    p_log.add_argument("--tail", type=int, default=80, help="只显示最后 N 行，0 表示全部（默认 80）")
    p_log.add_argument("--grep", help="按正则过滤日志行（附带上下文行号）")
    p_log.add_argument("-A", "--context", type=int, default=2, help="grep 时上下文行数（默认 2）")
    p_log.add_argument("-i", "--ignore-case", action="store_true", help="grep 忽略大小写")
    p_log.add_argument("--save", help="把完整日志保存到指定文件")
    p_log.set_defaults(func=cmd_log, build="latest")

    p_tests = new_parser("tests", "查看测试报告")
    p_tests.add_argument("--max-failures", type=int, default=20, help="最多显示多少个失败用例（默认 20）")
    p_tests.add_argument("--error-lines", type=int, default=8, help="每个失败用例显示的错误行数（默认 8）")
    p_tests.set_defaults(func=cmd_tests, build="latest")

    p_failure = new_parser("failure", "汇总失败原因（失败阶段、测试报告、日志错误）")
    p_failure.add_argument("--max-log-blocks", type=int, default=5, help="日志错误摘录块数（默认 5）")
    p_failure.add_argument("--log-tail", type=int, default=30, help="日志尾部行数（默认 30，0 表示不显示）")
    p_failure.add_argument("--max-failures", type=int, default=10, help="最多显示多少个失败用例（默认 10）")
    p_failure.add_argument("--error-lines", type=int, default=6, help="每个失败用例显示的错误行数（默认 6）")
    p_failure.add_argument("--no-stages", action="store_true", help="不查询 Pipeline 阶段")
    p_failure.set_defaults(func=cmd_failure, build="latest")

    p_stop = new_parser("stop", "停止构建（会提交停止请求）")
    p_stop.add_argument(
        "--wait", action="store_true", help="等待构建真正结束后输出报告"
    )
    p_stop.add_argument(
        "--interval", type=float, default=5.0, help="轮询间隔秒数（默认 5）"
    )
    p_stop.add_argument(
        "--watch-timeout",
        type=float,
        default=DEFAULT_WATCH_TIMEOUT,
        help="--wait 时等待构建结束的最长秒数，0 表示不限制（默认 3600）",
    )
    p_stop.add_argument(
        "--dry-run", action="store_true", help="只显示将要提交的请求，不实际停止"
    )
    p_stop.add_argument("--quiet", action="store_true", help="不打印轮询进度")
    p_stop.add_argument(
        "--no-report", action="store_true", help="--wait 结束后不输出失败报告"
    )
    p_stop.add_argument(
        "--no-stages", action="store_true", help="不查询 Pipeline 阶段"
    )
    p_stop.add_argument(
        "--max-log-blocks", type=int, default=5, help="失败报告中日志错误块数"
    )
    p_stop.add_argument(
        "--log-tail", type=int, default=30, help="失败报告中日志尾部行数"
    )
    p_stop.add_argument(
        "--max-failures", type=int, default=10, help="失败报告中失败用例上限"
    )
    p_stop.add_argument(
        "--error-lines", type=int, default=6, help="每个失败用例显示的错误行数"
    )
    p_stop.set_defaults(func=cmd_stop, build="latest")

    p_trigger = new_parser("trigger", "触发 Jenkins 构建（会提交构建请求）", with_build=False)
    p_trigger.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="构建参数，可重复；有参数时使用 buildWithParameters",
    )
    p_trigger.add_argument(
        "--wait", action="store_true", help="等待构建结束后输出报告"
    )
    p_trigger.add_argument(
        "--interval", type=float, default=5.0, help="队列/构建轮询间隔秒数（默认 5）"
    )
    p_trigger.add_argument(
        "--queue-timeout",
        type=float,
        default=300.0,
        help="等待构建开始的最长秒数，0 表示不限制（默认 300）",
    )
    p_trigger.add_argument(
        "--watch-timeout",
        type=float,
        default=DEFAULT_WATCH_TIMEOUT,
        help="--wait 时等待构建结束的最长秒数，0 表示不限制（默认 3600）",
    )
    p_trigger.add_argument(
        "--dry-run", action="store_true", help="只显示将要提交的请求，不实际触发"
    )
    p_trigger.add_argument("--quiet", action="store_true", help="不打印轮询进度")
    p_trigger.add_argument(
        "--no-report", action="store_true", help="--wait 结束后不输出失败报告"
    )
    p_trigger.add_argument(
        "--no-stages", action="store_true", help="不查询 Pipeline 阶段"
    )
    p_trigger.add_argument(
        "--max-log-blocks", type=int, default=5, help="失败报告中日志错误块数"
    )
    p_trigger.add_argument(
        "--log-tail", type=int, default=30, help="失败报告中日志尾部行数"
    )
    p_trigger.add_argument(
        "--max-failures", type=int, default=10, help="失败报告中失败用例上限"
    )
    p_trigger.add_argument(
        "--error-lines", type=int, default=6, help="每个失败用例显示的错误行数"
    )
    p_trigger.set_defaults(func=cmd_trigger)

    p_watch = new_parser("watch", "持续跟踪构建直到结束，并输出报告")
    p_watch.add_argument(
        "--next", action="store_true", help="等待下一个新构建（用于刚触发构建后等待）"
    )
    p_watch.add_argument(
        "--interval", type=float, default=DEFAULT_INTERVAL, help="轮询间隔秒数（默认 15）"
    )
    p_watch.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_WATCH_TIMEOUT,
        help="最长等待秒数，0 表示不限制（默认 3600）",
    )
    p_watch.add_argument("--no-report", action="store_true", help="结束后不输出失败报告")
    p_watch.add_argument("--no-stages", action="store_true", help="不查询 Pipeline 阶段")
    p_watch.add_argument("--quiet", action="store_true", help="只在状态变化时打印进度")
    p_watch.add_argument("--max-log-blocks", type=int, default=5, help="失败报告中日志错误块数")
    p_watch.add_argument("--log-tail", type=int, default=30, help="失败报告中日志尾部行数")
    p_watch.add_argument("--max-failures", type=int, default=10, help="失败报告中失败用例上限")
    p_watch.add_argument("--error-lines", type=int, default=6, help="每个失败用例显示的错误行数")
    p_watch.set_defaults(func=cmd_watch, build=None)

    return parser


def main(argv=None):
    global COLOR_ENABLED
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    COLOR_ENABLED = not getattr(args, "no_color", False) and os.environ.get("NO_COLOR") is None
    interval = getattr(args, "interval", DEFAULT_INTERVAL)
    if interval < 0.2:
        args.interval = 0.2
    try:
        if args.command in ("config", "saved", "urls", "token-url", "token"):
            return args.func(args)
        job_url, server_name, server_config = resolve_job_target(args)
        args._job_url = job_url
        args._server_name = server_name
        user, token = resolve_auth(server_name, server_config)
        client = JenkinsClient(
            user,
            token,
            insecure=args.insecure,
            http_timeout=args.http_timeout,
            verbose=args.verbose,
            server_name=server_name,
            server_base=server_config["server"],
        )
        code = args.func(args, client)
        if code in (0, 1, 3) and not getattr(args, "no_save", False) and not getattr(
            args, "dry_run", False
        ):
            record_job_url(find_jobs_file(args.jobs_file), args._job_url)
        return code
    except JenkinsError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        print("\n已中断（构建在 Jenkins 上会继续执行）", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
