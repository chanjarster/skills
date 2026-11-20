"""rc2-deploy 单元测试 + mock Rancher 端到端测试。

运行:
    cd skills/rc2-deploy
    pytest tests/test_rc2.py -v
"""

import base64
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import rc2  # noqa: E402

BASE_URL = ""
SCOPED_BASE_URL = ""
ACCESS_KEY = "token-demo"
SECRET_KEY = "secret-demo"
AUTH = "Basic " + base64.b64encode(f"{ACCESS_KEY}:{SECRET_KEY}".encode()).decode()
SCOPED_ACCESS_KEY = "token-scoped"
SCOPED_SECRET_KEY = "secret-scoped"
SCOPED_AUTH = "Basic " + base64.b64encode(
    f"{SCOPED_ACCESS_KEY}:{SCOPED_SECRET_KEY}".encode()
).decode()
SCOPED_CLUSTER = "c-wq9nv"
SCOPED_PROJECT_ID = "c-wq9nv:p-29k84"
STATE = {
    "applied": None,
    "deleted": [],
    "redeployed": [],
    "k8s_applied": [],
    "k8s_redeployed": [],
    "k8s_deleted": [],
}
NOW_MS = 1700000000000


# ────────────────────────────────────────────────────────────
# mock Rancher 数据
# ────────────────────────────────────────────────────────────


def collection_url(pid, collection):
    return f"{BASE_URL}/v3/projects/{pid}/{collection}"


def resource(pid, collection, rid, **fields):
    path = f"{collection_url(pid, collection)}/{rid}"
    payload = {
        "id": rid,
        "name": rid.split(":")[-1],
        "namespaceId": rid.split(":")[1] if rid.count(":") >= 2 else None,
        "state": "active",
        "createdTS": NOW_MS,
        "links": {"self": path, "remove": path, "yaml": path + "/yaml"},
        "actions": {
            "redeploy": path + "?action=redeploy",
            "pause": path + "?action=pause",
        },
    }
    payload.update(fields)
    return payload


def deployment(pid, namespace, name, scale, available, image, with_actions=True):
    item = resource(
        pid,
        "deployments",
        f"deployment:{namespace}:{name}",
        type="deployment",
        scale=scale,
        availableReplicas=available,
        containers=[{"name": name, "image": image}],
    )
    if not with_actions:
        item["actions"] = {}
    return item


def deployments(pid):
    if pid != "c-demo:p-web":
        return []
    return [
        deployment(pid, "default", "api", 3, 2, "registry.local/api:1.0"),
        deployment(pid, "prod", "api", 1, 1, "registry.local/api:1.0"),
        deployment(pid, "prod", "worker", 2, 2, "registry.local/worker:2.1"),
    ]


def statefulsets(pid):
    if pid != "c-demo:p-web":
        return []
    return [
        resource(
            pid,
            "statefulSets",
            "statefulset:prod:db",
            type="statefulset",
            scale=1,
            availableReplicas=1,
            containers=[{"name": "db", "image": "postgres:16"}],
        )
    ]


def workloads(pid):
    if pid != "c-demo:p-web":
        return []
    return [
        resource(
            pid,
            "workloads",
            "daemonset:prod:agent",
            type="daemonset",
            desired=2,
            ready=2,
            containers=[{"name": "agent", "image": "registry.local/agent:3.0"}],
            links={
                "self": f"{BASE_URL}/v3/project/{pid}/workloads/daemonset:prod:agent",
                "remove": f"{BASE_URL}/v3/project/{pid}/workloads/daemonset:prod:agent",
                "yaml": f"{BASE_URL}/v3/project/{pid}/workloads/daemonset:prod:agent/yaml",
            },
        ),
        {
            "type": "deployment",
            "id": "deployment:prod:extra",
            "name": "extra",
            "namespaceId": "prod",
        },
    ]


def jobs(pid):
    if pid != "c-demo:p-web":
        return []
    item = resource(
        pid,
        "jobs",
        "job:prod:nightly",
        type="job",
        completions=1,
        succeeded=1,
    )
    item["actions"] = {}
    return [item]


def configmaps(pid):
    if pid != "c-demo:p-web":
        return []
    return [
        resource(
            pid,
            "configmaps",
            "configmap:default:app-config",
            type="configMap",
            data={"log.level": "info", "app.yaml": "server:\n  port: 8080\n"},
        )
    ]


def secrets(pid):
    if pid != "c-demo:p-web":
        return []
    return [
        resource(
            pid,
            "secrets",
            "secret:prod:app-secret",
            type="secret",
            _type="Opaque",
            data={"password": "s3cr3t-value", "token": "abc123"},
        )
    ]


def ingresses(pid):
    if pid != "c-demo:p-web":
        return []
    return [
        resource(
            pid,
            "ingresses",
            "ingress:prod:web",
            type="ingress",
            rules=[{"host": "web.example.com"}],
        )
    ]


def services(pid):
    if pid != "c-demo:p-web":
        return []
    return [
        resource(
            pid,
            "services",
            "service:prod:web",
            type="service",
            clusterIp="10.43.0.10",
            ports=[{"port": 80, "protocol": "TCP"}],
        )
    ]


def pvcs(pid):
    if pid != "c-demo:p-web":
        return []
    return [
        resource(
            pid,
            "persistentvolumeclaims",
            "persistentvolumeclaim:prod:data",
            type="persistentVolumeClaim",
            resources={"requests": {"storage": "1Gi"}},
            accessModes=["ReadWriteOnce"],
        )
    ]


def namespaces(pid):
    if pid != "c-demo:p-web":
        return []
    return [
        {
            "id": "default",
            "name": "default",
            "namespaceId": None,
            "projectId": pid,
            "state": "active",
            "createdTS": NOW_MS,
        },
        {
            "id": "prod",
            "name": "prod",
            "namespaceId": None,
            "projectId": pid,
            "state": "active",
            "createdTS": NOW_MS,
        },
    ]


def project_resource(pid, name):
    actions = {}
    if pid == "c-demo:p-web":
        actions["importYaml"] = f"{BASE_URL}/v3/projects/{pid}?action=importYaml"
    return {
        "type": "project",
        "id": pid,
        "name": name,
        "clusterId": pid.split(":")[0],
        "state": "active",
        "createdTS": NOW_MS,
        "links": {"self": f"{BASE_URL}/v3/projects/{pid}"},
        "actions": actions,
    }


COLLECTIONS = {
    "deployments": deployments,
    "statefulsets": statefulsets,
    "jobs": jobs,
    "configmaps": configmaps,
    "secrets": secrets,
    "ingresses": ingresses,
    "services": services,
    "persistentvolumeclaims": pvcs,
    "namespaces": namespaces,
}


