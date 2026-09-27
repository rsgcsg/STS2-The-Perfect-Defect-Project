from __future__ import annotations

import json
import threading
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import pytest

from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.developer_server import Application, create_server


def test_local_recordings_route_uses_authenticated_local_browser_and_is_read_only(
    tmp_path: Path, monkeypatch
) -> None:
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    app = Application(config)
    reads = []
    value = {
        "schema": "stpd/local-recording-catalog-v1",
        "status": "tool_registration_missing",
        "error_code": "collection_tool_registration_missing",
        "requires_cloud_account": False,
    }
    monkeypatch.setattr(app.local_recordings, "read", lambda: reads.append(True) or value)
    monkeypatch.setattr(app.account, "status", lambda: (_ for _ in ()).throw(
        AssertionError("local source view must not request cloud identity")))
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    try:
        with pytest.raises(HTTPError) as denied:
            client.open(root + "/api/local-recordings")
        assert denied.value.code == 401
        client.open(root + "/").close()
        with client.open(root + "/api/local-recordings") as response:
            observed = json.load(response)
        assert observed == value
        assert len(reads) == 1
        with pytest.raises(HTTPError) as query:
            client.open(root + "/api/local-recordings?path=/private")
        assert query.value.code == 400
        assert len(reads) == 1

        request = Request(
            root + "/api/local-recordings",
            headers={"Host": f"localhost:{server.server_port}"},
        )
        with pytest.raises(HTTPError) as wrong_host:
            client.open(request)
        assert wrong_host.value.code == 403
        assert len(reads) == 1
        assert app.hub is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()
