"""jk-track 单元测试 + mock Jenkins 端到端测试。

运行:
    cd skills/jk-track
    pytest tests/test_jk.py -v
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import jk  # noqa: E402

BASE_URL = ""
AUTH_TOKEN = "Basic dXNlcjp0b2tlbg=="  # user:token
CRUMB = "test-crumb"
STATE = {
    "scenario": "finished",
    "polls": 0,
    "trigger_polls": 0,
    "last_trigger": None,
    "stopped": False,
}


def build_json(number, result, building=False, duration=192000, timestamp=1700000000000):
    return {
        "number": number,
        "displayName": f"#{number}",
        "url": f"{BASE_URL}/job/demo/{number}/",
        "building": building,
        "result": None if building else result,
        "duration": duration,
        "estimatedDuration": 200000,
        "timestamp": timestamp,
        "builtOn": "linux-1",
        "actions": [
            {
                "_class": "hudson.model.ParametersAction",
                "parameters": [{"name": "BRANCH", "value": "main"}],
            },
            {
                "_class": "hudson.model.CauseAction",
                "causes": [{"shortDescription": "Started by user alice"}],
            },
        ],
        "changeSet": {
            "items": [
                {
                    "commitId": "abc1234567",
                    "msg": "feat: demo change\n\nbody text",
                    "author": {"fullName": "Alice"},
                }
            ]
        },
        "artifacts": [{"relativePath": "target/demo.jar"}],
    }


def summarize(build):
    keys = (
        "number",
        "url",
        "building",
        "result",
        "duration",
        "estimatedDuration",
        "timestamp",
    )
    return {key: build.get(key) for key in keys}


def synthesize_builds(count=15):
    builds = []
    for number in range(129, 129 - count, -1):
        result = "FAILURE" if number == 128 else "SUCCESS"
        builds.append(summarize(build_json(number, result)))
    return builds


def container_payload():
    return {
        "name": "multi",
        "fullName": "multi",
        "url": f"{BASE_URL}/job/multi/",
        "jobs": [
            {
                "name": "main",
                "displayName": "main",
                "url": f"{BASE_URL}/job/multi/job/main/",
                "lastBuild": summarize(build_json(12, "SUCCESS", duration=3000)),
            },
            {
                "name": "feature/login",
                "displayName": "feature/login",
                "url": f"{BASE_URL}/job/multi/job/feature%2Flogin/",
                "lastBuild": summarize(build_json(42, "FAILURE", duration=4000)),
            },
            {
                "name": "feature/pay",
                "displayName": "feature/pay",
                "url": f"{BASE_URL}/job/multi/job/feature%2Fpay/",
                "lastBuild": None,
            },
        ],
    }


def job_payload():
    if STATE["scenario"] == "running":
        current = build_json(131, "SUCCESS", building=STATE["polls"] < 2, duration=1000)
        last = summarize(current)
        builds = [last] + synthesize_builds()
    else:
        current = build_json(129, "SUCCESS")
        last = summarize(current)
        builds = synthesize_builds()
    return {
        "name": "demo",
        "fullName": "demo",
        "url": f"{BASE_URL}/job/demo/",
        "inQueue": False,
        "queueItem": None,
        "nextBuildNumber": 132,
        "lastBuild": last,
        "lastCompletedBuild": last,
        "lastFailedBuild": summarize(build_json(128, "FAILURE")),
        "lastSuccessfulBuild": last,
        "builds": builds,
    }


STAGES_128 = {
    "status": "FAILED",
    "stages": [
        {"name": "准备", "status": "SUCCESS", "durationMillis": 1000},
        {
            "name": "测试",
            "status": "FAILED",
            "durationMillis": 2000,
            "error": {"message": "gradle test failed"},
        },
    ],
}

CONSOLE_128 = """Started by user alice
[Pipeline] stage
+ ./gradlew test
> Task :test FAILED
[ERROR] Failed to execute goal org.gradle.api.tasks.TaskExecutionException
Caused by: java.lang.IllegalStateException: boom
script returned exit code 1
Finished: FAILURE
"""

TEST_REPORT_128 = {
    "totalCount": 3,
    "failCount": 1,
    "passCount": 2,
    "skipCount": 0,
    "duration": 12500,
    "suites": [
        {
            "name": "com.acme.UserTest",
            "cases": [
                {
                    "className": "com.acme.UserTest",
                    "name": "shouldLogin",
                    "status": "FAILED",
                    "duration": 120,
                    "errorDetails": "expected true but was false",
                    "errorStackTrace": "at com.acme.UserTest.shouldLogin(UserTest.java:42)",
                },
                {
                    "className": "com.acme.UserTest",
                    "name": "shouldLogout",
                    "status": "PASSED",
                    "duration": 30,
                },
            ],
        }
    ],
}


def child_job_payload(number, result, name="main", full_name="multi/main", duration=3000):
    build = summarize(build_json(number, result, duration=duration))
    return {
        "name": name,
        "fullName": full_name,
        "url": f"{BASE_URL}/job/multi/job/{name.replace('/', '%2F')}/",
        "inQueue": False,
        "lastBuild": build,
        "lastCompletedBuild": build,
        "lastFailedBuild": build if result != "SUCCESS" else None,
        "lastSuccessfulBuild": build if result == "SUCCESS" else None,
    }


def route(path):
    if path == "/job/demo/api/json":
        return job_payload()
    if path == "/job/demo/128/api/json":
        return build_json(128, "FAILURE")
    if path == "/job/demo/129/api/json":
        return build_json(129, "SUCCESS")
    if path == "/job/demo/131/api/json":
        if STATE.get("stopped"):
            return build_json(131, "ABORTED", building=False, duration=2000)
        build = build_json(131, "SUCCESS", building=STATE["polls"] < 2, duration=1000)
        STATE["polls"] += 1
        return build
    if path == "/job/demo/128/wfapi/describe":
        return STAGES_128
    if path == "/job/demo/128/consoleText":
        return CONSOLE_128
    if path == "/job/demo/128/testReport/api/json":
        return TEST_REPORT_128
    if path == "/job/demo/129/wfapi/describe":
        return {"status": "SUCCESS", "stages": [{"name": "构建", "status": "SUCCESS"}]}
    if path == "/job/demo/131/wfapi/describe":
        return {"status": "SUCCESS", "stages": [{"name": "构建", "status": "IN_PROGRESS"}]}
    if path == "/job/folder/api/json":
        return {"name": "folder", "jobs": [{"name": "demo"}]}
    if path == "/job/multi/api/json":
        return container_payload()
    if path == "/job/multi/job/main/api/json":
        return child_job_payload(12, "SUCCESS")
    if path == "/job/multi/job/main/12/api/json":
        return build_json(12, "SUCCESS", duration=3000)
    if path == "/job/multi/job/feature%2Flogin/api/json":
        return child_job_payload(42, "FAILURE", name="feature/login", full_name="multi/feature/login", duration=4000)
    if path == "/job/multi/job/feature%2Flogin/42/api/json":
        return build_json(42, "FAILURE", duration=4000)
    if path == "/crumbIssuer/api/json":
        return {"crumb": CRUMB, "crumbRequestField": "Jenkins-Crumb"}
    if path == "/queue/item/900/api/json":
        return queue_payload(132)
    if path == "/queue/item/901/api/json":
        return queue_payload(132)
    if path == "/queue/item/902/api/json":
        return {
            "id": 902,
            "why": None,
            "cancelled": False,
            "executable": {
                "number": 12,
                "url": f"{BASE_URL}/job/multi/job/main/12/",
            },
        }
    if path == "/job/demo/132/api/json":
        building = STATE["trigger_polls"] < 2
        STATE["trigger_polls"] += 1
        return build_json(132, "SUCCESS", building=building, duration=1000)
    return None


def queue_payload(number):
    return {
        "id": number,
        "why": None,
        "cancelled": False,
        "executable": {"number": number, "url": f"{BASE_URL}/job/demo/{number}/"},
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.headers.get("Authorization") != AUTH_TOKEN:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"unauthorized")
            return
        path = urlsplit(self.path).path.rstrip("/")
        # 模拟同一台 Jenkins 上挂在 /jenkins-bad 下的另一套凭据
        if path.startswith("/jenkins-bad"):
            path = path[len("/jenkins-bad") :] or "/"
        payload = route(path)
        if payload is None:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")
            return
        if isinstance(payload, str):
            body = payload.encode("utf-8")
            content_type = "text/plain; charset=utf-8"
        else:
            body = json.dumps(payload).encode("utf-8")
            content_type = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.headers.get("Authorization") != AUTH_TOKEN:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"unauthorized")
            return
        if self.headers.get("Jenkins-Crumb") != CRUMB:
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"no valid crumb")
            return
        path = urlsplit(self.path).path.rstrip("/")
        if path.startswith("/jenkins-bad"):
            path = path[len("/jenkins-bad") :] or "/"
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else ""
        STATE["last_trigger"] = {"path": path, "body": body}
        if path == "/job/demo/131/stop":
            STATE["stopped"] = True
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        location = {
            "/job/demo/build": "/queue/item/900/",
            "/job/demo/buildWithParameters": "/queue/item/901/",
            "/job/multi/job/main/build": "/queue/item/902/",
        }.get(path)
        if not location:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(201)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture(scope="module", autouse=True)
def server():
    global BASE_URL
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    BASE_URL = f"http://127.0.0.1:{httpd.server_address[1]}"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield
    httpd.shutdown()


def job_arg():
    return f"{BASE_URL}/job/demo/"


def write_config(name, server, user="user", token="token"):
    jk.save_server_config(name, server, user, token)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(jk, "CONFIG_DIR", tmp_path / ".jk-track")
    monkeypatch.setattr(jk, "SERVERS_DIR", tmp_path / ".jk-track" / "servers")
    monkeypatch.setattr(jk, "SETTINGS_PATH", tmp_path / ".jk-track" / "settings.json")
    monkeypatch.chdir(tmp_path)
    jk.load_server_configs.cache_clear()
    jk.load_settings.cache_clear()
    write_config("demo", BASE_URL)
    write_config("bad", f"{BASE_URL}/jenkins-bad", user="bad", token="bad")
    write_config(
        "secure", f"{BASE_URL}/secure", user="alice", token="s3cr3t-value"
    )
    jk.save_settings({"default_server": "demo"})
    STATE["scenario"] = "finished"
    STATE["polls"] = 0
    STATE["trigger_polls"] = 0
    STATE["last_trigger"] = None
    STATE["stopped"] = False
    yield
    jk.load_server_configs.cache_clear()
    jk.load_settings.cache_clear()


def run(*argv):
    return jk.main(list(argv))


# ────────────────────────────────────────────────────────────
# 纯函数
# ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://ci.example.com/job/demo/", "https://ci.example.com/job/demo"),
        ("https://ci.example.com/job/demo/128/", "https://ci.example.com/job/demo"),
        ("https://ci.example.com/job/demo/128/consoleText", "https://ci.example.com/job/demo"),
        ("https://ci.example.com/job/demo/api/json?tree=name", "https://ci.example.com/job/demo"),
        ("https://ci.example.com/job/demo/testReport/api/json", "https://ci.example.com/job/demo"),
        ("https://ci.example.com/job/folder/job/demo/", "https://ci.example.com/job/folder/job/demo"),
        ("https://ci.example.com/job/demo#128", "https://ci.example.com/job/demo"),
        ("https://ci.example.com/job/api/", "https://ci.example.com/job/api"),
        ("https://ci.example.com/job/api/128/", "https://ci.example.com/job/api"),
        ("https://ci.example.com/job/123/", "https://ci.example.com/job/123"),
        ("https://ci.example.com/job/console/", "https://ci.example.com/job/console"),
    ],
)
def test_normalize_job_url(raw, expected):
    assert jk.normalize_job_url(raw) == expected


def test_normalize_job_name_with_server():
    assert (
        jk.normalize_job_url("folder/demo", "https://ci.example.com")
        == "https://ci.example.com/job/folder/job/demo"
    )


def test_normalize_server_base():
    assert jk.normalize_server_base("https://ci.example.com/jenkins/") == "https://ci.example.com/jenkins"
    with pytest.raises(jk.JenkinsError):
        jk.normalize_server_base("https://ci.example.com/job/demo/")


def test_extract_log_errors():
    text = "\n".join(
        [
            "line1",
            "line2",
            "line3",
            "[ERROR] boom",
            "detail a",
            "detail b",
            "Finished: FAILURE",
        ]
    )
    blocks = jk.extract_log_errors(text, max_blocks=2)
    assert len(blocks) == 1
    assert blocks[0]["line"] == 1
    assert "[ERROR] boom" in blocks[0]["text"]


# ────────────────────────────────────────────────────────────
# 查询命令
# ────────────────────────────────────────────────────────────


def test_info_latest(capsys):
    assert run("info", "--job", job_arg()) == 0
    out = capsys.readouterr().out
    assert "#129" in out
    assert "成功" in out
    assert "Started by user alice" in out
    assert "BRANCH=main" in out
    assert "abc12345" in out
    assert BASE_URL in out


def test_info_specific_build(capsys):
    assert run("info", "--job", job_arg(), "--build", "#128") == 0
    out = capsys.readouterr().out
    assert "#128" in out
    assert "失败" in out
    assert "测试" in out


def test_info_json(capsys):
    assert run("info", "--job", job_arg(), "--build", "128", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["number"] == 128
    assert payload["result"] == "FAILURE"
    assert payload["job"]["name"] == "demo"


def test_history(capsys):
    assert run("history", "--job", job_arg(), "--limit", "5") == 0
    out = capsys.readouterr().out
    assert "#129" in out and "#128" in out
    assert "#124" not in out
    assert "最新" in out


def test_history_all(capsys):
    assert run("history", "--job", job_arg(), "--all") == 0
    out = capsys.readouterr().out
    assert "#129" in out and "#115" in out
    assert "全部构建" in out


def test_jobs_container(capsys):
    assert run("jobs", "--job", f"{BASE_URL}/job/multi/") == 0
    out = capsys.readouterr().out
    assert "共 3 个子 job" in out
    assert "main" in out and "feature/login" in out and "feature/pay" in out
    assert "#12" in out and "成功" in out
    assert "#42" in out and "失败" in out
    assert "/job/multi/job/main/" in out


def test_jobs_leaf(capsys):
    assert run("jobs", "--job", job_arg()) == 0
    out = capsys.readouterr().out
    assert "没有子 job" in out
    assert "history" in out


def test_jobs_json(capsys):
    assert run("jobs", "--job", f"{BASE_URL}/job/multi/", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["job"] == "multi"
    assert payload["server"] == "demo"
    assert len(payload["children"]) == 3
    assert payload["children"][0]["name"] == "main"


def test_info_container_lists_children(capsys):
    assert run("info", "--job", f"{BASE_URL}/job/multi/") == 2
    err = capsys.readouterr().err
    assert "容器 job" in err
    assert "feature/login" in err
    assert "--child" in err


def test_child_selection(capsys):
    assert run("info", "--job", f"{BASE_URL}/job/multi/", "--child", "main") == 0
    out = capsys.readouterr().out
    assert "#12" in out and "成功" in out


def test_child_selection_with_slash_in_name(capsys):
    assert (
        run("info", "--job", f"{BASE_URL}/job/multi/", "--child", "feature/login")
        == 0
    )
    out = capsys.readouterr().out
    assert "#42" in out and "失败" in out


def test_child_not_found(capsys):
    assert run("info", "--job", f"{BASE_URL}/job/multi/", "--child", "nope") == 2
    err = capsys.readouterr().err
    assert "找不到子 job" in err and "main" in err


def test_child_on_leaf_job(capsys):
    assert run("info", "--job", job_arg(), "--child", "main") == 2
    assert "没有子 job" in capsys.readouterr().err


def test_log_tail_and_grep(capsys):
    assert run("log", "--job", job_arg(), "--build", "128", "--tail", "3") == 0
    out = capsys.readouterr().out
    assert "Finished: FAILURE" in out

    assert run("log", "--job", job_arg(), "--build", "128", "--grep", "Caused by") == 0
    out = capsys.readouterr().out
    assert "Caused by: java.lang.IllegalStateException" in out


def test_tests(capsys):
    assert run("tests", "--job", job_arg(), "--build", "128") == 0
    out = capsys.readouterr().out
    assert "总计 3" in out
    assert "com.acme.UserTest.shouldLogin" in out
    assert "expected true but was false" in out


def test_tests_missing(capsys):
    assert run("tests", "--job", job_arg(), "--build", "129") == 0
    out = capsys.readouterr().out
    assert "没有测试报告" in out


def test_failure(capsys):
    assert run("failure", "--job", job_arg(), "--build", "128") == 0
    out = capsys.readouterr().out
    assert "失败阶段" in out
    assert "测试 (FAILED)" in out
    assert "gradle test failed" in out
    assert "日志错误摘录" in out
    assert "[ERROR] Failed to execute goal" in out
    assert "退出码: 1" in out


# ────────────────────────────────────────────────────────────
# watch
# ────────────────────────────────────────────────────────────


def test_watch_finished(capsys):
    assert run("watch", "--job", job_arg(), "--interval", "0.2") == 0
    out = capsys.readouterr().out
    assert "构建结束: #129" in out


def test_watch_failed_exit_code(capsys):
    assert run("watch", "--job", job_arg(), "--build", "128", "--interval", "0.2") == 1
    out = capsys.readouterr().out
    assert "失败" in out


def test_watch_running_to_success(capsys):
    STATE["scenario"] = "running"
    assert run("watch", "--job", job_arg(), "--interval", "0.2", "--timeout", "10") == 0
    captured = capsys.readouterr()
    assert "构建结束: #131" in captured.out
    assert "运行中" in captured.err


def test_watch_next_waits_for_new_build(capsys):
    STATE["scenario"] = "running"
    STATE["polls"] = -100  # 让 #131 一直处于运行中，验证 --next 的排队提示与超时
    code = run(
        "watch", "--job", job_arg(), "--next", "--interval", "0.2", "--timeout", "0.5"
    )
    assert code == 3
    err = capsys.readouterr().err
    assert "排队中" in err


# ────────────────────────────────────────────────────────────
# trigger
# ────────────────────────────────────────────────────────────


def test_trigger(capsys):
    assert run("trigger", "--job", job_arg()) == 0
    out = capsys.readouterr().out
    assert "已触发" in out
    assert "#132" in out
    assert "/job/demo/132/" in out
    assert STATE["last_trigger"]["path"] == "/job/demo/build"


def test_trigger_with_params(capsys):
    assert (
        run(
            "trigger",
            "--job",
            job_arg(),
            "--param",
            "BRANCH=main",
            "--param",
            "NOTE=a=b",
        )
        == 0
    )
    assert STATE["last_trigger"]["path"].endswith("/buildWithParameters")
    body = parse_qs(STATE["last_trigger"]["body"])
    assert body["BRANCH"] == ["main"]
    assert body["NOTE"] == ["a=b"]


def test_trigger_wait(capsys):
    STATE["trigger_polls"] = 0
    assert (
        run(
            "trigger",
            "--job",
            job_arg(),
            "--wait",
            "--interval",
            "0.2",
            "--queue-timeout",
            "5",
            "--watch-timeout",
            "10",
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "构建结束: #132" in out


def test_trigger_child(capsys):
    assert run("trigger", "--job", f"{BASE_URL}/job/multi/", "--child", "main") == 0
    out = capsys.readouterr().out
    assert "#12" in out and "multi/job/main/12" in out
    assert STATE["last_trigger"]["path"] == "/job/multi/job/main/build"


def test_trigger_container_without_child(capsys):
    assert run("trigger", "--job", f"{BASE_URL}/job/multi/") == 2
    assert "容器 job" in capsys.readouterr().err


def test_trigger_dry_run(capsys):
    assert run("trigger", "--job", job_arg(), "--dry-run", "--param", "A=1") == 0
    out = capsys.readouterr().out
    assert "[dry-run]" in out and "A=1" in out
    assert STATE["last_trigger"] is None
    assert not jobs_file().exists()


def test_trigger_bad_param(capsys):
    assert run("trigger", "--job", job_arg(), "--param", "NOEQ") == 2
    assert "KEY=VALUE" in capsys.readouterr().err


def test_trigger_json(capsys):
    assert run("trigger", "--job", job_arg(), "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["build"]["number"] == 132
    assert payload["queue"].endswith("/queue/item/900/")


def test_trigger_wait_json(capsys):
    STATE["trigger_polls"] = 0
    assert (
        run(
            "trigger",
            "--job",
            job_arg(),
            "--wait",
            "--json",
            "--interval",
            "0.2",
            "--watch-timeout",
            "10",
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["build"]["number"] == 132
    assert payload["build"]["result"] == "SUCCESS"


# ────────────────────────────────────────────────────────────
# stop
# ────────────────────────────────────────────────────────────


def test_stop_finished(capsys):
    assert run("stop", "--job", job_arg(), "--build", "128") == 0
    out = capsys.readouterr().out
    assert "无需停止" in out
    assert "/job/demo/128/" in out


def test_stop_running(capsys):
    STATE["scenario"] = "running"
    STATE["polls"] = -100
    assert run("stop", "--job", job_arg(), "--build", "131") == 0
    out = capsys.readouterr().out
    assert "已发送停止请求" in out
    assert "/job/demo/131/" in out
    assert STATE["stopped"] is True


def test_stop_wait(capsys):
    STATE["scenario"] = "running"
    STATE["polls"] = -100
    assert (
        run(
            "stop",
            "--job",
            job_arg(),
            "--build",
            "131",
            "--wait",
            "--interval",
            "0.2",
            "--watch-timeout",
            "5",
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "已发送停止请求" in out
    assert "已中止" in out


def test_stop_dry_run(capsys):
    STATE["scenario"] = "running"
    STATE["polls"] = -100
    assert run("stop", "--job", job_arg(), "--build", "131", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "[dry-run]" in out
    assert STATE["last_trigger"] is None
    assert not jobs_file().exists()


def test_stop_json(capsys):
    STATE["scenario"] = "running"
    STATE["polls"] = -100
    assert run("stop", "--job", job_arg(), "--build", "131", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stopped"] is True
    assert payload["number"] == 131


def test_stop_container_without_child(capsys):
    assert run("stop", "--job", f"{BASE_URL}/job/multi/") == 2
    assert "容器 job" in capsys.readouterr().err


# ────────────────────────────────────────────────────────────
# 配置匹配与安全
# ────────────────────────────────────────────────────────────


def test_auth_failure(capsys):
    assert run("info", "--job", f"{BASE_URL}/jenkins-bad/job/demo/") == 2
    err = capsys.readouterr().err
    assert "认证失败" in err
    assert "config add bad" in err


def test_no_matching_server(capsys):
    assert run("info", "--job", "https://unknown.example.com/job/x/") == 2
    err = capsys.readouterr().err
    assert "不属于任何已配置的 Jenkins" in err
    assert "demo" in err
    assert "config add" in err


def test_ambiguous_servers(capsys):
    write_config("dup", BASE_URL, user="u", token="t")
    assert run("info", "--job", job_arg()) == 2
    err = capsys.readouterr().err
    assert "同时匹配多个 server 配置" in err
    assert "dup" in err


def test_forced_server(capsys):
    assert run("info", "--job", job_arg(), "--server", "demo") == 0
    assert "#129" in capsys.readouterr().out


def test_forced_server_mismatch(capsys):
    assert run("info", "--job", job_arg(), "--server", "bad") == 2
    assert "不匹配" in capsys.readouterr().err


def test_relative_job_name_uses_default_server(capsys):
    assert run("info", "--job", "demo") == 0
    assert "#129" in capsys.readouterr().out


def test_missing_configs(capsys):
    jk.SERVERS_DIR.joinpath("demo.json").unlink()
    jk.SERVERS_DIR.joinpath("bad.json").unlink()
    jk.SERVERS_DIR.joinpath("secure.json").unlink()
    jk.load_server_configs.cache_clear()
    assert run("info", "--job", job_arg()) == 2
    err = capsys.readouterr().err
    assert "还没有任何 Jenkins 配置" in err
    assert "config add" in err


# ────────────────────────────────────────────────────────────
# config 命令
# ────────────────────────────────────────────────────────────


def test_config_list_masks_token(capsys):
    assert jk.main(["config", "list"]) == 0
    out = capsys.readouterr().out
    assert "demo *" in out
    assert BASE_URL in out
    assert "us***" in out
    assert "s3cr3t-value" not in out
    assert "token 永远不会显示" in out


def test_config_show_masks_token(capsys):
    assert jk.main(["config", "show", "secure"]) == 0
    out = capsys.readouterr().out
    assert "al***" in out
    assert "已配置（不显示）" in out
    assert "s3cr3t-value" not in out
    assert f"{BASE_URL}/secure" in out


def test_config_add_interactive(capsys, monkeypatch):
    answers = iter(["https://ci.example.com", "bob"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    monkeypatch.setattr(jk.getpass, "getpass", lambda *_: "new-token")
    assert jk.main(["config", "add", "new"]) == 0
    out = capsys.readouterr().out
    assert "new-token" not in out
    path = jk.SERVERS_DIR / "new.json"
    data = json.loads(path.read_text("utf-8"))
    assert data["server"] == "https://ci.example.com"
    assert data["user"] == "bob"
    assert data["token"] == "new-token"
    assert (path.stat().st_mode & 0o777) == 0o600


def test_config_add_rejects_job_url(capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: f"{BASE_URL}/job/demo/")
    assert jk.main(["config", "add", "oops"]) == 2
    assert "不要填具体 job 的 URL" in capsys.readouterr().err
    assert not (jk.SERVERS_DIR / "oops.json").exists()


def test_config_set_is_alias_for_add(capsys, monkeypatch):
    answers = iter(["https://ci2.example.com", "bob"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    monkeypatch.setattr(jk.getpass, "getpass", lambda *_: "another-token")
    assert jk.main(["config", "set", "second"]) == 0
    assert (jk.SERVERS_DIR / "second.json").exists()


def test_config_use_and_remove(capsys):
    assert jk.main(["config", "use", "bad"]) == 0
    assert jk.load_settings()["default_server"] == "bad"

    assert jk.main(["config", "remove", "bad"]) == 0
    assert not (jk.SERVERS_DIR / "bad.json").exists()
    assert jk.load_settings()["default_server"] == "demo"


# ────────────────────────────────────────────────────────────
# 项目 .jenkins-jobs
# ────────────────────────────────────────────────────────────


def jobs_file():
    return Path.cwd() / ".jenkins-jobs"


def test_job_url_auto_saved(capsys):
    assert run("info", "--job", job_arg()) == 0
    captured = capsys.readouterr()
    assert job_arg().rstrip("/") in jk.read_job_urls(jobs_file())
    assert ".jenkins-jobs" in captured.err


def test_job_url_deduplicated(capsys):
    assert run("info", "--job", job_arg()) == 0
    assert run("info", "--job", job_arg()) == 0
    assert len(jk.read_job_urls(jobs_file())) == 1


def test_no_save(capsys):
    assert run("info", "--job", job_arg(), "--no-save") == 0
    assert not jobs_file().exists()


def test_saved_file_comments_ignored(capsys):
    jobs_file().write_text(
        "# 项目 job\n\n" + job_arg() + "\n", "utf-8"
    )
    assert run("info") == 0
    assert "#129" in capsys.readouterr().out


def test_saved_index_selection(capsys):
    jobs_file().write_text(f"{job_arg()}\n{BASE_URL}/job/multi/\n", "utf-8")
    assert run("info", "--job", "1") == 0
    assert "#129" in capsys.readouterr().out

    assert run("info", "--job", "#2") == 2
    assert "容器 job" in capsys.readouterr().err


def test_saved_short_name(capsys):
    jobs_file().write_text(f"{job_arg()}\n", "utf-8")
    assert run("info", "--job", "demo") == 0
    assert "#129" in capsys.readouterr().out


def test_saved_command(capsys):
    assert run("info", "--job", job_arg()) == 0
    capsys.readouterr()
    assert jk.main(["saved"]) == 0
    out = capsys.readouterr().out
    assert "Job 列表" in out
    assert job_arg().rstrip("/") in out
    assert "demo" in out


def test_saved_command_empty(capsys):
    assert jk.main(["saved"]) == 0
    assert "中没有记录 job URL" in capsys.readouterr().out


def test_jobs_file_override(tmp_path, capsys):
    custom = tmp_path / "custom-jobs.txt"
    assert run("info", "--job", job_arg(), "--jobs-file", str(custom)) == 0
    assert custom.exists()
    assert not jobs_file().exists()

