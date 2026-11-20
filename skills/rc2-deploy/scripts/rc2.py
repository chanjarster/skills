#!/usr/bin/env python3
"""rc2-deploy: Rancher 2.x 部署助手。

子命令:
    clusters   列出集群
    projects   列出项目
    namespaces 列出项目下的命名空间
    ls         列出资源（deployment/statefulset/daemonset/job/configmap/secret/ingress/service/pvc）
    get        查询一个资源（--json / --yaml）
    apply      用 YAML 清单创建或更新资源
    delete     删除一个资源
    redeploy   重新部署一个工作负载或 Job
    saved      列出项目目录 .rancher-projects 中记录的项目连接（别名 remembered）
    config     管理 Rancher 连接配置（add/list/show/use/remove）

连接配置:
    每个 Rancher 一个配置文件：~/.rc2-deploy/servers/<名称>.json。
    文件只保存 API 根地址、Access Key 和 Secret Key。
    由用户在自己的终端执行 `config add` 交互写入。
    不要在对话中索取 Access Key 或 Secret Key。

项目记忆:
    项目地址保存在项目目录的 .rancher-projects 中（每行一个）。
    命令成功后脚本自动记录。可用序号、项目 ID 或短 ID 选择项目。

用法示例:
    python rc2.py config add prod              # 由用户本人在终端执行
    python rc2.py clusters
    python rc2.py projects --cluster c-abc123
    python rc2.py namespaces --project https://rancher.example.com/v3/projects/c-abc123:p-def456
    python rc2.py ls deployments --namespace prod
    python rc2.py get deployment/api-server --namespace prod --yaml
    python rc2.py apply --file deploy.yaml
    python rc2.py redeploy deployment/api-server --namespace prod
    python rc2.py delete deployment/api-server --namespace prod

退出码:
    0    成功
    2    参数、配置、认证或网络错误
    130  用户中断
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
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.0"
CONFIG_DIR = Path.home() / ".rc2-deploy"
SERVERS_DIR = CONFIG_DIR / "servers"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
PROJECTS_FILE_NAME = ".rancher-projects"
DEFAULT_HTTP_TIMEOUT = 30.0
SUMMARY_LABEL_WIDTH = 10

SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
PROJECT_ID_RE = re.compile(r"^c-[A-Za-z0-9]+:p-[A-Za-z0-9]+$")

KINDS = {
    "deployment": {
        "label": "Deployment",
        "collections": ["deployments", "workloads"],
        "workload": True,
    },
    "statefulset": {
        "label": "StatefulSet",
        "collections": ["statefulsets", "statefulSets", "workloads"],
        "workload": True,
    },
    "daemonset": {
        "label": "DaemonSet",
        "collections": ["daemonsets", "daemonSets", "workloads"],
        "workload": True,
    },
    "job": {"label": "Job", "collections": ["jobs"], "workload": False},
    "configmap": {
        "label": "ConfigMap",
        "collections": ["configmaps", "configMaps"],
        "workload": False,
    },
    "secret": {"label": "Secret", "collections": ["secrets"], "workload": False},
    "ingress": {"label": "Ingress", "collections": ["ingresses"], "workload": False},
    "service": {"label": "Service", "collections": ["services"], "workload": False},
    "pvc": {
        "label": "PersistentVolumeClaim",
        "collections": ["persistentvolumeclaims", "persistentVolumeClaims"],
        "workload": False,
    },
}

KIND_ALIASES = {
    "deployment": "deployment",
    "deployments": "deployment",
    "deploy": "deployment",
    "dep": "deployment",
    "workload": "deployment",
    "workloads": "deployment",
    "statefulset": "statefulset",
    "statefulsets": "statefulset",
    "sts": "statefulset",
    "daemonset": "daemonset",
    "daemonsets": "daemonset",
    "ds": "daemonset",
    "job": "job",
    "jobs": "job",
    "configmap": "configmap",
    "configmaps": "configmap",
    "cm": "configmap",
    "secret": "secret",
    "secrets": "secret",
    "ingress": "ingress",
    "ingresses": "ingress",
    "ing": "ingress",
    "service": "service",
    "services": "service",
    "svc": "service",
    "pvc": "pvc",
    "pvcs": "pvc",
    "persistentvolumeclaim": "pvc",
    "persistentvolumeclaims": "pvc",
}

COLUMNS = {
    "deployment": ["NAME", "NAMESPACE", "READY", "IMAGES", "STATE", "AGE"],
    "statefulset": ["NAME", "NAMESPACE", "READY", "IMAGES", "STATE", "AGE"],
    "daemonset": ["NAME", "NAMESPACE", "READY", "IMAGES", "STATE", "AGE"],
    "job": ["NAME", "NAMESPACE", "COMPLETIONS", "SUCCEEDED", "STATE", "AGE"],
    "configmap": ["NAME", "NAMESPACE", "DATA", "AGE"],
    "secret": ["NAME", "NAMESPACE", "TYPE", "DATA", "AGE"],
    "ingress": ["NAME", "NAMESPACE", "HOSTS", "AGE"],
    "service": ["NAME", "NAMESPACE", "CLUSTER IP", "PORTS", "AGE"],
    "pvc": ["NAME", "NAMESPACE", "STATUS", "CAPACITY", "ACCESS MODES", "AGE"],
}

# 集群级 token 的后端：Rancher 的 Kubernetes API 代理路径。
K8S_KINDS = {
    "deployment": ("apps/v1", "deployments"),
    "statefulset": ("apps/v1", "statefulsets"),
    "daemonset": ("apps/v1", "daemonsets"),
    "job": ("batch/v1", "jobs"),
    "configmap": ("v1", "configmaps"),
    "secret": ("v1", "secrets"),
    "service": ("v1", "services"),
    "pvc": ("v1", "persistentvolumeclaims"),
    "ingress": ("networking.k8s.io/v1", "ingresses"),
}

MANIFEST_KINDS = {
    "deployment": "deployment",
    "deployments": "deployment",
    "statefulset": "statefulset",
    "statefulsets": "statefulset",
    "daemonset": "daemonset",
    "daemonsets": "daemonset",
    "job": "job",
    "jobs": "job",
    "configmap": "configmap",
    "configmaps": "configmap",
    "secret": "secret",
    "secrets": "secret",
    "ingress": "ingress",
    "ingresses": "ingress",
    "service": "service",
    "services": "service",
    "persistentvolumeclaim": "pvc",
    "persistentvolumeclaims": "pvc",
}

SCOPE_RE = re.compile(r"^cluster:(c-[A-Za-z0-9]+)$")


# ────────────────────────────────────────────────────────────
# 异常与通用工具
# ────────────────────────────────────────────────────────────


class RancherError(Exception):
    """可预期的错误。code 是进程退出码，status 是 HTTP 状态码。"""

    def __init__(self, message, code=2, status=None):
        super().__init__(message)
        self.code = code
        self.status = status


def disp_width(text):
    return sum(
        2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in text
    )


def pad(text, width):
    text = str(text)
    return text + " " * max(0, width - disp_width(text))


def truncate(text, limit):
    text = str(text)
    if disp_width(text) <= limit:
        return text
    result = ""
    for char in text:
        if disp_width(result + char) > limit - 1:
            break
        result += char
    return result + "…"


def print_table(headers, rows):
    if not rows:
        print("  ".join(headers))
        return
    widths = []
    for index, header in enumerate(headers):
        cells = [disp_width(str(row[index])) for row in rows]
        widths.append(max([disp_width(header)] + cells))
    print("  ".join(pad(header, width) for header, width in zip(headers, widths)).rstrip())
    for row in rows:
        print(
            "  ".join(pad(cell, width) for cell, width in zip(row, widths)).rstrip()
        )


def mask_secret(value, keep=4):
    if not value:
        return "-"
    value = str(value)
    if len(value) <= keep + 2:
        return "*" * len(value)
    return value[:keep] + "*" * 6 + value[-2:]


def add_query(url, **params):
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query.extend((key, str(value)) for key, value in params.items() if value is not None)
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), "")
    )


def dedupe(values):
    result = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def api_message(body):
    try:
        payload = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        return (body or "").strip()[:200]
    if isinstance(payload, dict):
        for key in ("message", "status", "code"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def http_error_message(method, url, status, body):
    detail = api_message(body)
    if status == 401:
        return (
            "认证失败（HTTP 401）：Access Key 或 Secret Key 无效。\n"
            "请让用户运行 config add 更新凭据。"
        )
    if status == 403:
        text = f"权限不足（HTTP 403）：API Key 不能执行 {method} {url}。"
        return text + (f"\nRancher 消息：{detail}" if detail else "")
    if status == 404:
        return f"资源不存在（HTTP 404）：{method} {url}"
    if status in (400, 422):
        return f"请求被拒绝（HTTP {status}）：{detail or url}"
    if status >= 500:
        return f"Rancher 服务端错误（HTTP {status}）：{detail or url}"
    return f"HTTP {status}：{detail or url}（{method} {url}）"


def normalize_server_url(raw):
    url = (raw or "").strip().rstrip("/")
    if not re.match(r"^https?://[^/]+", url):
        raise RancherError("Rancher 地址必须以 http:// 或 https:// 开头")
    for marker in ("/projects/", "/project/", "/clusters/", "/cluster/"):
        if marker in url:
            raise RancherError(
                "这里要填 Rancher API 根地址（以 /v3 结尾），不是项目或资源的地址"
            )
    if url.endswith("/apikeys"):
        url = url[: -len("/apikeys")]
    if not re.search(r"/v\d+$", url):
        url += "/v3"
    return url


def normalize_project_url(raw):
    parts = urllib.parse.urlsplit((raw or "").strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise RancherError("项目地址必须以 http:// 或 https:// 开头")
    _, project = extract_references(parts.path)
    if not project:
        raise RancherError(
            "项目地址要包含 /projects/<项目ID> 或 /p/<项目ID>，"
            "例如 https://rancher.example.com/p/c-abc123:p-def456/"
        )
    return f"{parts.scheme}://{parts.netloc}/v3/projects/{project}"


def project_id_of(url):
    return url.rstrip("/").rsplit("/", 1)[-1]


def extract_references(raw):
    """从文本或 URL 中提取 (集群 ID, 项目 ID)。找不到时为 None。

    支持：cluster:c-xxxxx、c-xxxxx、c-xxxxx:p-yyyy、
    集群页面 URL、项目页面 URL、/v3 API 地址、/k8s 代理地址。
    """
    text = (raw or "").strip()
    if not text:
        return None, None
    match = re.fullmatch(r"cluster:(c-[A-Za-z0-9]+)", text)
    if match:
        return match.group(1), None
    match = re.fullmatch(r"(c-[A-Za-z0-9]+)", text)
    if match:
        return match.group(1), None
    match = re.fullmatch(r"(c-[A-Za-z0-9]+):(p-[A-Za-z0-9]+)", text)
    if match:
        return match.group(1), f"{match.group(1)}:{match.group(2)}"
    path = text
    if "://" in text:
        path = urllib.parse.urlsplit(text).path
    if "/" in path:
        match = re.search(
            r"/(?:projects|p)/(c-[A-Za-z0-9]+):(p-[A-Za-z0-9]+)", path
        )
        if match:
            return match.group(1), f"{match.group(1)}:{match.group(2)}"
        match = re.search(r"/(?:k8s/)?clusters?/(c-[A-Za-z0-9]+)", path)
        if match:
            return match.group(1), None
        match = re.search(r"/(?:dashboard/)?c/(c-[A-Za-z0-9]+)", path)
        if match:
            return match.group(1), None
        match = re.search(r"(c-[A-Za-z0-9]+):(p-[A-Za-z0-9]+)", path)
        if match:
            return match.group(1), f"{match.group(1)}:{match.group(2)}"
    return None, None


def normalize_scope(raw):
    """把 scope 输入归一化为 cluster:c-xxxxx；空输入返回 None。"""
    text = (raw or "").strip()
    if not text:
        return None
    cluster, _ = extract_references(text)
    if not cluster:
        raise RancherError(
            "无法识别 scope。可以填：cluster:c-xxxxx、c-xxxxx，"
            "或直接粘贴集群/项目页面 URL。"
        )
    return f"cluster:{cluster}"


def scope_cluster(config):
    """返回配置的 scope 集群 ID；无 scope 时返回 None。"""
    scope = (config.get("scope") or "").strip()
    match = SCOPE_RE.match(scope)
    return match.group(1) if match else None


def server_root(url):
    return re.sub(r"/v\d+$", "", (url or "").rstrip("/"))


def apikeys_url(server_url):
    """由 Rancher 地址推导 API Key 创建页面地址。"""
    root = server_root(server_url)
    root = re.sub(r"/k8s/clusters/[^/]+$", "", root)
    return f"{root}/apikeys"


def parse_ref(raw, default_namespace=None):
    """把 deployment/api 或 deployment:prod:api 解析为 (kind, name, namespace)。"""
    text = (raw or "").strip()
    if not text:
        raise RancherError("缺少资源。写法：<类型>/<名称>，例如 deployment/api-server")
    if "/" in text:
        kind_raw, name = text.split("/", 1)
        namespace = default_namespace
    elif text.count(":") >= 2:
        kind_raw, namespace, name = text.split(":", 2)
    else:
        raise RancherError(
            "资源写法：<类型>/<名称>，例如 deployment/api-server；也可写 deployment:prod:api-server"
        )
    kind = normalize_kind(kind_raw)
    name = name.strip()
    namespace = (namespace or default_namespace or "").strip() or None
    if not name:
        raise RancherError("资源名称不能为空")
    return kind, name, namespace


def normalize_kind(raw):
    key = (raw or "").strip().lower()
    if key in KIND_ALIASES:
        return KIND_ALIASES[key]
    raise RancherError(
        f"不支持的类型「{raw}」。可用类型：{', '.join(sorted(KINDS))}"
    )


def created_ts(item):
    value = item.get("createdTS")
    if isinstance(value, (int, float)):
        return float(value)
    created = item.get("created")
    if isinstance(created, str) and created:
        try:
            return datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp() * 1000
        except ValueError:
            return None
    return None


def fmt_age(ms):
    if not ms:
        return "-"
    delta = max(0.0, time.time() - ms / 1000.0)
    if delta < 90:
        return f"{int(delta)}s"
    if delta < 5400:
        return f"{int(delta / 60)}m"
    if delta < 129600:
        return f"{int(delta / 3600)}h"
    if delta < 2592000:
        return f"{int(delta / 86400)}d"
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def num(item, *keys):
    for key in keys:
        value = item.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def ratio(item, numerator_keys, denominator_keys):
    numerator = num(item, *numerator_keys)
    denominator = num(item, *denominator_keys)
    if numerator is None and denominator is None:
        return "-"
    return f"{'-' if numerator is None else numerator}/{'-' if denominator is None else denominator}"


def images_text(item, limit=60):
    images = []
    for container in item.get("containers") or []:
        image = (container or {}).get("image")
        if image and image not in images:
            images.append(image)
    return truncate(", ".join(images), limit) if images else "-"


def ingress_hosts(item):
    hosts = []
    for rule in item.get("rules") or []:
        host = (rule or {}).get("host")
        if host and host not in hosts:
            hosts.append(host)
    for endpoint in item.get("publicEndpoints") or []:
        host = (endpoint or {}).get("hostname")
        if host and host not in hosts:
            hosts.append(host)
    return truncate(", ".join(hosts), 60) if hosts else "-"


def service_ports(item):
    ports = []
    for port in item.get("ports") or []:
        number = (port or {}).get("port")
        if number is None:
            continue
        ports.append(f"{number}/{(port or {}).get('protocol') or 'TCP'}")
    return ", ".join(ports) if ports else "-"


def pvc_capacity(item):
    requests = (item.get("resources") or {}).get("requests") or {}
    return requests.get("storage") or "-"


def pvc_modes(item):
    modes = item.get("accessModes") or []
    return ",".join(str(mode) for mode in modes) if modes else "-"


def pvc_status(item):
    return item.get("state") or (item.get("status") or {}).get("phase") or "-"


def data_keys(item):
    keys = list((item.get("data") or {}).keys())
    keys.extend((item.get("binaryData") or {}).keys())
    return keys


def secret_type(item):
    value = item.get("_type") or item.get("secretType")
    if not value and item.get("type") not in (None, "secret"):
        value = item.get("type")
    return value or "Opaque"


def kind_row(kind, item):
    name = item.get("name") or item.get("id") or "-"
    namespace = item.get("namespaceId") or "-"
    age = fmt_age(created_ts(item))
    state = item.get("state") or "-"
    if kind in ("deployment", "statefulset"):
        ready = ratio(item, ("availableReplicas", "readyReplicas"), ("scale", "replicas"))
        return [name, namespace, ready, images_text(item), state, age]
    if kind == "daemonset":
        ready = ratio(item, ("ready", "numberReady"), ("desired", "desiredNumberScheduled"))
        return [name, namespace, ready, images_text(item), state, age]
    if kind == "job":
        completions = num(item, "completions")
        succeeded = num(item, "succeeded")
        return [
            name,
            namespace,
            "-" if completions is None else completions,
            "-" if succeeded is None else succeeded,
            state,
            age,
        ]
    if kind == "configmap":
        return [name, namespace, len(data_keys(item)), age]
    if kind == "secret":
        return [name, namespace, secret_type(item), len(data_keys(item)), age]
    if kind == "ingress":
        return [name, namespace, ingress_hosts(item), age]
    if kind == "service":
        return [
            name,
            namespace,
            item.get("clusterIp") or item.get("clusterIP") or "-",
            service_ports(item),
            age,
        ]
    if kind == "pvc":
        return [name, namespace, pvc_status(item), pvc_capacity(item), pvc_modes(item), age]
    return [name, namespace, age]


# ────────────────────────────────────────────────────────────
# 连接配置
# ────────────────────────────────────────────────────────────


def script_hint(command="config add", name=None):
    target = f" {name}" if name else ""
    return f"python3 {Path(__file__).resolve()} {command}{target}"


def config_instruction(name=None):
    return (
        "请让用户在他/她自己的终端执行下面的命令。\n"
        "命令会交互询问 Access Key 和 Secret Key，密钥不回显。\n"
        "不要把 Access Key 或 Secret Key 粘贴到对话里。\n"
        f"  {script_hint('config add', name)}\n"
        "还没有 API Key 时：登录 Rancher，打开 https://<Rancher 域名>/apikeys 创建。"
    )


def validate_server_name(name):
    if not name or not SERVER_NAME_RE.match(name):
        raise RancherError("配置名称只能包含字母、数字、点、下划线和短横线")
    return name


def server_file(name):
    return SERVERS_DIR / f"{name}.json"


@functools.lru_cache(maxsize=1)
def load_server_configs():
    """读取 ~/.rc2-deploy/servers/*.json，返回 {名称: {url, access_key, secret_key}}。"""
    configs = {}
    if not SERVERS_DIR.is_dir():
        return configs
    for path in sorted(SERVERS_DIR.glob("*.json")):
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"警告: 无法读取 {path}: {exc}", file=sys.stderr)
            continue
        if not isinstance(raw, dict) or not raw.get("url"):
            print(f"警告: {path} 缺少 url 字段，已忽略", file=sys.stderr)
            continue
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            print(
                f"警告: {path} 权限过宽（{oct(mode)}），建议执行 chmod 600",
                file=sys.stderr,
            )
        configs[path.stem] = {
            "url": raw["url"].rstrip("/"),
            "access_key": raw.get("access_key"),
            "secret_key": raw.get("secret_key"),
            "scope": raw.get("scope"),
        }
    return configs


def save_server_config(name, url, access_key, secret_key, scope=None):
    validate_server_name(name)
    SERVERS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass
    path = server_file(name)
    tmp = path.with_name(path.name + ".tmp")
    payload = {
        "url": url.rstrip("/"),
        "access_key": access_key,
        "secret_key": secret_key,
    }
    if scope:
        payload["scope"] = scope
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    load_server_configs.cache_clear()


def delete_server_config(name):
    path = server_file(name)
    if not path.exists():
        raise RancherError(f"配置「{name}」不存在")
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


def config_add(args):
    name = args.name or input("配置名称（例如 prod）: ").strip()
    validate_server_name(name)
    url = normalize_server_url(
        input("Rancher API 地址（例如 https://rancher.example.com/v3）: ")
    )
    print(f"请在浏览器打开 API Key 页面：{apikeys_url(url)}")
    print("点「创建 API Key」。集群级 token 选择目标集群。")
    print("如果页面不存在，从右上角头像进入 Account & API Keys。")
    access_key = input("Access Key（用户名）: ").strip()
    secret_key = getpass.getpass("Secret Key（输入不回显）: ").strip()
    if not access_key or not secret_key:
        raise RancherError("Access Key 和 Secret Key 不能为空")
    print("Token scope：直接回车表示所有集群。")
    print("可以填 cluster:c-xxxxx，或直接粘贴集群/项目页面 URL。")
    scope_input = input("Token scope: ")
    cluster_id, project_id = extract_references(scope_input)
    if scope_input.strip() and not cluster_id:
        raise RancherError(
            "无法识别 scope。请粘贴集群或项目页面 URL，或填 cluster:c-xxxxx。"
        )
    scope = f"cluster:{cluster_id}" if cluster_id else None
    save_server_config(name, url, access_key, secret_key, scope)
    print(f"已保存配置「{name}」: {url}")
    if scope:
        print(f"Token scope: {scope}（只访问该集群的 Kubernetes API）")
    if project_id:
        project_url = f"{url}/projects/{project_id}"
        project_name = input("项目名称（可选，回车跳过）: ").strip()
        record_project_url(
            find_projects_file(None), project_url, project_name or None
        )
        print(f"已记录项目地址: {project_url}")
        if project_name:
            print(f"已记录项目名称: {project_name}")
    print(f"文件权限已设为 600: {server_file(name)}")
    settings = load_settings()
    if not settings.get("default_server"):
        settings["default_server"] = name
        save_settings(settings)
        print(f"已将「{name}」设为默认配置")
    print("下一步校验连接:")
    print(f"  {script_hint('clusters', '--server ' + name)}")
    return 0


def config_list(args):
    configs = load_server_configs()
    settings = load_settings()
    default = settings.get("default_server")
    if not configs:
        print("还没有任何 Rancher 配置。")
        print(config_instruction())
        return 0
    if getattr(args, "json", False):
        payload = [
            {
                "name": name,
                "url": config["url"],
                "scope": config.get("scope") or "",
                "access_key": mask_secret(config.get("access_key")),
                "has_secret": bool(config.get("secret_key")),
                "default": name == default,
            }
            for name, config in configs.items()
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    rows = []
    for name, config in configs.items():
        rows.append(
            [
                name + ("（默认）" if name == default else ""),
                config["url"],
                config.get("scope") or "所有集群",
                mask_secret(config.get("access_key")),
                "已保存" if config.get("secret_key") else "缺失",
            ]
        )
    print_table(["名称", "地址", "Scope", "Access Key", "Secret Key"], rows)
    print("\n修改或新增配置: config add <名称>（由用户本人在终端执行）")
    return 0


def config_show(args):
    configs = load_server_configs()
    name = args.name or load_settings().get("default_server")
    if not name:
        raise RancherError("请指定配置名称：config show <名称>")
    config = configs.get(name)
    if not config:
        raise RancherError(
            f"配置「{name}」不存在，现有配置：{'、'.join(configs) or '无'}"
        )
    print(f"名称        {name}")
    print(f"地址        {config['url']}")
    print(f"Scope       {config.get('scope') or '所有集群'}")
    print(f"Access Key  {mask_secret(config.get('access_key'))}")
    print(f"Secret Key  {'已保存' if config.get('secret_key') else '缺失'}")
    return 0


def config_use(args):
    name = args.name
    if not name:
        raise RancherError("用法: config use <名称>")
    configs = load_server_configs()
    if name not in configs:
        raise RancherError(
            f"配置「{name}」不存在，现有配置：{'、'.join(configs) or '无'}"
        )
    settings = load_settings()
    settings["default_server"] = name
    save_settings(settings)
    print(f"已将默认配置设为「{name}」")
    return 0


def config_remove(args):
    name = args.name
    if not name:
        raise RancherError("用法: config remove <名称>")
    configs = load_server_configs()
    if name not in configs:
        raise RancherError(
            f"配置「{name}」不存在，现有配置：{'、'.join(configs) or '无'}"
        )
    delete_server_config(name)
    settings = load_settings()
    if settings.get("default_server") == name:
        remaining = [item for item in configs if item != name]
        settings["default_server"] = remaining[0] if remaining else None
        save_settings(settings)
    print(f"已删除配置「{name}」")
    return 0


def cmd_config(args):
    actions = {
        "add": config_add,
        "set": config_add,
        "list": config_list,
        "show": config_show,
        "use": config_use,
        "remove": config_remove,
    }
    action = args.action or "list"
    handler = actions.get(action)
    if not handler:
        raise RancherError(f"未知的 config 子命令：{action}")
    return handler(args)


# ────────────────────────────────────────────────────────────
# 项目记忆（.rancher-projects）
# ────────────────────────────────────────────────────────────


def find_projects_file(explicit=None):
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("RC2_PROJECTS_FILE")
    if env:
        return Path(env).expanduser()
    current = Path.cwd()
    for directory in [current, *current.parents]:
        candidate = directory / PROJECTS_FILE_NAME
        if candidate.exists():
            return candidate
    return current / PROJECTS_FILE_NAME


def parse_project_line(line):
    """解析一行项目记录，返回 (URL, 名称)。不是记录行时返回 None。"""
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    parts = text.split(None, 1)
    url = parts[0].rstrip("/")
    if not url.startswith(("http://", "https://")):
        return None
    name = parts[1].strip() if len(parts) > 1 else ""
    return url, name


def read_projects(path):
    """读取项目记录，返回 [{"url": ..., "name": ...}]，保持文件顺序。"""
    if not path.exists():
        return []
    entries = []
    index = {}
    for line in path.read_text("utf-8").splitlines():
        parsed = parse_project_line(line)
        if not parsed:
            continue
        url, name = parsed
        if url in index:
            entry = entries[index[url]]
            if name and not entry["name"]:
                entry["name"] = name
            continue
        index[url] = len(entries)
        entries.append({"url": url, "name": name})
    return entries


def read_project_urls(path):
    return [entry["url"] for entry in read_projects(path)]


def record_project_url(path, url, name=None):
    """记录项目 URL。name 非空时一并记录名称；已有名称不会被空值覆盖。"""
    url = (url or "").rstrip("/")
    if not url:
        return False
    name = (name or "").strip()
    new_line = f"{url}  {name}" if name else url
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# rc2-deploy: 本项目的 Rancher 项目连接\n" + new_line + "\n", "utf-8"
        )
        return True
    lines = path.read_text("utf-8").splitlines()
    changed = False
    found = False
    for index, line in enumerate(lines):
        parsed = parse_project_line(line)
        if not parsed:
            continue
        existing_url, existing_name = parsed
        if existing_url != url:
            continue
        found = True
        merged = name or existing_name
        replacement = f"{url}  {merged}" if merged else url
        if lines[index] != replacement:
            lines[index] = replacement
            changed = True
        break
    if not found:
        lines.append(new_line)
        changed = True
    if changed:
        path.write_text("\n".join(lines) + "\n", "utf-8")
    return changed


def format_saved_projects(entries, configs=None):
    lines = []
    for index, entry in enumerate(entries, 1):
        url = entry["url"] if isinstance(entry, dict) else entry
        name = entry.get("name", "") if isinstance(entry, dict) else ""
        server = server_name_for_url(url, configs) if configs else "-"
        label = project_id_of(url) + (f" {name}" if name else "")
        lines.append(f"  {index}. {label}（server: {server or '-'}）")
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────
# 配置与项目选择
# ────────────────────────────────────────────────────────────


def resolve_server(args):
    configs = load_server_configs()
    if not configs:
        raise RancherError("还没有任何 Rancher 配置。\n" + config_instruction())
    explicit = getattr(args, "server", None) or os.environ.get("RC2_SERVER")
    if explicit:
        config = configs.get(explicit)
        if not config:
            raise RancherError(
                f"配置「{explicit}」不存在，现有配置：{'、'.join(configs)}"
            )
        return explicit, config
    default = load_settings().get("default_server")
    if default and default in configs:
        return default, configs[default]
    if len(configs) == 1:
        name = next(iter(configs))
        return name, configs[name]
    raise RancherError(
        f"存在多个 Rancher 配置，请用 --server 指定。现有配置：{'、'.join(configs)}"
    )


def match_server_config(url, configs, origin_only=False):
    """按 scheme、host、port 匹配配置。默认还要匹配根路径。"""
    parsed = urllib.parse.urlsplit(url)
    matches = []
    for name, config in configs.items():
        base = urllib.parse.urlsplit(config["url"])
        if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
            continue
        base_path = base.path.rstrip("/")
        if not origin_only and not parsed.path.startswith(base_path + "/"):
            continue
        matches.append((len(base_path), name, config))
    if not matches:
        raise RancherError(
            f"地址不属于任何 Rancher 配置：{url}\n"
            f"已有配置：{'、'.join(configs)}\n" + config_instruction()
        )
    matches.sort(key=lambda item: item[0], reverse=True)
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        raise RancherError(
            f"地址同时匹配多个配置：{'、'.join(item[1] for item in matches)}。"
            "请用 --server 指定配置。"
        )
    return matches[0][1], matches[0][2]


def server_name_for_url(url, configs):
    try:
        return match_server_config(url, configs)[0]
    except RancherError:
        return None


def finalize_project(server_name, config, project_url):
    """校验项目与 token scope，并返回解析结果。"""
    cluster = scope_cluster(config)
    project_cluster = project_id_of(project_url).split(":")[0]
    if cluster and project_cluster != cluster:
        raise RancherError(
            f"项目 {project_id_of(project_url)} 不属于 scope 集群 {cluster}。\n"
            f"配置「{server_name}」的 token 只能访问集群 {cluster}。"
        )
    return server_name, config, project_url


def resolve_project_target(args):
    """返回 (server 名称, server 配置, 规范化项目地址)。"""
    configs = load_server_configs()
    if not configs:
        raise RancherError("还没有任何 Rancher 配置。\n" + config_instruction())
    projects_file = find_projects_file(getattr(args, "projects_file", None))
    entries = read_projects(projects_file)
    saved = [entry["url"] for entry in entries]
    raw = (getattr(args, "project", None) or os.environ.get("RC2_PROJECT") or "").strip()

    if not raw:
        candidates = saved
        explicit_server = getattr(args, "server", None)
        if explicit_server:
            candidates = [
                url for url in saved if server_name_for_url(url, configs) == explicit_server
            ]
        if len(candidates) == 1:
            raw = candidates[0]
        elif not candidates:
            raise RancherError(
                "缺少项目。请用 --project 提供项目地址或项目 ID。\n"
                "项目页面示例：https://rancher.example.com/p/c-abc123:p-def456/\n"
                f"API 地址示例：{next(iter(configs.values()))['url']}/projects/c-abc123:p-def456\n"
                "项目 ID 示例：c-abc123:p-def456（配合 --server）\n"
                "以上信息来自 Rancher 页面地址或 clusters/projects 命令。"
            )
        else:
            raise RancherError(
                "有多个已记录的项目，请用 --project 选择：\n"
                + format_saved_projects(entries, configs)
            )

    if raw.startswith("http://") or raw.startswith("https://"):
        cluster_id, project_id = extract_references(raw)
        if not project_id:
            if cluster_id:
                raise RancherError(
                    f"这是集群地址，不是项目地址：{raw}\n"
                    "请提供项目页面地址（含 /p/c-xxxxx:p-yyyy/），或项目 ID。"
                )
            raise RancherError(
                f"无法从地址中识别项目：{raw}\n"
                "示例：https://rancher.example.com/p/c-abc123:p-def456/"
            )
        explicit_server = getattr(args, "server", None)
        origin_only = "/projects/" not in urllib.parse.urlsplit(raw).path
        if explicit_server:
            config = configs.get(explicit_server)
            if not config:
                raise RancherError(
                    f"配置「{explicit_server}」不存在，现有配置：{'、'.join(configs)}"
                )
            base = urllib.parse.urlsplit(config["url"])
            parsed = urllib.parse.urlsplit(raw)
            if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
                raise RancherError(
                    f"--server「{explicit_server}」和项目地址不匹配（主机不同）"
                )
            server_name = explicit_server
        else:
            server_name, config = match_server_config(
                raw, configs, origin_only=origin_only
            )
        return finalize_project(
            server_name, config, f"{config['url']}/projects/{project_id}"
        )

    if PROJECT_ID_RE.match(raw):
        server_name, config = resolve_server(args)
        return finalize_project(server_name, config, f"{config['url']}/projects/{raw}")

    if raw.isdigit():
        index = int(raw)
        if 1 <= index <= len(saved):
            project_url = saved[index - 1]
            server_name, config = match_server_config(project_url, configs)
            return finalize_project(server_name, config, project_url)
        raise RancherError(f"项目序号 {index} 超出范围。用 saved 查看已记录的项目。")

    matches = [
        url
        for url in saved
        if project_id_of(url) == raw or project_id_of(url).endswith(":" + raw)
    ]
    if len(matches) == 1:
        project_url = matches[0]
        server_name, config = match_server_config(project_url, configs)
        return finalize_project(server_name, config, project_url)
    if len(matches) > 1:
        raise RancherError(
            f"「{raw}」匹配多个项目，请用序号或完整地址：\n"
            + format_saved_projects(matches, configs)
        )

    name_matches = [
        entry["url"]
        for entry in entries
        if entry["name"] and entry["name"].casefold() == raw.casefold()
    ]
    if len(name_matches) == 1:
        project_url = name_matches[0]
        server_name, config = match_server_config(project_url, configs)
        return finalize_project(server_name, config, project_url)
    if len(name_matches) > 1:
        raise RancherError(
            f"项目名称「{raw}」对应多个项目，请用序号或完整地址：\n"
            + format_saved_projects(
                [entry for entry in entries if entry["url"] in name_matches], configs
            )
        )

    raise RancherError(
        f"无法识别项目「{raw}」。请提供完整项目地址、项目 ID 或已记录的项目名称。\n"
        "已记录的项目：\n" + (format_saved_projects(entries, configs) or "  无")
    )


def cmd_saved(args):
    path = find_projects_file(getattr(args, "projects_file", None))
    entries = read_projects(path)
    if args.json:
        print(
            json.dumps(
                {"file": str(path), "projects": entries},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not entries:
        print(f"{path} 中没有记录项目。")
        print("第一次成功执行命令后会自动记录。")
        print("也可以每行写一个项目地址，地址后写项目名（可选），两者用空格分隔。")
        return 0
    configs = load_server_configs()
    rows = []
    for index, entry in enumerate(entries, 1):
        url = entry["url"]
        rows.append(
            [
                index,
                project_id_of(url),
                entry.get("name") or "-",
                project_id_of(url).split(":")[0],
                server_name_for_url(url, configs) or "-",
                url,
            ]
        )
    print(f"项目列表: {path}\n")
    print_table(["#", "PROJECT", "NAME", "CLUSTER", "SERVER", "URL"], rows)
    print("\n用法：--project <序号|项目 ID|项目名称|URL>；该文件是纯文本，可直接编辑")
    return 0


def cmd_remember(args):
    name = (args.name or "").strip()
    if not name:
        raise RancherError("用法: remember <项目名称> --project <项目地址或 ID>")
    _, _, project_url = resolve_project_target(args)
    path = find_projects_file(getattr(args, "projects_file", None))
    record_project_url(path, project_url, name)
    print(f"已记录项目：{name} → {project_url}")
    return 0


# ────────────────────────────────────────────────────────────
# Rancher HTTP 客户端
# ────────────────────────────────────────────────────────────


class RancherClient:
    def __init__(
        self,
        base,
        access_key,
        secret_key,
        insecure=False,
        http_timeout=DEFAULT_HTTP_TIMEOUT,
        verbose=False,
        scoped=False,
    ):
        self.base = base.rstrip("/")
        self.http_timeout = http_timeout
        self.verbose = verbose
        self.scoped = scoped
        self._auth = "Basic " + base64.b64encode(
            f"{access_key}:{secret_key}".encode()
        ).decode()
        context = ssl.create_default_context()
        if insecure:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context)
        )

    @staticmethod
    def _encode(url):
        parts = urllib.parse.urlsplit(url)
        path = urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=~-._")
        query = urllib.parse.quote(parts.query, safe="=&%:@!$'()*+,;/?[]{}~-._")
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, ""))

    def request(self, method, url, payload=None, raw=False, body_text=None, content_type=None):
        headers = {
            "Authorization": self._auth,
            "Accept": "application/json, text/plain, */*",
            "User-Agent": f"rc2-deploy/{VERSION}",
        }
        data = None
        if body_text is not None:
            data = body_text.encode("utf-8")
            headers["Content-Type"] = content_type or "application/yaml"
        elif payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = content_type or "application/json"
        request = urllib.request.Request(
            self._encode(url), data=data, headers=headers, method=method
        )
        if self.verbose:
            print(f"  HTTP {method} {url}", file=sys.stderr)
        try:
            with self._opener.open(request, timeout=self.http_timeout) as response:
                body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            message = http_error_message(method, url, exc.code, text)
            if exc.code == 401:
                message += f"\n请在 {apikeys_url(self.base)} 重新创建 API Key。"
                if self.scoped:
                    message += (
                        "\n当前配置是集群级 scope token。请确认 scope 集群与项目集群一致，"
                        "并确认 token 未过期。"
                    )
            raise RancherError(message, status=exc.code)
        except urllib.error.URLError as exc:
            raise RancherError(f"无法连接 Rancher（{url}）：{exc.reason}")
        if raw:
            return body
        if not body.strip():
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body

    def get_json(self, url):
        return self.request("GET", url)

    def get_collection(self, url, page_size=200):
        items = []
        next_url = add_query(url, limit=page_size)
        seen = set()
        while next_url and next_url not in seen:
            seen.add(next_url)
            data = self.get_json(next_url)
            if not isinstance(data, dict):
                raise RancherError(f"返回内容不是 JSON 对象：{next_url}")
            items.extend(data.get("data") or [])
            next_url = (data.get("pagination") or {}).get("next")
            if next_url:
                next_url = urllib.parse.urljoin(self.base, next_url)
        return items


