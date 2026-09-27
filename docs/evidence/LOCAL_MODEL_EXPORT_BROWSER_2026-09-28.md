# Explicit offline model export: synthetic browser receipt

Date: 2026-09-28 Australia/Brisbane. This is local application evidence, not a
production install, real-data training, Runtime registration or native gameplay.

## Exact candidate and setup

The clean application checkout was `7058a2ceb9644f6bd653c92729a3ef008bcdd99c`,
tree `fb5cb8cc01d5846be330e571634e26a4052b1f87`. It combines separately reviewed
backend `187722185e0a8a32a633cb493ddfad63fd177406` and UI
`98b8762c74c132872ccfae9301414062e6959b20`, based on accepted PR #72.
Its private Python 3.11.15 environment was prepared with `uv sync --locked
--all-extras`; project imports resolved to that checkout. No running Workbench
environment was reused or synchronized.

An existing synthetic three-step model and its immutable lineage were copied
into an independent temporary local artifact store. The real `Application` and
HTTP server ran there, with no Hub, collection tool or game connection. No model
training was started. The earlier synthetic model identity was
`9bf8c81810424736d26c3e39e2d10ac8c8fecf3dfbb9911eb098912fc4507a88`.

## Actual operations and results

1. Safari opened the real model detail and displayed B v2, scratch, three steps,
   CPU and engineering-only qualification. Export status was idle.
2. One explicit click on **导出并校验** started the real export service. The page
   displayed pending. The service used the existing export and standalone scorer
   loader; it did not register a game policy.
3. One explicit **刷新导出状态** click displayed **导出校验完成；尚未登记为游戏模型，
   也未加载。游戏兼容性尚未检查。**, 157.8 KiB, and an enabled recheck button.
4. The durable operation was `completed`, with the same model ID and exactly
   161582 payload bytes. The operation ID stayed
   `3b3c53786f914a1b9b7f50332ac4695b`. The training operation remained `idle`.
5. The supervisor stopped only this temporary fixture server; its process exited
   0 and the receipt recorded `stopped`. The active user Workbench was untouched.

The fixture had a ten-minute automatic bound and was stopped after about 44
seconds. Browser actions used native Safari accessibility controls. No browser
script injection or mocked export API was used.

## Related source checks and limits

- Backend: from the author checkout root,
  `PYTHONPATH=python python/.venv/bin/python -m pytest -q python/tests/test_local_model_export.py python/tests/test_local_workspace_http.py`:
  13 passed, exit 0; scoped Ruff and Mypy
  also exited 0. Tests include real synthetic export/scoring, tampered bytes,
  interrupted completion, selected-store changes and HTTP origin/CSRF/instance checks.
- Integrated `node --test python/tests/console_project.test.mjs`: 124 passed,
  zero failures or skips, exit 0. This includes explicit-only dispatch and stale
  response/workspace handling.
- Hosted checks belong to the final PR candidate; this receipt does not borrow
  another PR's green result. Source tests and this synthetic UI exercise do not
  establish game compatibility, strategy quality or private-data training readiness.