def find_resource(path):
    for pid in ("c-demo:p-web",):
        for collection, builder in COLLECTIONS.items():
            for item in builder(pid):
                if (item.get("links") or {}).get("self") == path:
                    return item
    return None


# ────────────────────────────────────────────────────────────
# mock HTTP server
# ────────────────────────────────────────────────────────────


K8S_ROOT = "/k8s/clusters/c-wq9nv"


def k8s_meta(name, namespace=None, annotations=None):
    meta = {"name": name, "creationTimestamp": "2026-11-01T00:00:00Z"}
    if namespace:
        meta["namespace"] = namespace
    if annotations:
        meta["annotations"] = annotations
    return meta


def k8s_namespaces():
    return [
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": k8s_meta(
                name, annotations={"field.cattle.io/projectId": SCOPED_PROJECT_ID}
            ),
            "status": {"phase": "Active"},
        }
        for name in ("default", "prod")
    ] + [
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": k8s_meta(
                "ops", annotations={"field.cattle.io/projectId": "c-wq9nv:p-other"}
            ),
            "status": {"phase": "Active"},
        }
    ]


def k8s_deployments():
    return [
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": k8s_meta("api", "default"),
            "spec": {
                "replicas": 3,
                "template": {
                    "spec": {
                        "containers": [
                            {"name": "api", "image": "registry.local/api:1.0"}
                        ]
                    }
                },
            },
            "status": {"availableReplicas": 2, "readyReplicas": 2, "replicas": 3},
        },
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": k8s_meta("worker", "prod"),
            "spec": {
                "replicas": 2,
                "template": {
                    "spec": {
                        "containers": [
                            {"name": "worker", "image": "registry.local/worker:2.1"}
                        ]
                    }
                },
            },
            "status": {"availableReplicas": 2, "readyReplicas": 2, "replicas": 2},
        },
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": k8s_meta("billing", "ops"),
            "spec": {
                "replicas": 1,
                "template": {
                    "spec": {
                        "containers": [
                            {"name": "billing", "image": "registry.local/billing:1.0"}
                        ]
                    }
                },
            },
            "status": {"availableReplicas": 1, "readyReplicas": 1, "replicas": 1},
        },
    ]


def k8s_statefulsets():
    return [
        {
            "apiVersion": "apps/v1",
            "kind": "StatefulSet",
            "metadata": k8s_meta("db", "prod"),
            "spec": {
                "replicas": 1,
                "template": {
                    "spec": {"containers": [{"name": "db", "image": "postgres:16"}]}
                },
            },
            "status": {"availableReplicas": 1, "readyReplicas": 1},
        }
    ]


def k8s_daemonsets():
    return [
        {
            "apiVersion": "apps/v1",
            "kind": "DaemonSet",
            "metadata": k8s_meta("agent", "prod"),
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {"name": "agent", "image": "registry.local/agent:3.0"}
                        ]
                    }
                }
            },
            "status": {"desiredNumberScheduled": 2, "numberReady": 2},
        }
    ]


def k8s_jobs():
    return [
        {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": k8s_meta("nightly", "prod"),
            "spec": {
                "completions": 1,
                "template": {
                    "spec": {
                        "containers": [
                            {"name": "nightly", "image": "registry.local/job:1.0"}
                        ]
                    }
                },
            },
            "status": {"succeeded": 1},
        }
    ]


def k8s_configmaps():
    return [
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": k8s_meta("app-config", "default"),
            "data": {"log.level": "info"},
        }
    ]


def k8s_secrets():
    return [
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": k8s_meta("app-secret", "prod"),
            "type": "Opaque",
            "data": {"password": base64.b64encode(b"s3cr3t").decode()},
        }
    ]


def k8s_ingresses():
    return [
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": k8s_meta("web", "prod"),
            "spec": {"rules": [{"host": "web.example.com"}]},
        }
    ]


def k8s_services():
    return [
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": k8s_meta("web", "prod"),
            "spec": {
                "clusterIP": "10.43.0.10",
                "ports": [{"port": 80, "protocol": "TCP"}],
            },
        }
    ]


def k8s_pvcs():
    return [
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": k8s_meta("data", "prod"),
            "spec": {"accessModes": ["ReadWriteOnce"], "volumeName": "pv-1"},
            "status": {"phase": "Bound", "capacity": {"storage": "1Gi"}},
        }
    ]


K8S_COLLECTIONS = {
    "deployments": k8s_deployments,
    "statefulsets": k8s_statefulsets,
    "daemonsets": k8s_daemonsets,
    "jobs": k8s_jobs,
    "configmaps": k8s_configmaps,
    "secrets": k8s_secrets,
    "ingresses": k8s_ingresses,
    "services": k8s_services,
    "persistentvolumeclaims": k8s_pvcs,
}