def build_client(config, args):
    access_key = config.get("access_key")
    secret_key = config.get("secret_key")
    if not access_key or not secret_key:
        raise RancherError("配置缺少 Access Key 或 Secret Key。\n" + config_instruction())
    cluster = scope_cluster(config)
    base = config["url"]
    if cluster:
        base = f"{server_root(config['url'])}/k8s/clusters/{cluster}"
    return RancherClient(
        base,
        access_key,
        secret_key,
        insecure=getattr(args, "insecure", False),
        http_timeout=getattr(args, "http_timeout", DEFAULT_HTTP_TIMEOUT),
        verbose=getattr(args, "verbose", False),
        scoped=bool(cluster),
    )


# ────────────────────────────────────────────────────────────
# 项目上下文
# ────────────────────────────────────────────────────────────


class Project:
    def __init__(self, client, url, data):
        self.client = client
        self.url = url
        self.data = data
        self.id = data.get("id") or project_id_of(url)
        self.name = data.get("name") or self.id
        self.cluster_id = data.get("clusterId") or self.id.split(":")[0]
        self.links = data.get("links") or {}
        self.actions = data.get("actions") or {}
        self._collections = {}
        self._namespaces = None
        self.backend = V3Backend(self)

    @property
    def display(self):
        return f"{self.name}（{self.id}）"

    def _probe(self, url):
        try:
            self.client.get_json(add_query(url, limit=1))
            return True
        except RancherError as exc:
            if exc.status == 404:
                return False
            raise

    def _find_collection(self, candidates, label):
        for url in dedupe(candidates):
            if self._probe(url):
                return url
        raise RancherError(
            f"找不到{label}的 API 地址。项目 ID：{self.id}\n"
            f"项目链接：{', '.join(sorted(self.links)) or '无'}"
        )

    def collection(self, kind):
        if kind in self._collections:
            return self._collections[kind]
        spec = KINDS[kind]
        candidates = []
        for name in spec["collections"]:
            for key in (name, name.lower()):
                if self.links.get(key):
                    candidates.append(self.links[key])
        for name in spec["collections"]:
            if name == "workloads":
                candidates.append(f"{self.client.base}/project/{self.id}/workloads")
                candidates.append(f"{self.client.base}/projects/{self.id}/workloads")
            else:
                candidates.append(f"{self.client.base}/projects/{self.id}/{name}")
                candidates.append(f"{self.client.base}/project/{self.id}/{name}")
        url = self._find_collection(candidates, spec["label"])
        self._collections[kind] = url
        return url

    def namespaces(self):
        if self._namespaces:
            return self._namespaces
        candidates = []
        if self.links.get("namespaces"):
            candidates.append(self.links["namespaces"])
        candidates.extend(
            [
                f"{self.client.base}/projects/{self.id}/namespaces",
                f"{self.client.base}/project/{self.id}/namespaces",
                f"{self.client.base}/clusters/{self.cluster_id}/namespaces",
                f"{self.client.base}/cluster/{self.cluster_id}/namespaces",
            ]
        )
        url = self._find_collection(candidates, "命名空间")
        cluster_scope = "/cluster/" in url or "/clusters/" in url
        items = self.client.get_collection(url)
        if cluster_scope:
            items = [item for item in items if item.get("projectId") == self.id]
        items.sort(key=lambda item: item.get("name") or "")
        self._namespaces = (url, items)
        return self._namespaces


