"""Actual signed browser, local cookie/CSRF, and immutable member-download boundaries."""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
import time
from http.cookiejar import CookieJar
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import pytest
from test_hub_console import service
from test_hub_console import signed as signed
from test_hub_identity import create, decision, request
from test_local_identity import config

from spireagent.hub.application import HubApplication
from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.workbench.developer import atomic_json
from spireagent.workbench.developer_server import Application, create_server
from spireagent.workbench.identity import LocalIdentity
from spireagent.workbench.member_client import MemberClient


def test_project_presentation_runs_actual_shared_module():
    result = subprocess.run(
        ["node", "--test", "tests/console_project.test.mjs"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_admin_http_requires_browser_csrf_current_membership(tmp_path, signed):
    access, token, _ = signed
    app = HubApplication(
        service(tmp_path),
        "admin" * 16,
        browser_access=access,
        public_origin="https://hub.example",
    )
    # A browser retains one Access JWT; a newly issued JWT has a different CSRF binding.
    admin_token = token()
    identity = request(app, "/app/api/identity", jwt=admin_token)[1]
    route = "/app/api/admin/members"
    invite = {"email": "new@example.org", "csrf_token": identity["csrf_token"]}
    assert (
        request(app, route, method="POST", jwt=admin_token, body=invite, origin="https://evil")[0]
        == 403
    )
    assert (
        request(app, route, method="POST", jwt=admin_token, body={"email": "new@example.org"})[0]
        == 400
    )
    assert request(app, route, method="POST", bearer="admin" * 16, body=invite)[0] == 401
    code, member = request(app, route, method="POST", jwt=admin_token, body=invite)
    assert code == 200 and member["status"] == "invited"
    assert member["device_quota"] == 3
    user_token = token(email="new@example.org")
    assert request(app, "/app/api/identity", jwt=user_token)[1]["principal"]["role"] == "member"
    assert request(app, route, jwt=user_token)[0] == 403
    assert request(app, "/app/api/statistics", jwt=user_token)[0] == 200
    assert request(app, "/app/api/member/campaigns", jwt=user_token)[0] == 200
    changed_session = token(iat=int(time.time()) - 30)
    rejected = request(
        app,
        route + "/" + member["member_id"],
        method="POST",
        jwt=changed_session,
        body={"status": "disabled", "csrf_token": identity["csrf_token"]},
    )
    assert rejected[0] == 403 and rejected[1]["error"] == "identity_csrf_rejected"
    assert request(app, "/app/api/statistics", jwt=user_token)[0] == 200
    result = request(
        app,
        route + "/" + member["member_id"],
        method="POST",
        jwt=admin_token,
        body={"status": "disabled", "csrf_token": identity["csrf_token"]},
    )
    assert result[0] == 200
    assert request(app, "/app/api/statistics", jwt=user_token)[0] == 403


def test_personal_capability_cannot_mutate_admin_or_revoke_devices(tmp_path, signed):
    access, token, _ = signed
    app = HubApplication(
        service(tmp_path), "admin" * 16, browser_access=access, public_origin="https://hub.example"
    )
    flow = create(app)
    assert decision(app, token(), flow)[0] == 200
    grant = app.identity.poll(flow["flow_id"], {"client_secret": "client-secret-" + "a" * 48})
    personal = grant["session_token"]
    for route in ("/v1/identity/admin/members", "/v1/identity/member/admin/members"):
        assert (
            request(app, route, method="POST", bearer=personal, body={"email": "bad@example.org"})[
                0
            ]
            >= 400
        )
    assert request(app, "/v1/identity/member/campaigns", bearer=personal)[0] == 200
    assert request(app, "/v1/identity/member/campaigns", bearer=grant["device"]["token"])[0] == 401


def test_loopback_models_and_member_actions_need_correct_credential(tmp_path, monkeypatch):
    app = Application(config(tmp_path))
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    calls = []
    monkeypatch.setattr(app.models, "command", lambda action: calls.append(action) or {"ok": True})
    monkeypatch.setattr(app.models, "catalog", lambda: {"policies": []})
    monkeypatch.setattr(
        app.models, "prepare_text_runtime",
        lambda profile: calls.append("runtime:" + profile) or {"status": "pending"},
    )
    monkeypatch.setattr(
        app.models,
        "prepare_and_load",
        lambda selection, profile="short": calls.append("prepare:" + selection + ":" + profile)
        or {"status": "pending"},
    )
    monkeypatch.setattr(
        app.models, "start",
        lambda selection, profile="short": calls.append("start:" + selection + ":" + profile)
        or {"status": "pending"},
    )
    monkeypatch.setattr(
        app.evaluation_sharing,
        "share",
        lambda identity, authorized: (
            calls.append((identity, authorized)) or {"status": "preparing"}
        ),
    )
    try:
        with pytest.raises(HTTPError) as error:
            client.open(root + "/api/local-models")
        assert error.value.code == 401
        client.open(root + "/").close()
        headers = {
            "Content-Type": "application/json",
            "Origin": root,
            "X-CSRF-Token": app.account.csrf,
        }
        for changed in ({"Origin": "https://evil"}, {"X-CSRF-Token": "bad"}, {"Host": "evil"}):
            with pytest.raises(HTTPError) as denied:
                client.open(
                    Request(
                        root + "/api/local-models/command",
                        data=b'{"action":"one_step"}',
                        headers={**headers, **changed},
                    )
                )
            assert denied.value.code == 403
        assert calls == []
        with client.open(
            Request(root + "/api/local-models/command", data=b'{"action":"human"}', headers=headers)
        ) as response:
            assert json.load(response) == {"ok": True}
        assert calls == ["human"]
        for route, body in [
            ("prepare", {"selection_id": "audited"}),
            ("prepare", {"selection_id": "audited", "run_profile": "extended"}),
            ("start", {"selection_id": "audited", "run_profile": "extended"}),
            ("share", {"evaluation_id": "a" * 64, "authorized": True}),
        ]:
            with pytest.raises(HTTPError) as denied:
                client.open(
                    Request(
                        root + "/api/local-models/" + route,
                        data=json.dumps(body).encode(),
                        headers={**headers, "Origin": "https://evil"},
                    )
                )
            assert denied.value.code == 403
            with client.open(
                Request(
                    root + "/api/local-models/" + route,
                    data=json.dumps(body).encode(),
                    headers=headers,
                )
            ) as response:
                assert json.load(response)["status"] in {"pending", "preparing"}
        assert calls == ["human", "prepare:audited:short", "prepare:audited:extended",
                         "start:audited:extended",
                         ("a" * 64, True)]
        route = root + "/api/local-models/prepare-text-runtime"
        with pytest.raises(HTTPError) as denied:
            client.open(Request(route, data=b'{"runtime_profile":"text-menu-v1"}',
                                headers={**headers, "Origin": "https://evil"}))
        assert denied.value.code == 403
        with client.open(Request(route, data=b'{"runtime_profile":"text-menu-m2-v1"}',
                                 headers=headers)) as response:
            assert json.load(response) == {"status": "pending"}
        assert calls[-1] == "runtime:text-menu-m2-v1"
        with pytest.raises(HTTPError) as invalid_runtime_body:
            client.open(Request(route, data=b'{"runtime_profile":"text-menu-v1","path":"/tmp"}',
                                headers=headers))
        assert invalid_runtime_body.value.code == 409
        with pytest.raises(HTTPError) as invalid:
            client.open(Request(
                root + "/api/local-models/prepare",
                data=b'{"selection_id":"audited","run_profile":"short","extra":1}',
                headers=headers,
            ))
        assert invalid.value.code == 409
        cli = build_opener()
        with cli.open(
            Request(
                root + "/api/local-models",
                headers={
                    "Authorization": "Bearer " + app.control_token,
                },
            )
        ) as response:
            assert json.load(response) == {"policies": []}
        with pytest.raises(HTTPError) as denied:
            cli.open(
                Request(
                    root + "/api/member/exports",
                    data=b"{}",
                    headers={
                        "Authorization": "Bearer " + app.control_token,
                        "Content-Type": "application/json",
                    },
                )
            )
        assert denied.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def inventory(content):
    item = {
        "file_id": "b" * 64,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
    }
    value = {"schema": "stpd/project-export-v1", "files": [item], "total_bytes": len(content)}
    return {
        **value,
        "export_id": hashlib.sha256(json_bytes(value)).hexdigest(),
        "created_at": "now",
    }


def test_background_export_inventory_uses_transfer_budget(tmp_path):
    account = LocalIdentity(config(tmp_path))
    atomic_json(account.path, {"hub_url": "https://hub.example",
                              "session_token": "private-personal",
                              "expires_at": time.time() + 60})
    content = b"selected team data"
    value = inventory(content)
    identity = value["export_id"]
    calls = []

    class Opener:
        def open(self, request, *, timeout):
            route = request.full_url.removeprefix("https://hub.example")
            calls.append((route, timeout))
            if route == "/v1/identity/member/exports/" + identity:
                return BytesIO(json_bytes(value))
            if route == "/v1/identity/member/exports/" + identity + "/files/" + "b" * 64:
                return BytesIO(content)
            assert route in {"/v1/identity/status", "/v1/identity/member/campaigns",
                             "/v1/identity/member/exports"}
            return BytesIO(b"{}")

    account.opener = Opener()
    client = MemberClient(account)
    account.request("/v1/identity/status")
    client.request("campaigns")
    client.request("exports", {})
    try:
        client.download(identity)
        client.thread.join(timeout=3)
        assert not client.thread.is_alive()
        assert client.download_status()["status"] == "verified"
        assert (tmp_path / "downloads" / identity / ("b" * 64)).read_bytes() == content
        assert [timeout for _, timeout in calls] == [4, 10, 20, 30, 30]
        assert len(calls) == 5  # No automatic request replay.
    finally:
        client.close()


@pytest.mark.parametrize(
    "mutation", [None, "checksum", "oversize", "inventory", "logout", "eof_logout", "preexisting"]
)
def test_download_verifies_exact_inventory_and_bytes(tmp_path, monkeypatch, mutation):
    account = LocalIdentity(config(tmp_path))
    atomic_json(
        account.path,
        {
            "hub_url": "https://hub.example",
            "session_token": "private-personal",
            "expires_at": time.time() + 60,
        },
    )
    content = b"immutable bytes"
    value = inventory(content)
    identity = value["export_id"]
    prior = tmp_path / "downloads" / identity / ("b" * 64 + ".part")
    if mutation == "preexisting":
        prior.parent.mkdir(parents=True)
        prior.write_bytes(b"original partial evidence")
    if mutation == "inventory":
        value["total_bytes"] += 1
    monkeypatch.setattr(account, "request", lambda *a, **k: value)

    class Opener:
        def open(self, request, timeout):
            assert request.headers["Authorization"] == "Bearer private-personal"
            assert (
                request.full_url
                == "https://hub.example/v1/identity/member/exports/"
                + identity
                + "/files/"
                + "b" * 64
            )
            if mutation == "logout":
                account.path.unlink()
            if mutation == "eof_logout":

                class Stream(BytesIO):
                    def read(self, size=-1):
                        result = super().read(size)
                        if not result:
                            account.path.unlink(missing_ok=True)
                        return result

                return Stream(content)
            return BytesIO(
                content + b"extra"
                if mutation == "oversize"
                else b"x" * len(content)
                if mutation == "checksum"
                else content
            )

    account.opener = Opener()
    client = MemberClient(account)
    client.download(identity)
    client.thread.join(timeout=3)
    state = client.operation
    if mutation is None:
        assert state["status"] == "verified" and state["training_admitted"] is False
        assert (tmp_path / "downloads" / identity / ("b" * 64)).read_bytes() == content
        assert "private-personal" not in json.dumps(state)
    else:
        assert state["status"] == "failed"
        assert not (tmp_path / "downloads" / identity / "inventory.json").exists()
    if mutation == "preexisting":
        assert prior.read_bytes() == b"original partial evidence"
    else:
        assert not list(tmp_path.rglob("*.part"))
    client.close()


def test_member_bff_rejects_arbitrary_urls_paths_and_admin_calls(tmp_path):
    client = MemberClient(LocalIdentity(config(tmp_path)))
    for route in (
        "../uploads",
        "exports/../../admin",
        "https://evil",
        "campaigns?token=secret",
        "admin/members",
        "exports?limit=1&limit=2",
    ):
        with pytest.raises((BoundaryError, ValueError)):
            client.request(route)


def test_live_evaluation_transport_has_bounded_larger_body_and_same_auth(
    tmp_path, signed, monkeypatch
):
    access, token, _ = signed
    app = HubApplication(
        service(tmp_path), "admin" * 16, browser_access=access, public_origin="https://hub.example"
    )
    jwt = token()
    identity = request(app, "/app/api/identity", jwt=jwt)[1]
    calls = []
    monkeypatch.setattr(
        app.member_api.live_evaluations,
        "publish",
        lambda principal, body: calls.append(body) or {"accepted": True},
    )
    body = {"bounded_test_padding": "x" * 70000, "csrf_token": identity["csrf_token"]}
    route = "/app/api/member/live-evaluations"
    assert request(app, route, method="POST", jwt=jwt, body=body, origin="https://evil")[0] == 403
    assert not calls
    assert request(app, route, method="POST", jwt=jwt, body=body)[0] == 200
    assert len(calls) == 1 and "csrf_token" not in calls[0]
    assert request(app, "/app/api/member/exports", method="POST", jwt=jwt, body=body)[0] >= 400
    assert (
        request(
            app,
            route,
            method="POST",
            jwt=jwt,
            body={},
            headers={"CONTENT_LENGTH": str(32 * 1024 * 1024 + 1)},
        )[0]
        >= 400
    )
    assert len(calls) == 1


def test_dataset_visibility_uses_current_browser_csrf(tmp_path, signed, monkeypatch):
    access, token, _ = signed
    app = HubApplication(service(tmp_path), "admin" * 16, browser_access=access,
                         public_origin="https://hub.example")
    jwt = token()
    identity = request(app, "/app/api/identity", jwt=jwt)[1]
    calls = []
    monkeypatch.setattr(app.member_api.decisions, "set_archived",
                        lambda principal, body: calls.append(body) or {"archived": True})
    route = "/app/api/member/datasets/visibility"
    body = {"ids": ["a" * 32], "archived": True}
    assert request(app, route, method="POST", jwt=jwt, body=body)[0] == 403
    assert calls == []
    assert request(app, route, method="POST", jwt=jwt,
                   body={**body, "csrf_token": identity["csrf_token"]})[0] == 200
    assert calls == [body]


def test_member_bff_allows_visibility_and_archived_pagination(tmp_path, monkeypatch):
    account = LocalIdentity(config(tmp_path))
    client = MemberClient(account)
    monkeypatch.setattr(client, "token", lambda: "private")
    calls = []
    monkeypatch.setattr(account, "request",
                        lambda route, **kwargs: calls.append(route) or {})
    client.request("datasets/visibility", {"ids": ["a" * 32], "archived": True})
    client.request("datasets/archived?limit=25&offset=25")
    assert calls == ["/v1/identity/member/datasets/visibility",
                     "/v1/identity/member/datasets/archived?limit=25&offset=25"]