def k8s_parse_tail(tail):
    text = tail.strip("/")
    if text.startswith("api/v1/"):
        rest = text[len("api/v1/") :]
    elif text.startswith("apis/"):
        rest = text[len("apis/") :]
        group, _, rest = rest.partition("/")
        version, _, rest = rest.partition("/")
    else:
        return None
    segments = [segment for segment in rest.split("/") if segment]
    namespace = None
    if segments and segments[0] == "namespaces":
        if len(segments) < 2:
            return None
        namespace = segments[1]
        segments = segments[2:]
    if not segments:
        return None
    plural = segments[0]
    name = segments[1] if len(segments) > 1 else None
    return plural, namespace, name


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, payload=None, content_type="application/json"):
        if payload is None:
            body = b""
        elif isinstance(payload, str):
            body = payload.encode("utf-8")
        else:
            body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self, allowed=None):
        headers = {AUTH} if allowed is None else allowed
        if self.headers.get("Authorization") in headers:
            return True
        self._send(401, {"type": "error", "status": "401", "message": "must authenticate"})
        return False

    def do_GET(self):
        parts = urlsplit(self.path)
        path = parts.path.rstrip("/")
        query = parse_qs(parts.query)
        if path.startswith(K8S_ROOT):
            if not self._authorized({SCOPED_AUTH}):
                return
            self._k8s_get(path)
            return
        if not self._authorized():
            return

        if path == "/v3/clusters":
            if query.get("marker") == ["2"]:
                payload = {
                    "data": [
                        {
                            "id": "c-ops",
                            "name": "ops",
                            "state": "active",
                            "version": {"gitVersion": "v1.27.3"},
                            "createdTS": NOW_MS,
                        }
                    ],
                    "pagination": {},
                }
            else:
                payload = {
                    "data": [
                        {
                            "id": "c-demo",
                            "name": "demo",
                            "state": "active",
                            "version": {"gitVersion": "v1.28.5"},
                            "createdTS": NOW_MS,
                        }
                    ],
                    "pagination": {
                        "next": f"{BASE_URL}/v3/clusters?limit=200&marker=2"
                    },
                }
            self._send(200, payload)
            return

        if path == "/v3/clusters/c-demo":
            self._send(
                200,
                {
                    "id": "c-demo",
                    "name": "demo",
                    "type": "cluster",
                    "links": {"self": f"{BASE_URL}/v3/clusters/c-demo"},
                    "actions": {
                        "importYaml": f"{BASE_URL}/v3/clusters/c-demo?action=importYaml"
                    },
                },
            )
            return

        if path == "/v3/projects":
            data = [
                project_resource("c-demo:p-web", "web"),
                project_resource("c-demo:p-data", "data"),
            ]
            if query.get("clusterId") == ["c-ops"]:
                data = []
            self._send(200, {"data": data, "pagination": {}})
            return

        if path.startswith("/v3/projects/"):
            rest = path[len("/v3/projects/") :]
            if "/" in rest:
                pid, tail = rest.split("/", 1)
            else:
                pid, tail = rest, ""
            if pid not in ("c-demo:p-web", "c-demo:p-data"):
                self._send(404, {"type": "error", "status": "404", "message": "not found"})
                return
            if not tail:
                name = "web" if pid.endswith("p-web") else "data"
                self._send(200, project_resource(pid, name))
                return
            if tail in COLLECTIONS:
                self._send(
                    200, {"data": COLLECTIONS[tail](pid), "pagination": {}}
                )
                return
            if tail.endswith("/yaml"):
                self._send(
                    200,
                    "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: api\n",
                    content_type="text/yaml",
                )
                return
            self._send(404, {"type": "error", "status": "404", "message": "not found"})
            return

        # 兼容 Rancher 2.4 的 /v3/project/<id>/workloads 集合
        if path.startswith("/v3/project/"):
            rest = path[len("/v3/project/") :]
            pid, tail = rest.split("/", 1)
            if tail == "workloads" and pid == "c-demo:p-web":
                self._send(200, {"data": workloads(pid), "pagination": {}})
                return
            self._send(404, {"type": "error", "status": "404", "message": "not found"})
            return

        self._send(404, {"type": "error", "status": "404", "message": "not found"})

    def _k8s_get(self, path):
        tail = path[len(K8S_ROOT) :]
        if tail in ("", "/version"):
            self._send(200, {"gitVersion": "v1.28.5"})
            return
        if tail == "/api/v1/namespaces":
            self._send(200, {"items": k8s_namespaces()})
            return
        parsed = k8s_parse_tail(tail)
        if not parsed:
            self._send(404, {"type": "error", "status": "404", "message": "not found"})
            return
        plural, namespace, name = parsed
        builder = K8S_COLLECTIONS.get(plural)
        if not builder:
            self._send(404, {"type": "error", "status": "404", "message": "not found"})
            return
        items = [
            item
            for item in builder()
            if namespace is None
            or (item.get("metadata") or {}).get("namespace") == namespace
        ]
        if name:
            for item in items:
                if (item.get("metadata") or {}).get("name") == name:
                    self._send(200, item)
                    return
            self._send(404, {"type": "error", "status": "404", "message": "not found"})
            return
        self._send(200, {"items": items})

    def do_POST(self):
        if not self._authorized():
            return
        parts = urlsplit(self.path)
        path = parts.path.rstrip("/")
        query = parse_qs(parts.query)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else ""

        if query.get("action") == ["importYaml"]:
            STATE["applied"] = {"path": path, "body": body}
            self._send(200, {})
            return

        if query.get("action") == ["redeploy"]:
            STATE["redeployed"].append(path)
            self._send(200, {})
            return

        self._send(404, {"type": "error", "status": "404", "message": "not found"})

    def do_DELETE(self):
        path = urlsplit(self.path).path.rstrip("/")
        if path.startswith(K8S_ROOT):
            if not self._authorized({SCOPED_AUTH}):
                return
            STATE["k8s_deleted"].append(path)
            self._send(200, {})
            return
        if not self._authorized():
            return
        STATE["deleted"].append(path)
        self._send(200, {})

    def do_PATCH(self):
        path = urlsplit(self.path).path.rstrip("/")
        if not path.startswith(K8S_ROOT):
            self._send(404, {"type": "error", "status": "404", "message": "not found"})
            return
        if not self._authorized({SCOPED_AUTH}):
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else ""
        record = {
            "path": path,
            "body": body,
            "content_type": self.headers.get("Content-Type") or "",
        }
        if "apply-patch" in record["content_type"]:
            STATE["k8s_applied"].append(record)
        else:
            STATE["k8s_redeployed"].append(record)
        self._send(200, {})


@pytest.fixture(scope="module", autouse=True)
def server():
    global BASE_URL, SCOPED_BASE_URL
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    BASE_URL = f"http://127.0.0.1:{httpd.server_address[1]}"
    SCOPED_BASE_URL = f"http://localhost:{httpd.server_address[1]}"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield
    httpd.shutdown()


# ────────────────────────────────────────────────────────────
# 测试夹具
# ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(rc2, "CONFIG_DIR", tmp_path / ".rc2-deploy")
    monkeypatch.setattr(rc2, "SERVERS_DIR", tmp_path / ".rc2-deploy" / "servers")
    monkeypatch.setattr(rc2, "SETTINGS_PATH", tmp_path / ".rc2-deploy" / "settings.json")
    monkeypatch.chdir(tmp_path)
    rc2.load_server_configs.cache_clear()
    rc2.load_settings.cache_clear()
    rc2.save_server_config("demo", f"{BASE_URL}/v3", ACCESS_KEY, SECRET_KEY)
    rc2.save_server_config(
        "scoped",
        f"{SCOPED_BASE_URL}/v3",
        SCOPED_ACCESS_KEY,
        SCOPED_SECRET_KEY,
        f"cluster:{SCOPED_CLUSTER}",
    )
    rc2.save_settings({"default_server": "demo"})
    STATE["applied"] = None
    STATE["deleted"].clear()
    STATE["redeployed"].clear()
    STATE["k8s_applied"].clear()
    STATE["k8s_redeployed"].clear()
    STATE["k8s_deleted"].clear()
    yield
    rc2.load_server_configs.cache_clear()
    rc2.load_settings.cache_clear()


PROJECT_URL = None


def project_url():
    return f"{BASE_URL}/v3/projects/c-demo:p-web"


def run(*argv):
    return rc2.main(list(argv))


def run_ls(*extra):
    return run("ls", *extra, "--project", project_url())


def scoped_project_url():
    return f"{SCOPED_BASE_URL}/v3/projects/{SCOPED_PROJECT_ID}"


def run_scoped(*argv):
    return run(*argv, "--server", "scoped", "--project", scoped_project_url())