def fetch_project(client, project_url):
    data = client.get_json(project_url)
    if not isinstance(data, dict) or not data.get("id"):
        raise RancherError(f"无法读取项目：{project_url}")
    if data.get("type") not in (None, "project"):
        raise RancherError(
            f"地址不是项目：{project_url}\n项目地址要包含 /projects/<项目ID>"
        )
    return Project(client, project_url, data)


class ScopedProject:
    """集群级 token 的项目上下文。只保留项目 ID，不访问 /v3。"""

    def __init__(self, client, project_url, cluster_id):
        self.client = client
        self.url = project_url
        self.id = project_id_of(project_url)
        self.cluster_id = cluster_id
        self.name = self.id
        self.backend = K8sBackend(client, self.id)

    @property
    def display(self):
        return f"{self.name}（{self.id}）"


# ────────────────────────────────────────────────────────────
# Kubernetes API 后端（集群级 scope token）
# ────────────────────────────────────────────────────────────


def k8s_collection_path(kind, namespace=None):
    group, plural = K8S_KINDS[kind]
    prefix = "/api/v1" if group == "v1" else f"/apis/{group}"
    if namespace:
        return f"{prefix}/namespaces/{urllib.parse.quote(namespace)}/{plural}"
    return f"{prefix}/{plural}"


def adapt_k8s_resource(kind, obj, self_url):
    """把 Kubernetes 对象转换成脚本内部使用的字段。"""
    meta = obj.get("metadata") or {}
    spec = obj.get("spec") or {}
    status = obj.get("status") or {}
    name = meta.get("name") or "-"
    namespace = meta.get("namespace")
    view = {
        "id": f"{kind}:{namespace or ''}:{name}",
        "name": name,
        "namespaceId": namespace,
        "created": meta.get("creationTimestamp"),
        "state": status.get("phase") or ("active" if status else "-"),
        "links": {"self": self_url, "remove": self_url},
        "actions": {},
        "raw": obj,
    }
    if kind in ("deployment", "statefulset", "daemonset"):
        template = spec.get("template") or {}
        view["containers"] = (template.get("spec") or {}).get("containers") or []
    if kind in ("deployment", "statefulset"):
        view["scale"] = spec.get("replicas")
        view["availableReplicas"] = status.get("availableReplicas", status.get("readyReplicas"))
    if kind == "daemonset":
        view["desired"] = status.get("desiredNumberScheduled")
        view["ready"] = status.get("numberReady")
    if kind in ("deployment", "statefulset", "daemonset"):
        view["actions"] = {"redeploy": self_url}
    if kind == "job":
        view["completions"] = spec.get("completions")
        view["succeeded"] = status.get("succeeded")
        view["containers"] = (spec.get("template") or {}).get("spec", {}).get("containers") or []
    if kind in ("configmap", "secret"):
        view["data"] = obj.get("data") or {}
    if kind == "secret":
        view["_type"] = obj.get("type")
    if kind == "ingress":
        view["rules"] = spec.get("rules") or []
    if kind == "service":
        view["clusterIp"] = spec.get("clusterIP")
        view["ports"] = spec.get("ports") or []
    if kind == "pvc":
        view["resources"] = {"requests": (status.get("capacity") or {})}
        view["accessModes"] = spec.get("accessModes") or []
        view["state"] = status.get("phase") or view["state"]
    return view