# ────────────────────────────────────────────────────────────
# 纯函数
# ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("cluster:c-abc123", ("c-abc123", None)),
        ("c-abc123", ("c-abc123", None)),
        ("c-abc123:p-def456", ("c-abc123", "c-abc123:p-def456")),
        (
            "https://rancher.example.com/dashboard/c/c-abc123/",
            ("c-abc123", None),
        ),
        (
            "https://rancher.example.com/c/c-abc123/workloads",
            ("c-abc123", None),
        ),
        (
            "https://rancher.example.com/p/c-abc123:p-def456/",
            ("c-abc123", "c-abc123:p-def456"),
        ),
        (
            "https://rancher.example.com/v3/projects/c-abc123:p-def456",
            ("c-abc123", "c-abc123:p-def456"),
        ),
        (
            "https://rancher.example.com/v3/clusters/c-abc123",
            ("c-abc123", None),
        ),
        (
            "https://rancher.example.com/k8s/clusters/c-abc123/api/v1/namespaces",
            ("c-abc123", None),
        ),
        ("", (None, None)),
        ("no ids here", (None, None)),
    ],
)
def test_extract_references(raw, expected):
    assert rc2.extract_references(raw) == expected


def test_normalize_scope_accepts_urls():
    assert rc2.normalize_scope("c-abc123") == "cluster:c-abc123"
    assert (
        rc2.normalize_scope("https://rancher.example.com/p/c-abc123:p-def456/")
        == "cluster:c-abc123"
    )
    assert (
        rc2.normalize_scope("https://rancher.example.com/dashboard/c/c-abc123/")
        == "cluster:c-abc123"
    )
    with pytest.raises(rc2.RancherError):
        rc2.normalize_scope("abc")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://rancher.example.com/v3", "https://rancher.example.com/v3"),
        ("https://rancher.example.com/v3/", "https://rancher.example.com/v3"),
        ("https://rancher.example.com", "https://rancher.example.com/v3"),
        ("https://rancher.example.com/", "https://rancher.example.com/v3"),
        ("http://10.0.0.1:8080/v3", "http://10.0.0.1:8080/v3"),
        ("https://rancher.example.com/apikeys", "https://rancher.example.com/v3"),
        ("https://rancher.example.com/apikeys/", "https://rancher.example.com/v3"),
    ],
)
def test_normalize_server_url(raw, expected):
    assert rc2.normalize_server_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://rancher.example.com/v3/projects/c-demo:p-web",
        "https://rancher.example.com/v3/project/c-demo:p-web/workloads",
        "rancher.example.com",
        "",
    ],
)
def test_normalize_server_url_rejects(raw):
    with pytest.raises(rc2.RancherError):
        rc2.normalize_server_url(raw)


def test_normalize_project_url():
    assert (
        rc2.normalize_project_url(f"{BASE_URL}/v3/projects/c-demo:p-web/")
        == f"{BASE_URL}/v3/projects/c-demo:p-web"
    )
    assert rc2.project_id_of(f"{BASE_URL}/v3/projects/c-demo:p-web") == "c-demo:p-web"


def test_normalize_project_url_rejects():
    with pytest.raises(rc2.RancherError):
        rc2.normalize_project_url("https://rancher.example.com/v3/clusters/c-demo")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("deployment/api", ("deployment", "api", None)),
        ("deployment/api", ("deployment", "api", None)),
        ("deployments/api", ("deployment", "api", None)),
        ("cm/app-config", ("configmap", "app-config", None)),
        ("svc/web", ("service", "web", None)),
        ("deployment:prod:api", ("deployment", "api", "prod")),
        ("pvc/data", ("pvc", "data", None)),
    ],
)
def test_parse_ref(raw, expected):
    assert rc2.parse_ref(raw) == expected


def test_parse_ref_with_namespace_default():
    assert rc2.parse_ref("sts/db", "prod") == ("statefulset", "db", "prod")


@pytest.mark.parametrize("raw", ["", "api", "deployment/"])
def test_parse_ref_rejects(raw):
    with pytest.raises(rc2.RancherError):
        rc2.parse_ref(raw)


def test_normalize_kind_unknown():
    with pytest.raises(rc2.RancherError):
        rc2.normalize_kind("cronjob")


def test_apikeys_url():
    assert rc2.apikeys_url("https://rancher.example.com/v3") == (
        "https://rancher.example.com/apikeys"
    )
    assert rc2.apikeys_url("http://10.0.0.1:8080/v3") == "http://10.0.0.1:8080/apikeys"
    assert (
        rc2.apikeys_url("https://rancher.example.com/k8s/clusters/c-wq9nv")
        == "https://rancher.example.com/apikeys"
    )


def test_mask_secret():
    assert rc2.mask_secret("token-abcdefgh") == "toke******gh"
    assert rc2.mask_secret(None) == "-"


def test_fmt_age():
    now = time.time() * 1000
    assert rc2.fmt_age(now - 30_000) == "30s"
    assert rc2.fmt_age(now - 600_000) == "10m"
    assert rc2.fmt_age(now - 10_800_000) == "3h"
    assert rc2.fmt_age(now - 172_800_000) == "2d"


# ────────────────────────────────────────────────────────────
# 配置
# ────────────────────────────────────────────────────────────


def test_config_add_writes_private_file(tmp_path, monkeypatch, capsys):
    answers = ["prod", f"{BASE_URL}/v3", "token-prod", "cluster:c-abc123"]
    monkeypatch.setattr("builtins.input", lambda prompt="": answers.pop(0))
    monkeypatch.setattr(rc2.getpass, "getpass", lambda prompt="": "secret-prod")
    assert run("config", "add") == 0
    path = tmp_path / ".rc2-deploy" / "servers" / "prod.json"
    assert path.exists()
    assert oct(path.stat().st_mode)[-3:] == "600"
    payload = json.loads(path.read_text("utf-8"))
    assert payload == {
        "url": f"{BASE_URL}/v3",
        "access_key": "token-prod",
        "secret_key": "secret-prod",
        "scope": "cluster:c-abc123",
    }
    output = capsys.readouterr().out
    assert "secret-prod" not in output
    assert "cluster:c-abc123" in output
    assert f"{BASE_URL}/apikeys" in output


def test_config_add_scope_accepts_project_url(tmp_path, monkeypatch, capsys):
    answers = [
        "prod",
        f"{BASE_URL}/v3",
        "token-prod",
        f"{BASE_URL}/p/c-demo:p-web/",
        "id-gov",
    ]
    monkeypatch.setattr("builtins.input", lambda prompt="": answers.pop(0))
    monkeypatch.setattr(rc2.getpass, "getpass", lambda prompt="": "secret-prod")
    assert run("config", "add") == 0
    payload = json.loads(
        (tmp_path / ".rc2-deploy" / "servers" / "prod.json").read_text("utf-8")
    )
    assert payload["scope"] == "cluster:c-demo"
    projects = (tmp_path / ".rancher-projects").read_text("utf-8")
    assert f"{BASE_URL}/v3/projects/c-demo:p-web" in projects
    assert "id-gov" in projects
    output = capsys.readouterr().out
    assert "已记录项目地址" in output
    assert "已记录项目名称" in output
    assert "secret-prod" not in output