def clean_k8s_object(obj):
    """删除只读字段，使输出清单可以重新 apply。"""
    clone = json.loads(json.dumps(obj))
    clone.pop("status", None)
    meta = clone.get("metadata")
    if isinstance(meta, dict):
        for key in (
            "uid",
            "resourceVersion",
            "generation",
            "creationTimestamp",
            "managedFields",
            "selfLink",
        ):
            meta.pop(key, None)
        annotations = meta.get("annotations")
        if isinstance(annotations, dict):
            annotations.pop("kubectl.kubernetes.io/last-applied-configuration", None)
    spec = clone.get("spec")
    if isinstance(spec, dict):
        for key in ("clusterIP", "clusterIPs", "ipFamilies", "ipFamilyPolicy", "volumeName"):
            spec.pop(key, None)
        template = spec.get("template")
        if isinstance(template, dict):
            template_meta = template.get("metadata")
            if isinstance(template_meta, dict):
                template_meta.pop("creationTimestamp", None)
    return clone


YAML_SPECIAL_START = set("-?:,[]{}#&*!|>'\"%@`")
YAML_KEYWORDS = {"true", "false", "null", "yes", "no", "on", "off", "~"}
YAML_NUMBER_RE = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")


def yaml_scalar(value):
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "":
        return "''"
    needs_quote = (
        text.strip() != text
        or text[0] in YAML_SPECIAL_START
        or ": " in text
        or " #" in text
        or text.lower() in YAML_KEYWORDS
        or YAML_NUMBER_RE.match(text)
        or text.endswith(":")
    )
    if needs_quote:
        return "'" + text.replace("'", "''") + "'"
    return text