def test_config_add_scope_accepts_cluster_url(tmp_path, monkeypatch, capsys):
    answers = [
        "prod",
        f"{BASE_URL}/v3",
        "token-prod",
        f"{BASE_URL}/dashboard/c/c-ops/",
    ]
    monkeypatch.setattr("builtins.input", lambda prompt="": answers.pop(0))
    monkeypatch.setattr(rc2.getpass, "getpass", lambda prompt="": "secret-prod")
    assert run("config", "add") == 0
    payload = json.loads(
        (tmp_path / ".rc2-deploy" / "servers" / "prod.json").read_text("utf-8")
    )
    assert payload["scope"] == "cluster:c-ops"
    assert not (tmp_path / ".rancher-projects").exists()


def test_config_add_scope_rejects_unknown(monkeypatch, capsys):
    answers = ["prod", f"{BASE_URL}/v3", "token-prod", "abc"]
    monkeypatch.setattr("builtins.input", lambda prompt="": answers.pop(0))
    monkeypatch.setattr(rc2.getpass, "getpass", lambda prompt="": "secret-prod")
    assert run("config", "add") == 2
    assert "无法识别 scope" in capsys.readouterr().err


def test_config_add_rejects_project_url(monkeypatch):
    answers = ["prod", f"{BASE_URL}/v3/projects/c-demo:p-web", "token-prod"]
    monkeypatch.setattr("builtins.input", lambda prompt="": answers.pop(0))
    monkeypatch.setattr(rc2.getpass, "getpass", lambda prompt="": "secret-prod")
    assert run("config", "add") == 2


def test_config_list_masks_secret(capsys):
    assert run("config", "list") == 0
    output = capsys.readouterr().out
    assert "demo" in output
    assert SECRET_KEY not in output
    assert ACCESS_KEY not in output
    assert "toke" in output