def to_yaml_lines(value, indent=0):
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            return [pad + "{}"]
        lines = []
        for key, item in value.items():
            if isinstance(item, str) and "\n" in item:
                lines.append(f"{pad}{key}: |")
                for line in item.splitlines():
                    lines.append("  " * (indent + 1) + line)
            elif isinstance(item, (dict, list)) and item:
                lines.append(f"{pad}{key}:")
                lines.extend(to_yaml_lines(item, indent + 1))
            else:
                lines.append(f"{pad}{key}: {yaml_scalar(item)}")
        return lines
    if isinstance(value, list):
        if not value:
            return [pad + "[]"]
        lines = []
        for item in value:
            if isinstance(item, dict) and item:
                nested = to_yaml_lines(item, indent + 1)
                lines.append(f"{pad}- {nested[0].strip()}")
                lines.extend(nested[1:])
            elif isinstance(item, list) and item:
                lines.append(f"{pad}-")
                lines.extend(to_yaml_lines(item, indent + 1))
            else:
                lines.append(f"{pad}- {yaml_scalar(item)}")
        return lines
    return [pad + yaml_scalar(value)]


def to_yaml(value):
    return "\n".join(to_yaml_lines(value)) + "\n"


def split_manifest_documents(text):
    documents = []
    current = []
    for line in text.splitlines():
        if line.strip() == "---":
            documents.append("\n".join(current))
            current = []
            continue
        current.append(line)
    documents.append("\n".join(current))
    return [doc for doc in documents if doc.strip()]


def parse_manifest_document(doc):
    """取清单的 kind、apiVersion、metadata.name 和 metadata.namespace。"""
    stripped = doc.lstrip()
    if stripped.startswith("{"):
        payload = json.loads(doc)
        meta = payload.get("metadata") or {}
        return (
            payload.get("kind"),
            payload.get("apiVersion"),
            meta.get("name"),
            meta.get("namespace"),
        )
    kind = api_version = name = namespace = None
    in_metadata = False
    for raw_line in doc.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("---"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            in_metadata = key == "metadata"
            if key == "kind":
                kind = value
            elif key == "apiVersion":
                api_version = value
            continue
        if in_metadata and indent == 2:
            key, _, value = line.strip().partition(":")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key == "name":
                name = value
            elif key == "namespace":
                namespace = value
    return kind, api_version, name, namespace


class V3Backend:
    """通过 Rancher /v3 API 操作资源。"""

    def __init__(self, project):
        self.project = project

    def namespaces(self):
        _, items = self.project.namespaces()
        return items

    def list(self, kind, namespace=None):
        return list_resources(self.project, kind, namespace)

    def find(self, kind, name, namespace=None):
        return find_resource(self.project, kind, name, namespace)

    def yaml(self, kind, resource):
        link = resource_link(resource, "yaml")
        if not link:
            raise RancherError(
                f"{KINDS[kind]['label']}「{resource.get('name')}」没有 yaml 链接。"
                "请用 --json 查看。"
            )
        return self.project.client.request("GET", link, raw=True).rstrip()

    def _import_action(self):
        action = self.project.actions.get("importYaml")
        if not action:
            cluster = self.project.client.get_json(
                f"{self.project.client.base}/clusters/{self.project.cluster_id}"
            )
            action = (cluster.get("actions") or {}).get("importYaml")
        if not action:
            raise RancherError(
                "项目和集群都没有 importYaml 动作。请确认 Rancher 版本是 2.x，并且 API Key 有写权限。"
            )
        return action

    def apply_plan(self, text):
        return [("POST", self._import_action(), "项目 importYaml")]

    def apply(self, text):
        self.project.client.request("POST", self._import_action(), {"yaml": text})

    def delete_url(self, kind, resource):
        link = resource_link(resource, "remove", "self")
        if not link:
            raise RancherError(f"{KINDS[kind]['label']}「{resource.get('name')}」没有删除链接。")
        return "DELETE", link

    def delete(self, kind, resource):
        _, link = self.delete_url(kind, resource)
        self.project.client.request("DELETE", link)

    def redeploy_action(self, kind, resource):
        action = (resource.get("actions") or {}).get("redeploy")
        if not action:
            available = ", ".join(sorted((resource.get("actions") or {}).keys())) or "无"
            raise RancherError(
                f"{KINDS[kind]['label']}「{resource.get('name')}」不支持 redeploy。"
                f"可用动作：{available}。\n"
                "请先用 get --yaml 导出清单，再用 delete 和 apply 重建。"
            )
        return "POST", action

    def redeploy(self, kind, resource):
        _, action = self.redeploy_action(kind, resource)
        self.project.client.request("POST", action)


class K8sBackend:
    """通过 Rancher 的 Kubernetes API 代理操作单个集群。"""

    def __init__(self, client, project_id):
        self.client = client
        self.project_id = project_id
        self._namespace_cache = None

    def _url(self, path):
        return self.client.base + path

    def _namespace_objects(self):
        """缓存命名空间，返回 {名称: (项目 ID, 对象)}。"""
        if self._namespace_cache is None:
            data = self.client.get_json(self._url("/api/v1/namespaces"))
            cache = {}
            for obj in data.get("items") or []:
                meta = obj.get("metadata") or {}
                annotations = meta.get("annotations") or {}
                cache[meta.get("name")] = (
                    annotations.get("field.cattle.io/projectId"),
                    obj,
                )
            self._namespace_cache = cache
        return self._namespace_cache

    def _project_namespaces(self):
        return {
            name
            for name, (project_id, _) in self._namespace_objects().items()
            if project_id == self.project_id
        }

    def namespaces(self):
        items = []
        for name in self._project_namespaces():
            obj = self._namespace_objects()[name][1]
            meta = obj.get("metadata") or {}
            items.append(
                {
                    "name": name,
                    "state": (obj.get("status") or {}).get("phase") or "-",
                    "created": meta.get("creationTimestamp"),
                }
            )
        items.sort(key=lambda item: item.get("name") or "")
        return items

    def list(self, kind, namespace=None):
        data = self.client.get_json(self._url(k8s_collection_path(kind, namespace)))
        allowed = self._project_namespaces()
        items = []
        for obj in data.get("items") or []:
            meta = obj.get("metadata") or {}
            item_namespace = meta.get("namespace")
            if item_namespace not in allowed:
                continue
            self_url = self._url(
                k8s_collection_path(kind, item_namespace)
                + "/"
                + urllib.parse.quote(meta.get("name") or "")
            )
            items.append(adapt_k8s_resource(kind, obj, self_url))
        items.sort(
            key=lambda item: (item.get("namespaceId") or "", item.get("name") or "")
        )
        return items

    def find(self, kind, name, namespace=None):
        label = KINDS[kind]["label"]
        if namespace:
            if namespace not in self._project_namespaces():
                raise RancherError(
                    f"命名空间 {namespace} 不属于项目 {self.project_id}。"
                )
            url = self._url(
                k8s_collection_path(kind, namespace) + "/" + urllib.parse.quote(name)
            )
            try:
                obj = self.client.get_json(url)
            except RancherError as exc:
                if exc.status == 404:
                    raise RancherError(
                        f"命名空间 {namespace} 中找不到 {label}「{name}」"
                    )
                raise
            return adapt_k8s_resource(kind, obj, url)
        matches = [item for item in self.list(kind) if item.get("name") == name]
        if not matches:
            raise RancherError(f"项目 {self.project_id} 中找不到 {label}「{name}」")
        if len(matches) > 1:
            namespaces = sorted({str(item.get("namespaceId")) for item in matches})
            raise RancherError(
                f"{label}「{name}」在多个命名空间存在：{', '.join(namespaces)}。"
                "请用 --namespace 指定。"
            )
        return matches[0]

    def yaml(self, kind, resource):
        return to_yaml(clean_k8s_object(resource["raw"]))

    def apply_plan(self, text):
        plan = []
        for document in split_manifest_documents(text):
            kind_raw, api_version, name, namespace = parse_manifest_document(document)
            kind = MANIFEST_KINDS.get((kind_raw or "").lower())
            if not kind:
                raise RancherError(
                    f"清单的 kind「{kind_raw or '空'}」不受支持。"
                    f"可用：{', '.join(sorted(MANIFEST_KINDS))}"
                )
            if not name:
                raise RancherError("清单缺少 metadata.name")
            if not namespace:
                raise RancherError("清单缺少 metadata.namespace。集群级 scope 模式必须指定命名空间。")
            prefix = "/api/v1" if api_version in (None, "v1") else f"/apis/{api_version}"
            plural = K8S_KINDS[kind][1]
            url = self._url(
                f"{prefix}/namespaces/{urllib.parse.quote(namespace)}/{plural}/"
                f"{urllib.parse.quote(name)}"
            )
            url = add_query(url, fieldManager="rc2-deploy", force="true")
            plan.append(
                ("PATCH", url, f"{KINDS[kind]['label']}/{name} @ {namespace}", document)
            )
        return plan

    def apply(self, text):
        for _, url, _, document in self.apply_plan(text):
            self.client.request(
                "PATCH",
                url,
                body_text=document,
                content_type="application/apply-patch+yaml",
            )

    def delete_url(self, kind, resource):
        link = resource.get("links", {}).get("self")
        if not link:
            raise RancherError(f"{KINDS[kind]['label']}「{resource.get('name')}」没有删除链接。")
        return "DELETE", link

    def delete(self, kind, resource):
        _, link = self.delete_url(kind, resource)
        self.client.request("DELETE", link)

    def redeploy_action(self, kind, resource):
        if kind not in ("deployment", "statefulset", "daemonset"):
            raise RancherError(
                f"{KINDS[kind]['label']}不支持 redeploy。\n"
                "请先用 get --yaml 导出清单，再用 delete 和 apply 重建。"
            )
        return "PATCH", resource["links"]["self"]

    def redeploy(self, kind, resource):
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = {
            "spec": {"template": {"metadata": {"annotations": {"cattle.io/timestamp": stamp}}}}
        }
        self.client.request(
            "PATCH",
            resource["links"]["self"],
            payload,
            content_type="application/merge-patch+json",
        )


# ────────────────────────────────────────────────────────────
# 资源操作
# ────────────────────────────────────────────────────────────


def list_resources(project, kind, namespace=None):
    url = project.collection(kind)
    items = project.client.get_collection(url)
    if url.rstrip("/").endswith("/workloads"):
        items = [item for item in items if item.get("type") == kind]
    if namespace:
        items = [item for item in items if item.get("namespaceId") == namespace]
    items.sort(key=lambda item: (item.get("namespaceId") or "", item.get("name") or ""))
    return items


def find_resource(project, kind, name, namespace=None):
    label = KINDS[kind]["label"]
    matches = [
        item
        for item in list_resources(project, kind, namespace)
        if item.get("name") == name
    ]
    if not matches:
        where = f"命名空间 {namespace} 中" if namespace else "项目中"
        raise RancherError(f"{where}找不到 {label}「{name}」")
    if len(matches) > 1:
        namespaces = sorted({str(item.get("namespaceId")) for item in matches})
        raise RancherError(
            f"{label}「{name}」在多个命名空间存在：{', '.join(namespaces)}。"
            "请用 --namespace 指定。"
        )
    return matches[0]


def resource_link(resource, *keys):
    links = resource.get("links") or {}
    for key in keys:
        if links.get(key):
            return links[key]
    return None


def redact_resource(kind, resource):
    if kind != "secret":
        return resource
    clone = json.loads(json.dumps(resource))
    for container in (clone, clone.get("raw") or {}):
        for key in ("data", "stringData"):
            if isinstance(container.get(key), dict):
                container[key] = {name: "***" for name in container[key]}
    return clone


# ────────────────────────────────────────────────────────────
# 命令实现
# ────────────────────────────────────────────────────────────


def cmd_clusters(args, client):
    items = client.get_collection(f"{client.base}/clusters")
    items.sort(key=lambda item: item.get("name") or "")
    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 0
    if not items:
        print("没有可访问的集群。")
        return 0
    rows = []
    for item in items:
        version = (item.get("version") or {}).get("gitVersion") or "-"
        rows.append(
            [
                item.get("id") or "-",
                item.get("name") or "-",
                item.get("state") or "-",
                version,
                fmt_age(created_ts(item)),
            ]
        )
    print_table(["ID", "NAME", "STATE", "VERSION", "AGE"], rows)
    print("\n下一步：projects --cluster <集群 ID>")
    return 0


def cmd_clusters_scoped(args, client, cluster_id):
    version = client.get_json(f"{client.base}/version")
    git_version = version.get("gitVersion") or "-"
    item = {
        "id": cluster_id,
        "name": cluster_id,
        "version": {"gitVersion": git_version},
        "links": {"self": client.base},
    }
    if args.json:
        print(json.dumps([item], ensure_ascii=False, indent=2))
        return 0
    print_table(["ID", "NAME", "VERSION"], [[cluster_id, cluster_id, git_version]])
    print("\n集群级 scope token：只能访问这个集群。")
    print("下一步：用 --project 指定该集群内的项目。")
    return 0


def cmd_projects(args, client):
    url = f"{client.base}/projects"
    if args.cluster:
        url = add_query(url, clusterId=args.cluster)
    items = client.get_collection(url)
    items.sort(key=lambda item: (item.get("clusterId") or "", item.get("name") or ""))
    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 0
    if not items:
        print("没有可访问的项目。")
        return 0
    rows = []
    for item in items:
        rows.append(
            [
                item.get("id") or "-",
                item.get("name") or "-",
                item.get("clusterId") or "-",
                item.get("state") or "-",
            ]
        )
    print_table(["PROJECT", "NAME", "CLUSTER", "STATE"], rows)
    print(
        "\n项目地址 = Rancher 地址 + /projects/<PROJECT>，"
        "例如 https://rancher.example.com/v3/projects/c-abc123:p-def456"
    )
    return 0


def cmd_namespaces(args, project):
    items = project.backend.namespaces()
    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 0
    if not items:
        print(f"项目 {project.display} 中没有命名空间。")
        return 0
    rows = [
        [item.get("name") or "-", item.get("state") or "-", fmt_age(created_ts(item))]
        for item in items
    ]
    print_table(["NAME", "STATE", "AGE"], rows)
    print(f"\n项目：{project.display}")
    return 0


def cmd_ls(args, project):
    kind = normalize_kind(args.kind)
    items = project.backend.list(kind, args.namespace)
    if args.json:
        print(
            json.dumps(
                {
                    "project": project.id,
                    "kind": kind,
                    "items": [redact_resource(kind, item) for item in items],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not items:
        scope = f"命名空间 {args.namespace}" if args.namespace else "项目"
        print(f"{scope} {project.display} 中没有 {KINDS[kind]['label']}。")
        return 0
    print_table(
        COLUMNS[kind], [kind_row(kind, item) for item in items]
    )
    print(f"\n项目：{project.display}  共 {len(items)} 个 {KINDS[kind]['label']}")
    print(
        f"查看详情：get {kind}/<名称> --namespace <命名空间>；"
        "导出清单：加 --yaml"
    )
    return 0


def print_summary(project, kind, resource):
    def line(label, value):
        print(f"{pad(label, SUMMARY_LABEL_WIDTH)}{value}")

    name = resource.get("name") or resource.get("id") or "-"
    namespace = resource.get("namespaceId") or "-"
    line("类型", KINDS[kind]["label"])
    line("名称", name)
    line("命名空间", namespace)
    line("项目", project.display)
    line("状态", resource.get("state") or "-")
    if kind in ("deployment", "statefulset"):
        line(
            "副本",
            ratio(resource, ("availableReplicas", "readyReplicas"), ("scale", "replicas")),
        )
    if kind == "daemonset":
        line(
            "副本",
            ratio(resource, ("ready", "numberReady"), ("desired", "desiredNumberScheduled")),
        )
    if kind == "job":
        completions = num(resource, "completions")
        succeeded = num(resource, "succeeded")
        line(
            "完成",
            f"{'-' if succeeded is None else succeeded}/"
            f"{'-' if completions is None else completions}",
        )
    if resource.get("containers"):
        line("镜像", images_text(resource, 80))
    if kind == "secret":
        line("类型", secret_type(resource))
        line("数据键", ", ".join(sorted(data_keys(resource))) or "-")
    if kind == "service":
        line("集群 IP", resource.get("clusterIp") or resource.get("clusterIP") or "-")
        line("端口", service_ports(resource))
    if kind == "ingress":
        line("域名", ingress_hosts(resource))
    if kind == "pvc":
        line("容量", pvc_capacity(resource))
        line("访问模式", pvc_modes(resource))
    actions = sorted((resource.get("actions") or {}).keys())
    line("动作", ", ".join(actions) or "-")
    line("创建", fmt_age(created_ts(resource)))
    line("链接", resource_link(resource, "self") or project.url)


def cmd_get(args, project):
    kind, name, namespace = parse_ref(args.ref, args.namespace)
    resource = project.backend.find(kind, name, namespace)
    if args.json:
        payload = resource if args.reveal else redact_resource(kind, resource)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if kind == "secret" and not args.reveal:
            print("提示: Secret 的数据值已打码。加 --reveal 显示实际值。", file=sys.stderr)
        return 0
    if args.yaml:
        print(project.backend.yaml(kind, resource).rstrip())
        return 0
    print_summary(project, kind, resource)
    return 0


def cmd_apply(args, project):
    path = Path(args.file).expanduser()
    if not path.is_file():
        raise RancherError(f"文件不存在：{path}")
    text = path.read_text("utf-8")
    if not text.strip():
        raise RancherError(f"文件为空：{path}")
    plan = project.backend.apply_plan(text)
    print(f"项目: {project.display}")
    print(f"文件: {path}（{len(text.splitlines())} 行，{len(plan)} 个文档）")
    for method, url, label, *_ in plan:
        print(f"  {method} {url}  [{label}]")
    if args.dry_run:
        print("未提交（--dry-run）。")
        return 0
    project.backend.apply(text)
    print("已提交清单。")
    print("下一步：用 ls 和 get 确认结果。")
    return 0


def cmd_delete(args, project):
    kind, name, namespace = parse_ref(args.ref, args.namespace)
    resource = project.backend.find(kind, name, namespace)
    method, link = project.backend.delete_url(kind, resource)
    where = resource.get("namespaceId") or namespace or "-"
    if args.dry_run:
        print(f"将调用: {method} {link}")
        print(f"目标: {KINDS[kind]['label']}「{name}」（命名空间 {where}）")
        print("未提交（--dry-run）。")
        return 0
    project.backend.delete(kind, resource)
    print(f"已删除 {KINDS[kind]['label']}「{name}」（命名空间 {where}）")
    return 0


def cmd_redeploy(args, project):
    kind, name, namespace = parse_ref(args.ref, args.namespace)
    resource = project.backend.find(kind, name, namespace)
    method, action = project.backend.redeploy_action(kind, resource)
    where = resource.get("namespaceId") or namespace or "-"
    if args.dry_run:
        print(f"将调用: {method} {action}")
        print(f"目标: {KINDS[kind]['label']}「{name}」（命名空间 {where}）")
        print("未提交（--dry-run）。")
        return 0
    project.backend.redeploy(kind, resource)
    print(f"已提交重新部署：{KINDS[kind]['label']}「{name}」（命名空间 {where}）")
    print("下一步：用 get 查看状态。")
    return 0


# ────────────────────────────────────────────────────────────
# 命令行入口
# ────────────────────────────────────────────────────────────


def add_common_arguments(parser, project=True, include_json=True):
    if project:
        parser.add_argument(
            "--project",
            help="项目地址或项目 ID（c-xxx:p-yyy）。省略时使用项目目录记录的项目。",
        )
        parser.add_argument("--projects-file", help="项目记忆文件路径（默认 .rancher-projects）")
        parser.add_argument("--namespace", help="命名空间")
    parser.add_argument("--server", help="Rancher 配置名称（省略时使用默认配置）")
    parser.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验")
    parser.add_argument(
        "--http-timeout",
        type=float,
        default=DEFAULT_HTTP_TIMEOUT,
        help=f"HTTP 超时秒数（默认 {int(DEFAULT_HTTP_TIMEOUT)}）",
    )
    if include_json:
        parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--verbose", action="store_true", help="打印 HTTP 请求")
    parser.add_argument("--no-save", action="store_true", help="不记录项目地址")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="rc2.py",
        description="rc2-deploy: Rancher 2.x 部署助手",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--version", action="version", version=f"rc2-deploy {VERSION}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    p_config = subparsers.add_parser(
        "config",
        help="管理 Rancher 连接配置（由用户本人在终端交互执行）",
        description="管理连接配置：add / list / show / use / remove",
    )
    p_config.add_argument(
        "action",
        nargs="?",
        default="list",
        choices=["add", "set", "list", "show", "use", "remove"],
        help="操作类型（默认 list）",
    )
    p_config.add_argument("name", nargs="?", help="配置名称")
    p_config.add_argument("--json", action="store_true", help="输出 JSON（仅 list）")
    p_config.set_defaults(func=cmd_config)

    p_saved = subparsers.add_parser(
        "saved",
        aliases=["remembered"],
        help="列出项目目录中记录的项目连接",
        description="列出 .rancher-projects 中记录的项目连接",
    )
    p_saved.add_argument("--projects-file", help="项目记忆文件路径")
    p_saved.add_argument("--json", action="store_true", help="输出 JSON")
    p_saved.set_defaults(func=cmd_saved)

    p_remember = subparsers.add_parser(
        "remember",
        help="记录项目名称，便于用 --project <名称> 选择",
        description="记录项目名称到 .rancher-projects",
    )
    add_common_arguments(p_remember)
    p_remember.add_argument("name", help="项目名称，例如 infras")
    p_remember.set_defaults(func=cmd_remember)

    p_clusters = subparsers.add_parser(
        "clusters", help="列出集群", description="列出集群"
    )
    add_common_arguments(p_clusters, project=False)
    p_clusters.set_defaults(func=cmd_clusters)

    p_projects = subparsers.add_parser(
        "projects", help="列出项目", description="列出项目"
    )
    add_common_arguments(p_projects, project=False)
    p_projects.add_argument("--cluster", help="只列出指定集群的项目")
    p_projects.set_defaults(func=cmd_projects)

    p_namespaces = subparsers.add_parser(
        "namespaces", help="列出项目下的命名空间", description="列出项目下的命名空间"
    )
    add_common_arguments(p_namespaces)
    p_namespaces.set_defaults(func=cmd_namespaces)

    p_ls = subparsers.add_parser(
        "ls",
        aliases=["list"],
        help="列出资源",
        description="列出资源：deployment/statefulset/daemonset/job/configmap/secret/ingress/service/pvc",
    )
    add_common_arguments(p_ls)
    p_ls.add_argument("kind", help="资源类型，例如 deployments、cm、svc、pvc")
    p_ls.set_defaults(func=cmd_ls)

    p_get = subparsers.add_parser(
        "get", help="查询一个资源", description="查询一个资源（--json / --yaml）"
    )
    add_common_arguments(p_get)
    p_get.add_argument("ref", help="<类型>/<名称>，例如 deployment/api-server")
    p_get.add_argument("--yaml", action="store_true", help="输出 YAML 清单")
    p_get.add_argument("--reveal", action="store_true", help="JSON 中显示 Secret 的实际值")
    p_get.set_defaults(func=cmd_get)

    p_apply = subparsers.add_parser(
        "apply",
        aliases=["create", "edit"],
        help="用 YAML 清单创建或更新资源",
        description="用 YAML 清单创建或更新资源（项目 importYaml）",
    )
    add_common_arguments(p_apply)
    p_apply.add_argument("--file", required=True, help="YAML 或 JSON 清单文件")
    p_apply.add_argument("--dry-run", action="store_true", help="只显示请求，不提交")
    p_apply.set_defaults(func=cmd_apply)

    p_delete = subparsers.add_parser(
        "delete", help="删除一个资源", description="删除一个资源"
    )
    add_common_arguments(p_delete)
    p_delete.add_argument("ref", help="<类型>/<名称>，例如 configmap/app-config")
    p_delete.add_argument("--dry-run", action="store_true", help="只显示请求，不删除")
    p_delete.set_defaults(func=cmd_delete)

    p_redeploy = subparsers.add_parser(
        "redeploy",
        help="重新部署工作负载或 Job",
        description="重新部署 deployment/statefulset/daemonset/job",
    )
    add_common_arguments(p_redeploy)
    p_redeploy.add_argument("ref", help="<类型>/<名称>，例如 deployment/api-server")
    p_redeploy.add_argument("--dry-run", action="store_true", help="只显示请求，不部署")
    p_redeploy.set_defaults(func=cmd_redeploy)

    return parser


def project_record_name(project):
    """新记录项目时使用的名称。名称等于项目 ID 时不记录。"""
    name = getattr(project, "name", None)
    if not name or name == getattr(project, "id", None):
        return None
    return name


def dispatch(args):
    if args.command == "config":
        return cmd_config(args)
    if args.command in ("saved", "remembered"):
        return cmd_saved(args)
    if args.command == "remember":
        return cmd_remember(args)
    if args.command == "clusters":
        _, config = resolve_server(args)
        client = build_client(config, args)
        cluster = scope_cluster(config)
        if cluster:
            return cmd_clusters_scoped(args, client, cluster)
        return cmd_clusters(args, client)
    if args.command == "projects":
        _, config = resolve_server(args)
        if scope_cluster(config):
            raise RancherError(
                "集群级 scope token 不能列项目（需要 /v3 API）。\n"
                "请从 Rancher UI 复制项目地址，例如 https://rancher.example.com/p/c-xxx:p-yyy/，\n"
                "然后用 --project 指定。"
            )
        client = build_client(config, args)
        return cmd_projects(args, client)

    _, config, project_url = resolve_project_target(args)
    client = build_client(config, args)
    cluster = scope_cluster(config)
    if cluster:
        project = ScopedProject(client, project_url, cluster)
    else:
        project = fetch_project(client, project_url)
    code = args.func(args, project)
    if code == 0 and not getattr(args, "no_save", False) and not getattr(
        args, "dry_run", False
    ):
        record_project_url(
            find_projects_file(getattr(args, "projects_file", None)),
            project_url,
            project_record_name(project),
        )
    return code


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    try:
        return dispatch(args)
    except RancherError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())