def test_config_list_json(capsys):
    assert run("config", "list", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["name"] == "demo"
    assert payload[0]["default"] is True
    assert payload[0]["has_secret"] is True
    assert SECRET_KEY not in json.dumps(payload)


def test_config_show_and_use_and_remove(tmp_path, capsys):
    rc2.save_server_config("other", f"{BASE_URL}/v3", "token-other", "secret-other")
    assert run("config", "use", "other") == 0
    assert rc2.load_settings()["default_server"] == "other"
    assert run("config", "show") == 0
    output = capsys.readouterr().out
    assert f"{BASE_URL}/v3" in output
    assert "secret-other" not in output
    assert run("config", "remove", "other") == 0
    assert rc2.load_settings()["default_server"] == "demo"
    assert not (tmp_path / ".rc2-deploy" / "servers" / "other.json").exists()


def test_config_without_any_config(tmp_path, monkeypatch, capsys):
    for path in (tmp_path / ".rc2-deploy" / "servers").glob("*.json"):
        path.unlink()
    rc2.load_server_configs.cache_clear()
    assert run("clusters") == 2
    err = capsys.readouterr().err
    assert "config add" in err
    assert "/apikeys" in err


# ────────────────────────────────────────────────────────────
# 只读命令
# ────────────────────────────────────────────────────────────


def test_clusters_follows_pagination(capsys):
    assert run("clusters") == 0
    output = capsys.readouterr().out
    assert "c-demo" in output
    assert "c-ops" in output
    assert "v1.28.5" in output


def test_clusters_json(capsys):
    assert run("clusters", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["id"] for item in payload] == ["c-demo", "c-ops"]


def test_projects(capsys):
    assert run("projects") == 0
    output = capsys.readouterr().out
    assert "c-demo:p-web" in output
    assert "web" in output


def test_projects_filter_cluster(capsys):
    assert run("projects", "--cluster", "c-ops") == 0
    assert "没有可访问的项目" in capsys.readouterr().out


def test_namespaces(capsys):
    assert run("namespaces", "--project", project_url()) == 0
    output = capsys.readouterr().out
    assert "default" in output
    assert "prod" in output


def test_namespaces_uses_saved_project(capsys):
    assert run("namespaces", "--project", project_url()) == 0
    assert run("namespaces") == 0
    assert "prod" in capsys.readouterr().out


# ────────────────────────────────────────────────────────────
# ls
# ────────────────────────────────────────────────────────────


def test_ls_deployments(capsys):
    assert run_ls("deployments") == 0
    output = capsys.readouterr().out
    assert "api" in output
    assert "worker" in output
    assert "2/3" in output
    assert "3 个 Deployment" in output


def test_ls_deployments_namespace(capsys):
    assert run_ls("deployments", "--namespace", "prod") == 0
    output = capsys.readouterr().out
    assert "worker" in output
    assert "2/2" in output
    assert "3 个" not in output


def test_ls_deployments_json(capsys):
    assert run_ls("deployments", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "deployment"
    assert len(payload["items"]) == 3


def test_ls_statefulset_camelcase_fallback(capsys):
    assert run_ls("sts") == 0
    output = capsys.readouterr().out
    assert "db" in output
    assert "postgres:16" in output


def test_ls_daemonset_workloads_fallback(capsys):
    assert run_ls("ds") == 0
    output = capsys.readouterr().out
    assert "agent" in output
    assert "2/2" in output
    assert "extra" not in output


def test_ls_job(capsys):
    assert run_ls("jobs") == 0
    output = capsys.readouterr().out
    assert "nightly" in output


def test_ls_configmap(capsys):
    assert run_ls("cm") == 0
    output = capsys.readouterr().out
    assert "app-config" in output
    assert "2" in output


def test_ls_secret_hides_values(capsys):
    assert run_ls("secrets") == 0
    output = capsys.readouterr().out
    assert "app-secret" in output
    assert "Opaque" in output
    assert "s3cr3t-value" not in output


def test_ls_ingress(capsys):
    assert run_ls("ing") == 0
    assert "web.example.com" in capsys.readouterr().out


def test_ls_service(capsys):
    assert run_ls("svc") == 0
    output = capsys.readouterr().out
    assert "10.43.0.10" in output
    assert "80/TCP" in output


def test_ls_pvc(capsys):
    assert run_ls("pvc") == 0
    output = capsys.readouterr().out
    assert "1Gi" in output
    assert "ReadWriteOnce" in output


def test_ls_unknown_kind(capsys):
    assert run_ls("cronjob") == 2
    assert "不支持的类型" in capsys.readouterr().err


def test_ls_empty_namespace(capsys):
    assert run_ls("deployments", "--namespace", "empty") == 0
    assert "中没有 Deployment" in capsys.readouterr().out


# ────────────────────────────────────────────────────────────
# get
# ────────────────────────────────────────────────────────────


def test_get_summary(capsys):
    rc2.record_project_url(rc2.find_projects_file(), project_url())
    assert run("get", "deployment/api", "--namespace", "default") == 0
    output = capsys.readouterr().out
    assert "Deployment" in output
    assert "2/3" in output
    assert "registry.local/api:1.0" in output
    assert "redeploy" in output
    assert f"{BASE_URL}/v3/projects/c-demo:p-web/deployments/deployment:default:api" in output


def test_get_ambiguous_namespace(capsys):
    assert run("get", "deployment/api", "--project", project_url()) == 2
    assert "多个命名空间" in capsys.readouterr().err


def test_get_json(capsys):
    assert (
        run("get", "deployment/api", "--namespace", "default", "--project", project_url(), "--json")
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "api"
    assert payload["scale"] == 3


def test_get_yaml(capsys):
    assert (
        run("get", "deployment/api", "--namespace", "default", "--project", project_url(), "--yaml")
        == 0
    )
    assert "kind: Deployment" in capsys.readouterr().out


def test_get_secret_json_masks_values(capsys):
    assert run("get", "secret/app-secret", "--project", project_url(), "--json") == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["data"] == {"password": "***", "token": "***"}
    assert "s3cr3t-value" not in captured.out
    assert "--reveal" in captured.err


def test_get_secret_json_reveal(capsys):
    assert (
        run("get", "secret/app-secret", "--project", project_url(), "--json", "--reveal") == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["password"] == "s3cr3t-value"


def test_get_missing_resource(capsys):
    assert run("get", "deployment/missing", "--project", project_url()) == 2
    assert "找不到" in capsys.readouterr().err


# ────────────────────────────────────────────────────────────
# apply / delete / redeploy
# ────────────────────────────────────────────────────────────


def write_manifest(tmp_path, text="apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: demo\n"):
    path = tmp_path / "manifest.yaml"
    path.write_text(text, "utf-8")
    return path


def test_apply_dry_run(tmp_path, capsys):
    path = write_manifest(tmp_path)
    assert run("apply", "--file", str(path), "--project", project_url(), "--dry-run") == 0
    assert STATE["applied"] is None
    output = capsys.readouterr().out
    assert "importYaml" in output
    assert "未提交" in output


def test_apply_posts_yaml(tmp_path, capsys):
    path = write_manifest(tmp_path)
    assert run("apply", "--file", str(path), "--project", project_url()) == 0
    assert STATE["applied"]["path"] == "/v3/projects/c-demo:p-web"
    assert "kind: ConfigMap" in json.loads(STATE["applied"]["body"])["yaml"]
    assert "已提交清单" in capsys.readouterr().out


def test_apply_uses_cluster_action_when_project_has_none(tmp_path, capsys):
    path = write_manifest(tmp_path)
    data_project = f"{BASE_URL}/v3/projects/c-demo:p-data"
    assert run("apply", "--file", str(path), "--project", data_project) == 0
    assert STATE["applied"]["path"] == "/v3/clusters/c-demo"
    assert "已提交清单" in capsys.readouterr().out


def test_apply_missing_file(capsys):
    assert run("apply", "--file", "nope.yaml", "--project", project_url()) == 2
    assert "文件不存在" in capsys.readouterr().err


def test_apply_empty_file(tmp_path, capsys):
    path = tmp_path / "empty.yaml"
    path.write_text("  \n", "utf-8")
    assert run("apply", "--file", str(path), "--project", project_url()) == 2
    assert "文件为空" in capsys.readouterr().err


def test_delete_resource(capsys):
    assert run("delete", "deployment/worker", "--namespace", "prod", "--project", project_url()) == 0
    assert STATE["deleted"] == [
        "/v3/projects/c-demo:p-web/deployments/deployment:prod:worker"
    ]
    assert "已删除" in capsys.readouterr().out


def test_delete_dry_run(capsys):
    assert (
        run("delete", "configmap/app-config", "--project", project_url(), "--dry-run") == 0
    )
    assert STATE["deleted"] == []
    assert "未提交" in capsys.readouterr().out


def test_redeploy(capsys):
    assert run("redeploy", "deployment/api", "--namespace", "default", "--project", project_url()) == 0
    assert STATE["redeployed"] == [
        "/v3/projects/c-demo:p-web/deployments/deployment:default:api"
    ]
    assert "已提交重新部署" in capsys.readouterr().out


def test_redeploy_job_unsupported(capsys):
    assert run("redeploy", "job/nightly", "--project", project_url()) == 2
    assert "不支持 redeploy" in capsys.readouterr().err


def test_redeploy_dry_run(capsys):
    assert (
        run("redeploy", "deployment/api", "--namespace", "default",
            "--project", project_url(), "--dry-run") == 0
    )
    assert STATE["redeployed"] == []
    assert "未提交" in capsys.readouterr().out


# ────────────────────────────────────────────────────────────
# 项目记忆
# ────────────────────────────────────────────────────────────


def test_project_url_recorded_after_success(tmp_path):
    assert run_ls("deployments") == 0
    path = tmp_path / ".rancher-projects"
    assert path.exists()
    assert project_url() in path.read_text("utf-8")


def test_no_save_skips_record(tmp_path):
    assert run("ls", "deployments", "--project", project_url(), "--no-save") == 0
    assert not (tmp_path / ".rancher-projects").exists()


def test_record_project_name(tmp_path):
    path = tmp_path / ".rancher-projects"
    url = project_url()
    assert rc2.record_project_url(path, url) is True
    assert rc2.read_projects(path) == [{"url": url, "name": ""}]
    assert rc2.record_project_url(path, url, "id-gov") is True
    assert rc2.read_projects(path) == [{"url": url, "name": "id-gov"}]
    assert rc2.record_project_url(path, url) is False
    assert rc2.read_projects(path)[0]["name"] == "id-gov"


def test_read_projects_parses_names(tmp_path):
    path = tmp_path / ".rancher-projects"
    path.write_text(
        "# header\n"
        "https://rancher.example.com/v3/projects/c-a:p-b  id gov\n"
        "https://rancher.example.com/v3/projects/c-a:p-c\n",
        "utf-8",
    )
    assert rc2.read_projects(path) == [
        {"url": "https://rancher.example.com/v3/projects/c-a:p-b", "name": "id gov"},
        {"url": "https://rancher.example.com/v3/projects/c-a:p-c", "name": ""},
    ]


def test_project_name_recorded_from_api(tmp_path):
    assert run_ls("deployments") == 0
    entries = rc2.read_projects(tmp_path / ".rancher-projects")
    assert entries[0]["name"] == "web"


def test_scoped_project_recorded_without_name(tmp_path):
    assert run_scoped("ls", "deployments") == 0
    entries = rc2.read_projects(tmp_path / ".rancher-projects")
    assert entries[0]["name"] == ""


def test_project_selected_by_name(capsys):
    rc2.record_project_url(rc2.find_projects_file(), project_url(), "web")
    assert run("ls", "deployments", "--project", "WEB") == 0
    assert "api" in capsys.readouterr().out


def test_remember_command(tmp_path, capsys):
    assert (
        run(
            "remember",
            "infras",
            "--server",
            "scoped",
            "--project",
            f"{SCOPED_BASE_URL}/p/{SCOPED_PROJECT_ID}/",
        )
        == 0
    )
    entries = rc2.read_projects(tmp_path / ".rancher-projects")
    assert entries == [{"url": scoped_project_url(), "name": "infras"}]
    assert run("ls", "deployments", "--server", "scoped", "--project", "infras") == 0
    assert "worker" in capsys.readouterr().out


def test_saved_shows_project_name(capsys):
    rc2.record_project_url(rc2.find_projects_file(), project_url(), "web")
    assert run("saved") == 0
    output = capsys.readouterr().out
    assert "NAME" in output
    assert "web" in output


def test_failure_does_not_record(tmp_path):
    assert run("get", "deployment/missing", "--project", project_url()) == 2
    assert not (tmp_path / ".rancher-projects").exists()


def test_select_saved_project_by_index(capsys):
    assert run_ls("deployments") == 0
    capsys.readouterr()
    assert run("ls", "deployments", "--project", "1") == 0
    assert "api" in capsys.readouterr().out


def test_multiple_saved_projects_need_choice(tmp_path, capsys):
    rc2.record_project_url(rc2.find_projects_file(), project_url())
    rc2.record_project_url(
        rc2.find_projects_file(), f"{BASE_URL}/v3/projects/c-demo:p-data"
    )
    assert run("ls", "deployments") == 2
    assert "多个已记录的项目" in capsys.readouterr().err


def test_select_saved_project_by_short_id(capsys):
    rc2.record_project_url(rc2.find_projects_file(), project_url())
    rc2.record_project_url(
        rc2.find_projects_file(), f"{BASE_URL}/v3/projects/c-demo:p-data"
    )
    assert run("ls", "deployments", "--project", "p-data") == 0
    assert "没有 Deployment" in capsys.readouterr().out


def test_project_id_with_default_server(capsys):
    assert run("ls", "deployments", "--project", "c-demo:p-web") == 0
    assert "worker" in capsys.readouterr().out


def test_project_ui_url(capsys):
    assert run("ls", "deployments", "--project", f"{BASE_URL}/p/c-demo:p-web/") == 0
    assert "worker" in capsys.readouterr().out


def test_project_api_deep_link(capsys):
    assert (
        run(
            "ls",
            "deployments",
            "--project",
            f"{BASE_URL}/v3/projects/c-demo:p-web/deployments?limit=1",
        )
        == 0
    )
    assert "api" in capsys.readouterr().out


def test_project_cluster_url_rejected(capsys):
    assert run("ls", "deployments", "--project", f"{BASE_URL}/dashboard/c/c-demo/") == 2
    assert "这是集群地址" in capsys.readouterr().err


def test_project_url_host_mismatch(capsys):
    assert (
        run(
            "ls",
            "deployments",
            "--server",
            "demo",
            "--project",
            f"{SCOPED_BASE_URL}/p/{SCOPED_PROJECT_ID}/",
        )
        == 2
    )
    assert "主机不同" in capsys.readouterr().err


def test_scoped_project_ui_url(capsys):
    assert (
        run(
            "ls",
            "deployments",
            "--server",
            "scoped",
            "--project",
            f"{SCOPED_BASE_URL}/p/{SCOPED_PROJECT_ID}/",
        )
        == 0
    )
    assert "worker" in capsys.readouterr().out


def test_saved_lists_projects(tmp_path, capsys):
    rc2.record_project_url(rc2.find_projects_file(), project_url())
    assert run("saved") == 0
    output = capsys.readouterr().out
    assert "c-demo:p-web" in output
    assert "demo" in output


def test_project_url_without_matching_config(capsys):
    assert run("ls", "deployments", "--project", "https://other.example.com/v3/projects/c-x:p-y") == 2
    assert "不属于任何 Rancher 配置" in capsys.readouterr().err


def test_server_and_project_mismatch():
    rc2.save_server_config("second", f"{BASE_URL}/other/v3", ACCESS_KEY, SECRET_KEY)
    assert (
        run("ls", "deployments", "--project", project_url(), "--server", "second") == 2
    )


# ────────────────────────────────────────────────────────────
# 错误处理
# ────────────────────────────────────────────────────────────


def test_unauthorized(monkeypatch, capsys):
    rc2.save_server_config("demo", f"{BASE_URL}/v3", ACCESS_KEY, "wrong-secret")
    rc2.load_server_configs.cache_clear()
    assert run("clusters") == 2
    err = capsys.readouterr().err
    assert "HTTP 401" in err
    assert f"{BASE_URL}/apikeys" in err


# ────────────────────────────────────────────────────────────
# 集群级 scope token（Kubernetes API 代理）
# ────────────────────────────────────────────────────────────


def test_scoped_clusters(capsys):
    assert run("clusters", "--server", "scoped") == 0
    output = capsys.readouterr().out
    assert SCOPED_CLUSTER in output
    assert "v1.28.5" in output


def test_scoped_projects_rejected(capsys):
    assert run("projects", "--server", "scoped") == 2
    assert "不能列项目" in capsys.readouterr().err


def test_scoped_namespaces(capsys):
    assert run_scoped("namespaces") == 0
    output = capsys.readouterr().out
    assert "default" in output
    assert "prod" in output
    assert "ops" not in output


def test_scoped_ls_deployments(capsys):
    assert run_scoped("ls", "deployments") == 0
    output = capsys.readouterr().out
    assert "api" in output
    assert "worker" in output
    assert "billing" not in output
    assert "2/3" in output
    assert "registry.local/api:1.0" in output


def test_scoped_ls_namespace(capsys):
    assert run_scoped("ls", "deployments", "--namespace", "prod") == 0
    output = capsys.readouterr().out
    assert "worker" in output
    assert "api" not in output


def test_scoped_ls_other_kinds(capsys):
    assert run_scoped("ls", "sts") == 0
    assert "postgres:16" in capsys.readouterr().out
    assert run_scoped("ls", "ds") == 0
    assert "2/2" in capsys.readouterr().out
    assert run_scoped("ls", "jobs") == 0
    assert "nightly" in capsys.readouterr().out
    assert run_scoped("ls", "cm") == 0
    assert "app-config" in capsys.readouterr().out
    assert run_scoped("ls", "ing") == 0
    assert "web.example.com" in capsys.readouterr().out
    assert run_scoped("ls", "svc") == 0
    assert "10.43.0.10" in capsys.readouterr().out
    assert run_scoped("ls", "pvc") == 0
    assert "1Gi" in capsys.readouterr().out


def test_scoped_ls_secret_hides_values(capsys):
    assert run_scoped("ls", "secrets") == 0
    output = capsys.readouterr().out
    assert "app-secret" in output
    assert "Opaque" in output
    assert base64.b64encode(b"s3cr3t").decode() not in output


def test_scoped_get_summary(capsys):
    assert run_scoped("get", "deployment/api", "--namespace", "default") == 0
    output = capsys.readouterr().out
    assert "2/3" in output
    assert "registry.local/api:1.0" in output
    assert f"{K8S_ROOT}/apis/apps/v1/namespaces/default/deployments/api" in output


def test_scoped_get_yaml(capsys):
    assert run_scoped("get", "deployment/api", "--namespace", "default", "--yaml") == 0
    output = capsys.readouterr().out
    assert "kind: Deployment" in output
    assert "name: api" in output
    assert "status:" not in output
    assert "creationTimestamp" not in output


def test_scoped_get_missing(capsys):
    assert run_scoped("get", "deployment/missing", "--namespace", "default") == 2
    assert "找不到" in capsys.readouterr().err


def test_scoped_get_other_project_namespace(capsys):
    assert run_scoped("get", "deployment/billing", "--namespace", "ops") == 2
    assert "不属于项目" in capsys.readouterr().err
    assert run_scoped("get", "deployment/billing") == 2
    assert "找不到" in capsys.readouterr().err


def test_scoped_get_secret_masks_and_reveals(capsys):
    assert run_scoped("get", "secret/app-secret", "--json") == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["data"] == {"password": "***"}
    assert base64.b64encode(b"s3cr3t").decode() not in captured.out
    assert run_scoped("get", "secret/app-secret", "--json", "--reveal") == 0
    revealed = json.loads(capsys.readouterr().out)
    assert revealed["data"]["password"] == base64.b64encode(b"s3cr3t").decode()


def test_scoped_apply(tmp_path, capsys):
    path = write_manifest(
        tmp_path,
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: demo\n  namespace: prod\n",
    )
    assert run_scoped("apply", "--file", str(path)) == 0
    assert len(STATE["k8s_applied"]) == 1
    record = STATE["k8s_applied"][0]
    assert record["path"] == f"{K8S_ROOT}/api/v1/namespaces/prod/configmaps/demo"
    assert "apply-patch" in record["content_type"]
    assert "kind: ConfigMap" in record["body"]
    assert "已提交清单" in capsys.readouterr().out


def test_scoped_apply_multiple_documents(tmp_path):
    path = write_manifest(
        tmp_path,
        "kind: ConfigMap\napiVersion: v1\nmetadata:\n  name: a\n  namespace: prod\n"
        "---\nkind: Secret\napiVersion: v1\nmetadata:\n  name: b\n  namespace: prod\n",
    )
    assert run_scoped("apply", "--file", str(path)) == 0
    paths = [record["path"] for record in STATE["k8s_applied"]]
    assert paths == [
        f"{K8S_ROOT}/api/v1/namespaces/prod/configmaps/a",
        f"{K8S_ROOT}/api/v1/namespaces/prod/secrets/b",
    ]


def test_scoped_apply_dry_run(tmp_path, capsys):
    path = write_manifest(
        tmp_path,
        "kind: ConfigMap\napiVersion: v1\nmetadata:\n  name: demo\n  namespace: prod\n",
    )
    assert run_scoped("apply", "--file", str(path), "--dry-run") == 0
    assert STATE["k8s_applied"] == []
    output = capsys.readouterr().out
    assert "PATCH" in output
    assert "未提交" in output


def test_scoped_apply_requires_namespace(tmp_path, capsys):
    path = write_manifest(
        tmp_path, "kind: ConfigMap\napiVersion: v1\nmetadata:\n  name: demo\n"
    )
    assert run_scoped("apply", "--file", str(path)) == 2
    assert "metadata.namespace" in capsys.readouterr().err


def test_scoped_apply_unsupported_kind(tmp_path, capsys):
    path = write_manifest(
        tmp_path, "kind: CronJob\napiVersion: batch/v1\nmetadata:\n  name: x\n  namespace: prod\n"
    )
    assert run_scoped("apply", "--file", str(path)) == 2
    assert "不受支持" in capsys.readouterr().err


def test_scoped_redeploy(capsys):
    assert run_scoped("redeploy", "deployment/api", "--namespace", "default") == 0
    assert len(STATE["k8s_redeployed"]) == 1
    record = STATE["k8s_redeployed"][0]
    assert record["path"].endswith(
        "/apis/apps/v1/namespaces/default/deployments/api"
    )
    assert "merge-patch" in record["content_type"]
    assert "cattle.io/timestamp" in record["body"]
    assert "已提交重新部署" in capsys.readouterr().out


def test_scoped_redeploy_dry_run(capsys):
    assert run_scoped(
        "redeploy", "deployment/api", "--namespace", "default", "--dry-run"
    ) == 0
    assert STATE["k8s_redeployed"] == []
    assert "未提交" in capsys.readouterr().out


def test_scoped_redeploy_job_unsupported(capsys):
    assert run_scoped("redeploy", "job/nightly") == 2
    assert "不支持 redeploy" in capsys.readouterr().err


def test_scoped_delete(capsys):
    assert run_scoped("delete", "configmap/app-config", "--namespace", "default") == 0
    assert STATE["k8s_deleted"] == [
        f"{K8S_ROOT}/api/v1/namespaces/default/configmaps/app-config"
    ]
    assert "已删除" in capsys.readouterr().out


def test_scoped_project_mismatch(capsys):
    assert (
        run(
            "ls",
            "deployments",
            "--server",
            "scoped",
            "--project",
            "c-demo:p-web",
        )
        == 2
    )
    assert "不属于 scope 集群" in capsys.readouterr().err


def test_scoped_records_project(tmp_path):
    assert run_scoped("ls", "deployments") == 0
    path = tmp_path / ".rancher-projects"
    assert path.exists()
    assert scoped_project_url() in path.read_text("utf-8")


def test_scoped_unauthorized_hint(capsys):
    rc2.save_server_config(
        "scoped",
        f"{SCOPED_BASE_URL}/v3",
        SCOPED_ACCESS_KEY,
        "wrong-secret",
        f"cluster:{SCOPED_CLUSTER}",
    )
    rc2.load_server_configs.cache_clear()
    assert run("clusters", "--server", "scoped") == 2
    err = capsys.readouterr().err
    assert "集群级 scope token" in err
    assert f"{SCOPED_BASE_URL}/apikeys" in err