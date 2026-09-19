import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

class Element {
  constructor(tag) {
    this.tag = tag;
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.textContent = "";
    this.value = "";
    this.checked = false;
    this.disabled = false;
  }
  get firstChild() { return this.children[0]; }
  append(...items) {
    this.children.push(...items);
  }
  replaceChildren(...items) {
    this.children = items;
  }
  setAttribute(key, value) {
    this.attributes[key] = value;
  }
}
const walk = (node) => [node, ...(node?.children || []).flatMap(walk)];
const text = (node) =>
  walk(node)
    .map((item) => item.textContent || "")
    .join("\n");
const find = (node, predicate) => {
  const result = walk(node).find(predicate);
  assert.ok(result, "expected element");
  return result;
};
const action = (node, name) =>
  find(node, (element) => element.dataset?.action === name);
const field = (node, name) => find(node, (element) => element.name === name);
const id = (digit) => digit.repeat(64);
const uploadId = "a".repeat(32);
const enrollmentId = "e".repeat(32);
const memberId = "f".repeat(32);
const owner = (role = "member", subject = "person") => ({
  status: "signed_in",
  csrf_token: "local-csrf",
  device_id: "this-pc",
  principal: { role, subject, email: subject + "@example.test" },
  devices: [
    {
      device_id: "this-pc",
      name: "This computer",
      ownership: "owned_by_you",
      active: true,
    },
    {
      device_id: "other-pc",
      name: "Other computer",
      ownership: "owned_by_you",
      active: true,
    },
    {
      device_id: "shared-pc",
      name: "Teammate",
      ownership: "shared",
      active: true,
    },
    {
      device_id: "revoked-pc",
      name: "Revoked",
      ownership: "owned_by_you",
      active: false,
    },
  ],
});
const emptyList = () => ({
  items: [],
  templates: [],
  total: 0,
  next_offset: null,
  availability: "available",
});
function setup({
  mode = "local",
  identity = owner(),
  view = "statistics",
  handler = () => emptyList(),
  query = "",
} = {}) {
  const calls = [],
    notice = new Element("div"),
    confirms = [];
  let selectedScope = mode === "local" ? "local" : "project",
    generation = 0,
    reloads = 0;
  const context = vm.createContext({
    document: {
      body: { dataset: { mode, cloudUrl: "https://hub.example.test" } },
      getElementById: () => notice,
      createElement: (tag) => new Element(tag),
    },
    location: { search: "?view=" + view + query },
    URL,
    URLSearchParams,
    Date,
    AbortSignal,
    window: {
      confirm: (message) => {
        confirms.push(message);
        return true;
      },
      SpireIdentity: {
        context: () =>
          `${selectedScope}:${identity?.principal?.subject || "anonymous"}:${generation}`,
        isLocal: () => mode === "local" && selectedScope === "local",
        api: (route, query) => {
          const params = new URLSearchParams(query);
          if (!["project", "local"].includes(selectedScope))
            params.set("device", selectedScope);
          return (
            (mode === "local" ? "/api/project/" : "/app/api/") +
            route +
            (params.size ? "?" + params : "")
          );
        },
      },
    },
    fetch: async (url, options) => {
      calls.push({ url, options });
      const body = await handler(url, options);
      return {
        ok: !(body?.httpStatus >= 400),
        status: body?.httpStatus || 200,
        json: async () => body,
      };
    },
  });
  vm.runInContext(
    readFileSync(
      new URL("../spireagent/console/project.js", import.meta.url),
      "utf8",
    ),
    context,
  );
  const ui = context.window.SpireProject;
  ui.reload = async () => {
    reloads++;
  };
  return {
    ui,
    calls,
    notice,
    confirms,
    context,
    get reloads() {
      return reloads;
    },
    render: (mount) => ui.render(view, identity, mount),
    scope: (value) => {
      selectedScope = value;
      generation++;
    },
    account: (value) => {
      identity = value;
      generation++;
    },
    navigate: (newView, newQuery = "") => {
      view = newView;
      context.location.search = "?view=" + view + newQuery;
    },
  };
}
const post = (calls) => calls.filter((call) => call.options.method === "POST");
const body = (call) => JSON.parse(call.options.body);
const template = {
  template_id: id("b"),
  template: {
    schema: "stpd/collection-activity-v1",
    name: "Bounded capture",
    description: "New recordings only",
    consent_text: "Reviewed consent",
    activity_id: "canary",
    version: 1,
    game: { version: "exact-game" },
  },
};
const enrollment = {
  schema: "stpd/collection-enrollment-v1",
  template_id: id("b"),
  template: template.template,
  enrollment_id: enrollmentId,
  device_id: "this-pc",
  campaign_id: "campaign-" + enrollmentId,
  declared_at: 1700000000,
};
const exportManifest = {
  schema: "stpd/project-export-v1",
  export_id: id("c"),
  created_at: "2026-09-14T00:00:00Z",
  files_count: 1,
  total_bytes: 42,
  files: [
    {
      file_id: id("d"),
      sha256: id("e"),
      size: 42,
      filename: id("d") + ".bin",
      type: "payload",
      role: "dataset",
      upload_id: null,
    },
  ],
};

test("statistics consumes owner coverage and occurrence units without replacing unknowns with zero", async () => {
  const metric = { value: null, known: 1, unknown: 2, partial: true };
  const data = {
    schema: "stpd/project-statistics-v1",
    observed_at: "2026-09-14T00:00:00Z",
    uploads: 3,
    unique_content_ids: 2,
    duplicate_content_uploads: 1,
    metrics: {
      canonical: metric,
      real_failures: { value: 0, known: 3, unknown: 0, partial: false },
      native_starts: { value: 1, known: 1, unknown: 2, partial: true },
    },
    facets: {
      device: {
        availability: "available",
        known: 3,
        unknown: 0,
        items: [{ value: "this-pc", count: 3 }],
        truncated: false,
      },
    },
    collection_profiles: {
      availability: "partial",
      sources: 3,
      profiles_available: 1,
      profiles_missing: 2,
      records: 4,
      partial: true,
      facets: {
        game_version: {
          availability: "unavailable",
          known: 0,
          unknown: 4,
          items: [],
        },
      },
    },
    artifacts: { counts: { dataset: 2 }, inventory_complete: null },
  };
  const env = setup({
      handler: (url) => {
        assert.equal(url, "/api/project/statistics");
        return data;
      },
    }),
    page = await env.render();
  assert.equal(page.dataset.observedAt, data.observed_at);
  assert.match(text(page), /部分汇总/);
  assert.match(text(page), /未知 2 份/);
  assert.match(text(page), /不能单独证明 uninterrupted Full Run/);
  assert.match(text(page), /不是云存储桶的完整盘点/);
  assert.match(text(page), /记录出现次数/);
  const cards = walk(page).filter((node) =>
    node.className?.includes("metric-value"),
  );
  assert.equal(cards[2].textContent, "未知");
  assert.equal(cards[3].textContent, "0");
});

test("project catalog preserves the selected device filter but never calls nonexistent local catalog", async () => {
  const env = setup({ view: "research" });
  env.scope("other-pc");
  await env.render();
  assert.equal(env.calls.length, 3);
  assert.ok(env.calls.every((call) => call.url.includes("device=other-pc")));
  env.scope("local");
  env.calls.length = 0;
  await env.render();
  assert.ok(env.calls.every((call) => call.url.startsWith("/api/project/")));
});

test("member and personal admin views never request browser management endpoints", async () => {
  for (const [mode, role] of [
    ["local", "admin"],
    ["cloud", "member"],
  ]) {
    const env = setup({ mode, identity: owner(role), view: "members" }),
      page = await env.render();
    assert.equal(env.calls.length, 0);
    assert.match(
      text(page),
      role === "admin" ? /云端浏览器/ : /当前是项目成员/,
    );
  }
});

test("browser admin invitation uses explicit role quota and csrf; promotions and disables confirm", async () => {
  const item = {
    member_id: memberId,
    email: "member@example.test",
    role: "member",
    status: "active",
    device_quota: 3,
    enroll_devices: true,
    active_device_count: 1,
    owned_device_count: 2,
  };
  const env = setup({
    mode: "cloud",
    identity: owner("admin"),
    view: "members",
    handler: (url, options) =>
      options.method === "POST" ? {} : { items: [item], total: 1 },
  });
  let page = await env.render();
  field(page, "email").value = "new@example.test";
  await action(page, "member-save-invite").onclick();
  assert.deepEqual(body(post(env.calls)[0]), {
    email: "new@example.test",
    role: "member",
    device_quota: 3,
    enroll_devices: true,
    csrf_token: "local-csrf",
  });
  page = await env.render();
  const row = find(
    page,
    (node) =>
      node.tag === "section" &&
      node.children[0]?.textContent === "member@example.test",
  );
  field(row, "role").value = "admin";
  await action(row, "member-save-" + memberId).onclick();
  assert.equal(env.confirms.length, 1);
  assert.equal(body(post(env.calls)[1]).role, "admin");
  await action(row, "member-status-" + memberId).onclick();
  assert.equal(body(post(env.calls)[2]).status, "disabled");
  assert.equal(env.confirms.length, 2);
});

test("server last-admin rejection remains a visible failure without optimistic success", async () => {
  const env = setup({
    mode: "cloud",
    identity: owner("admin"),
    view: "members",
    handler: (url, options) =>
      options.method === "POST"
        ? { httpStatus: 409, error: "last_admin_required" }
        : {
            items: [
              {
                member_id: memberId,
                email: "a@example.test",
                role: "admin",
                status: "active",
                device_quota: 3,
                enroll_devices: true,
              },
            ],
            total: 1,
          },
  });
  const page = await env.render();
  await action(page, "member-status-" + memberId).onclick();
  assert.match(text(env.notice), /保留至少一位/);
  assert.equal(env.reloads, 0);
});

const settings = (record = template) => ({
  schema: "stpd/collection-settings-v1", default: record, upload_hosts: ["uploads.example.test"],
  observed_at: "2026-09-15T00:00:00Z",
});
const preparation = (saved = false) => ({
  status: saved ? "native_binding_required" : "not_prepared",
  configuration_saved: saved, delivery_selected: false, delivery_status: "stopped",
  native_binding: { status: "not_checked" }, next_action: saved ? "bind_recording_root" : "prepare",
});
test("raw export selection is exact, csrf scoped, and creating a manifest does not claim completed download", async () => {
  const env = setup({
    view: "downloads",
    handler: (url, options) => {
      if (url.includes("/collections?"))
        return {
          items: [
            {
              id: uploadId,
              upload_id: uploadId,
              status: "verified",
              device_id: "this-pc",
            },
          ],
          total: 1,
        };
      if (options.method === "POST") return exportManifest;
      if (url.endsWith("/download-status"))
        return {
          status: "downloading",
          export_id: id("c"),
          verified_files: 1,
          verified_bytes: 42,
          total_files: 2,
          total_bytes: 84,
        };
      return exportManifest;
    },
  });
  let page = await env.render(),
    selected = field(page, "collection-" + uploadId);
  selected.checked = true;
  selected.onchange();
  await action(page, "create-export").onclick();
  assert.deepEqual(body(post(env.calls)[0]), {
    schema: "stpd/project-export-request-v1",
    collections: [uploadId],
    artifacts: [],
  });
  assert.equal(
    post(env.calls)[0].options.headers["X-CSRF-Token"],
    "local-csrf",
  );
  assert.match(text(env.notice), /文件尚未下载/);
  page = await env.render();
  assert.match(text(page), /1 \/ 2/);
  assert.match(text(page), /42 B \/ 84 B/);
  assert.doesNotMatch(text(page), /所选文件已通过本机完整性校验/);
  await action(page, "export-download-" + id("c")).onclick();
  assert.ok(post(env.calls)[1].url.endsWith("/download"));
  assert.match(text(env.notice), /服务已接收下载请求/);
});

test("export artifact payloads require explicit selected own roles; manifest does not recursively select parents", async () => {
  const artifact = id("9"),
    env = setup({
      mode: "cloud",
      view: "downloads",
      handler: (url, options) => {
        if (url.includes("/collections?")) return emptyList();
        if (url.includes("/datasets/"))
          return {
            item: {
              artifact_id: artifact,
              kind: "dataset",
              parents: [{ artifact_id: id("8") }],
              payloads: [
                { role: "training", size: 42, sha256: id("7") },
                { role: "profile", size: 12, sha256: id("6") },
              ],
            },
          };
        return exportManifest;
      },
    });
  let page = await env.render();
  field(page, "artifact-id").value = artifact;
  await action(page, "inspect-export-artifact").onclick();
  page = await env.render();
  const manifest = field(page, "artifact-" + artifact),
    payload = field(page, "role-training");
  assert.equal(payload.disabled, true);
  manifest.checked = true;
  manifest.onchange();
  payload.checked = true;
  payload.onchange();
  await action(page, "create-export").onclick();
  assert.deepEqual(body(post(env.calls)[0]), {
    schema: "stpd/project-export-request-v1",
    collections: [],
    artifacts: [{ artifact_id: artifact, roles: ["training"] }],
    csrf_token: "local-csrf",
  });
});

test("cloud export links are fixed same-origin file identities and malformed identities fail closed", async () => {
  const env = setup({
    mode: "cloud",
    view: "downloads",
    query: "&id=" + id("c"),
    handler: () => exportManifest,
  });
  const page = await env.render();
  const download = find(page, (node) => node.textContent === "下载此文件");
  assert.equal(
    download.href,
    "/app/api/member/exports/" + id("c") + "/files/" + id("d"),
  );
  assert.equal(env.calls.length, 1);
  assert.ok(env.calls.every((call) => !call.url.includes("local-models")));
  env.navigate("downloads", "&id=../../secret");
  const broken = await env.render();
  assert.match(text(broken), /invalid_export_identity/);
  assert.equal(env.calls.length, 1);
});

function modelHandler(url, options) {
  if (url === "/api/local-models")
    return {
      policies: [{ selection_id: "audited-cpu", label: "Reviewed CPU" }],
      downloaded_models: [],
      evaluations: [],
    };
  if (url === "/api/local-models/status")
    return { status: "idle", loaded: false, operation: null };
  if (url.includes("/readiness?"))
    return {
      selection_id: "audited-cpu",
      status: "ready_to_load",
      checks: { package: { status: "pass" } },
    };
  if (options.method === "POST")
    return {
      status: "pending",
      operation: { action: "start", status: "pending" },
    };
  return emptyList();
}
test("runtime prepares one trusted selection with optional diagnosis and no implicit start", async () => {
  const env = setup({ view: "local-models", handler: modelHandler });
  let page = await env.render();
  assert.equal(action(page, "model-start-audited-cpu").disabled, false);
  assert.equal(post(env.calls).length, 0);
  await action(page, "model-readiness-audited-cpu").onclick();
  assert.ok(
    env.calls.some(
      (call) =>
        call.url === "/api/local-models/readiness?selection_id=audited-cpu",
    ),
  );
  page = await env.render();
  assert.equal(action(page, "model-start-audited-cpu").disabled, false);
  assert.match(text(page), /不代表模型已经加载/);
  await action(page, "model-start-audited-cpu").onclick();
  assert.equal(post(env.calls)[0].url, "/api/local-models/prepare");
  assert.deepEqual(body(post(env.calls)[0]), { selection_id: "audited-cpu" });
  assert.match(text(env.notice), /尚需完成实际加载/);
});

test("unknown or stale runtime state permits explicit recovery but never another game decision", async () => {
  for (const state of [
    {status:"loading", loaded:false, operation:{status:"pending", action:"prepare-and-load"}},
    {
      status: "command_unknown",
      loaded: true,
      operation: { status: "unknown" },
      runtime: { mode: "auto", tainted: false },
    },
    {
      status: "loaded",
      loaded: true,
      observation_error: "unavailable",
      runtime: { mode: "auto" },
    },
    {
      status: "recovery_required",
      loaded: false,
      previous_session: { run_id: "previous" },
    },
  ]) {
    const env = setup({
        view: "local-models",
        handler: (url, options) =>
          url === "/api/local-models/status"
            ? state
            : modelHandler(url, options),
      }),
      page = await env.render();
    for (const name of ["auto", "one_step", "shadow"])
      assert.equal(action(page, "model-command-" + name).disabled, true);
    for (const name of ["human", "stop"])
      assert.equal(action(page, "model-command-" + name).disabled, false);
    await action(page, "model-command-auto").onclick();
    assert.equal(post(env.calls).length, 0);
    await action(page, "model-command-human").onclick();
    assert.deepEqual(body(post(env.calls)[0]), { action: "human" });
  }
});

test("cloud local-models view does not call or control any local service", async () => {
  const env = setup({ mode: "cloud", view: "local-models" }),
    page = await env.render();
  assert.equal(env.calls.length, 0);
  assert.match(text(page), /云页面不会远程启动或控制游戏/);
});

test("explicit game command needs no repeated confirmation and network ambiguity never auto retries", async () => {
  const env = setup({
      view: "local-models",
      handler: (url, options) => {
        if (url === "/api/local-models/status")
          return {
            status: "loaded",
            loaded: true,
            runtime: { mode: "human", lifecycle: "running" },
          };
        if (options.method === "POST") throw new Error("network lost");
        return modelHandler(url, options);
      },
    }),
    page = await env.render();
  await action(page, "model-command-one_step").onclick();
  assert.equal(env.confirms.length, 0);
  assert.equal(post(env.calls).length, 1);
  assert.match(text(env.notice), /不会自动重发/);
  assert.equal(env.reloads, 0);
});

test("late account mutation response cannot repaint notices or retain exports for the next account", async () => {
  let finish;
  const env = setup({
    view: "downloads",
    handler: (url, options) => {
      if (url.includes("/collections?"))
        return { items: [{ id: uploadId, status: "verified" }], total: 1 };
      if (options.method === "POST")
        return new Promise((resolve) => {
          finish = resolve;
        });
      return { status: "idle" };
    },
  });
  let page = await env.render();
  const item = field(page, "collection-" + uploadId);
  item.checked = true;
  item.onchange();
  const operation = action(page, "create-export").onclick();
  await new Promise((resolve) => setImmediate(resolve));
  env.account(owner("member", "next"));
  page = await env.render();
  finish(exportManifest);
  await operation;
  assert.equal(text(env.notice), "");
  assert.equal(env.reloads, 0);
  assert.equal(field(page, "collection-" + uploadId).checked, false);
  page = await env.render();
  assert.equal(
    walk(page).some((n) => n.dataset?.action === "export-download-" + id("c")),
    false,
  );
});

test("research category read failures preserve other categories and do not become empty success", async () => {
  const env = setup({
      view: "research",
      handler: (url) => {
        if (url.includes("/training?")) throw new Error("network");
        if (url.includes("/evaluations?"))
          return {
            availability: "available",
            items: [
              {
                artifact_id: id("3"),
                kind: "offline_evaluation",
                metadata: { records: 14 },
                payload_bytes: 10,
              },
            ],
            total: 1,
          };
        return emptyList();
      },
    }),
    page = await env.render();
  assert.match(text(page), /暂时无法读取此目录/);
  assert.match(text(page), /离线评估/);
  assert.match(text(page), /暂无已索引记录/);
  assert.equal(env.calls.length, 3);
});

test("a loaded service flag without a running Runtime observation cannot enable decisions", async () => {
  for (const runtime of [null, { lifecycle: "stopped", mode: "human" }]) {
    const env = setup({
      view: "local-models",
      handler: (url, options) =>
        url === "/api/local-models/status"
          ? { status: "loaded", loaded: true, runtime }
          : modelHandler(url, options),
    });
    const page = await env.render();
    assert.equal(action(page, "model-command-one_step").disabled, true);
    assert.equal(action(page, "model-command-human").disabled, false);
  }
});

test("persisted older activity is shown as the selected collection without activity authoring", async () => {
  const env = setup({view: "campaigns", handler: collectionHandler({items: [enrollment]})});
  const page = await env.render();
  assert.match(text(page), /授权已保存/);
  assert.doesNotMatch(text(page), /发布活动|专题活动/);
  assert.equal(post(env.calls).length, 0);
});

test("in-page project navigation preserves selected export identities and leaves file downloads untouched", async () => {
  const artifact = id("5");
  const env = setup({
    view: "research",
    handler: (url) =>
      url.includes("/training?")
        ? {
            availability: "available",
            items: [{ artifact_id: artifact, kind: "training_input" }],
            total: 1,
          }
        : url.endsWith("/download-status")
          ? { status: "idle" }
          : emptyList(),
  });
  const transitions = [];
  env.ui.navigate = (view, identity) => {
    transitions.push({ view, identity });
    env.navigate(view, identity ? "&id=" + identity : "");
  };
  let page = await env.render();
  await action(page, "research-export-" + artifact).onclick();
  const link = find(
    page,
    (node) => node.tag === "a" && node.textContent === "打开数据下载",
  );
  let prevented = false;
  assert.equal(typeof link.onclick, "function");
  link.onclick({
    button: 0,
    preventDefault() {
      prevented = true;
    },
  });
  assert.equal(prevented, true);
  assert.deepEqual(transitions, [{ view: "downloads", identity: null }]);
  page = await env.render();
  assert.match(text(page), /1 个产物/);
  const other = setup({
    mode: "cloud",
    view: "downloads",
    query: "&id=" + id("c"),
    handler: () => exportManifest,
  });
  page = await other.render();
  assert.equal(
    find(page, (node) => node.textContent === "下载此文件").onclick,
    undefined,
  );
});

const defaultTemplate = {
  template_id: id("b"), template: {...template.template, schema: "stpd/collection-activity-v2",
    name: "日常录制", game: undefined},
};
function collectionHandler({items = [], binding = "not_checked", paused = false} = {}) {
  let entries = items;
  return (url, options) => {
    if (url.endsWith("/collection-flow")) return {
      schema: "stpd/local-collection-flow-v1", default: defaultTemplate, device_id: "this-pc",
      stage: entries.length ? "native_binding_required" : "consent_required",
      next_action: paused ? "resume_upload" : "prepare", consent_required: !entries.length,
      upload: {enabled: !paused, process: binding === "bound" && !paused ? "running" : "stopped"},
      enrollment: entries.length ? {...entries[0], preparation: {...preparation(true), native_binding: {status: binding, bound: binding === "bound"}}} : null,
    };
    if (url.endsWith("/collection-settings")) return settings(defaultTemplate);
    if (url.endsWith("/consent")) { entries = [enrollment]; return {schema: "stpd/local-collection-flow-v1", enrollment}; }
    if (url.endsWith("/prepare")) return {stage: "native_binding_required"};
    if (url.endsWith("/upload")) return {upload: {enabled: JSON.parse(options.body).enabled}};
    return emptyList();
  };
}

test("one deliberate collection button saves consent then prepares only this computer", async () => {
  const env = setup({view: "campaigns", handler: collectionHandler()});
  let page = await env.render();
  assert.equal(post(env.calls).length, 0);
  assert.equal(walk(page).some(x => x.type === "checkbox"), false);
  assert.doesNotMatch(text(page), /发布活动|专题活动/);
  assert.match(text(page), /项目成员/);
  await action(page, "enroll-" + id("b")).onclick();
  assert.deepEqual(post(env.calls).map(x => x.url), ["/api/member/collection-flow/consent", "/api/member/collection-flow/prepare"]);
  assert.deepEqual(body(post(env.calls)[0]), {template_id: id("b"), accepted: true});
  page = await env.render();
  assert.match(text(page), /授权已保存/);
  assert.equal(walk(page).some(x => x.dataset?.action?.startsWith("enroll-")), false);
});

test("consent identity mismatch never starts preparation", async () => {
  const base = collectionHandler();
  const env = setup({view: "campaigns", handler: (url, options) => url.endsWith("/consent")
    ? {schema: "stpd/local-collection-flow-v1", enrollment: {...enrollment, device_id: "other"}} : base(url, options)});
  await action(await env.render(), "enroll-" + id("b")).onclick();
  assert.equal(post(env.calls).length, 1);
  assert.match(text(env.notice), /enrollment_identity_mismatch/);
});

test("fresh pages reuse saved consent and show native problems without claiming readiness", async () => {
  for (const binding of ["not_checked", "game_not_running", "mismatch", "blocked"]) {
    const env = setup({view: "campaigns", handler: collectionHandler({items: [enrollment], binding})});
    const page = await env.render();
    assert.match(text(page), /授权已保存/);
    assert.doesNotMatch(text(page), /已准备好/);
    assert.equal(post(env.calls).length, 0);
    await action(page, "prepare-collection").onclick();
    assert.equal(post(env.calls).length, 1);
    assert.equal(post(env.calls)[0].url, "/api/member/collection-flow/prepare");
  }
});

test("ready and paused upload states survive page reload without an implicit resume", async () => {
  for (const paused of [false, true]) {
    const env = setup({view: "campaigns", handler: collectionHandler({items: [enrollment], binding: "bound", paused})});
    const page = await env.render();
    assert.match(text(page), paused ? /自动上传已暂停/ : /已准备好/);
    assert.equal(post(env.calls).length, 0);
    await action(page, "toggle-collection-upload").onclick();
    assert.deepEqual(body(post(env.calls)[0]), {enabled: paused});
    assert.equal(post(env.calls)[0].url, "/api/member/collection-flow/upload");
  }
});

test("prepare accepts an explicit absolute game location and does not retry a lost response", async () => {
  const base = collectionHandler({items: [enrollment]});
  const env = setup({view: "campaigns", handler: (url, options) => {
    if (url.endsWith("/prepare")) throw new Error("lost reply");
    return base(url, options);
  }});
  const page = await env.render();
  field(page, "game_directory").value = "relative/path";
  await action(page, "prepare-collection").onclick();
  assert.equal(post(env.calls).length, 0);
  field(page, "game_directory").value = "/games/STS2";
  await action(page, "prepare-collection").onclick();
  assert.deepEqual(body(post(env.calls)[0]), {game_directory: "/games/STS2"});
  assert.equal(post(env.calls).length, 1);
  assert.match(text(env.notice), /不会自动重发/);
});

test("cloud administrator publishes typed daily defaults while member and local admin cannot", async () => {
  for (const [mode, role] of [["cloud", "member"], ["local", "admin"], ["cloud", "admin"]]) {
    const env = setup({mode, identity: owner(role), view: "campaigns", handler: collectionHandler()});
    const page = await env.render();
    const controls = walk(page).filter(item => item.dataset?.action === "save-default-collection");
    assert.equal(controls.length, mode === "cloud" && role === "admin" ? 1 : 0);
    if (controls.length) {
      field(page, "default_name").value = "Everyday capture";
      field(page, "default_description").value = "Only new sessions";
      field(page, "default_consent_text").value = "Member reviewed grant";
      await controls[0].onclick();
      assert.deepEqual(body(post(env.calls)[0]), {
        name: "Everyday capture", description: "Only new sessions", consent_text: "Member reviewed grant",
        csrf_token: "local-csrf",
      });
      assert.equal(post(env.calls)[0].url, "/app/api/admin/collection-settings");
    }
    if (mode === "cloud") {
      assert.equal(env.calls.some(call => call.url.startsWith("/api/")), false);
      assert.equal(walk(page).some(item => item.name === "game_directory"), false);
    }
  }
});

test("foreign device readback cannot offer collection preparation", async () => {
  const base = collectionHandler({items: [enrollment]});
  const env = setup({view: "campaigns", handler: (url, options) => {
    const value = base(url, options); return url.endsWith("/collection-flow") ? {...value, device_id: "other"} : value;
  }});
  const page = await env.render();
  assert.match(text(page), /collection_status_identity_mismatch/);
  assert.equal(post(env.calls).length, 0);
  assert.equal(walk(page).some(x => x.dataset?.action === "prepare-collection"), false);
});

test("changed recommendation keeps the saved local enrollment and unknown state remains unknown", async () => {
  const base = collectionHandler({items: [enrollment]});
  const env = setup({view: "campaigns", handler: (url, options) => {
    const value = base(url, options);
    return url.endsWith("/collection-flow") ? {...value, stage: "unavailable", default: {...defaultTemplate, template_id: id("c")},
      enrollment: {...enrollment, preparation: {status: "unavailable"}}} : value;
  }});
  const page = await env.render();
  assert.match(text(page), /不会改写这份授权/);
  assert.doesNotMatch(text(page), /已准备好/);
  assert.equal(walk(page).some(x => x.dataset?.action === "prepare-collection" || x.dataset?.action?.startsWith("enroll-")), false);
  assert.equal(post(env.calls).length, 0);
});

test("decision dataset defaults preview selected uploads without complete-run restriction", async () => {
  const h = setup({view: "datasets", handler: async (url) => {
    if (url.includes("collections?")) return {items: [{upload_id: uploadId, status: "verified"}], total: 1};
    return {items: []};
  }});
  await action(await h.render(), "dataset-tab-create").onclick();
  const page = await h.render();
  const source = field(page, `source-${uploadId}`);
  source.checked = true; source.onchange();
  await action(page, "preview-dataset").onclick();
  const call = h.calls.find(c => c.options?.method === "POST");
  assert.ok(call);
  const body = JSON.parse(call.options.body);
  assert.equal(body.preview_id, null);
  assert.equal(body.rules.complete_only, false);
  assert.equal(body.rules.wins_only, false);
  assert.equal(body.rules.no_failures_only, false);
  assert.deepEqual(body.uploads, [uploadId]);
  assert.deepEqual(body.curation, {purpose:"training",paired_training:null});
});

test("test selection keeps the paired training identity and Gold has no ordinary download", async () => {
  const env = setup({view:"datasets", handler: async url => {
    if (url.includes("collections?")) return {items:[{upload_id:uploadId,status:"verified"}],total:1};
    if (url.includes("datasets?")) return {items:[
      {artifact_id:id("a"),metadata:{schema:"stpd/curated-decision-dataset-v1",purpose:"training",materialization:"on_demand"}},
      {artifact_id:id("b"),metadata:{schema:"stpd/curated-decision-dataset-v1",purpose:"gold",materialization:"on_demand"}},
    ],total:2};
    return {id:uploadId,items:[]};
  }});
  const library = await env.render();
  assert.equal(action(library, `download-dataset-${id("b")}`).disabled, true);
  await action(library, "dataset-tab-create").onclick();
  const page = await env.render();
  const purpose = field(page,"dataset-purpose"); purpose.value="test"; purpose.onchange();
  const paired = field(page,"paired-training"); paired.value=id("a"); paired.oninput();
  const source = field(page,`source-${uploadId}`); source.checked=true; source.onchange();
  await action(page,"preview-dataset").onclick();
  assert.deepEqual(body(post(env.calls)[0]).curation,{purpose:"test",paired_training:id("a")});
});

test("quality annotation saves an immutable operation identity and reason", async () => {
  const env = setup({view:"record-quality",query:`&id=${uploadId}`,handler:async () => ({
    items:[{id:id("a"),sequence:7,run:"run",family:"play_card",action:{kind:"play_card"},annotations:[]}],total:1,
  })});
  const page=await env.render();
  field(page,`reason-${id("a")}`).value="点错了";
  field(page,`quality-${id("a")}`).value="exclude";
  await action(page,`annotate-${id("a")}`).onclick();
  assert.deepEqual(body(post(env.calls)[0]),{upload_id:uploadId,occurrence:id("a"),action:"exclude",reason:"点错了"});
  assert.ok(post(env.calls)[0].url.endsWith("/quality-annotations"));
});

test("game page reads derived summaries without submitting work", async () => {
  const h = setup({view: "games", handler: async () => ({items: [], profile_limit: 100, pending_profiles: 1, failed_profiles: 0})});
  const page = await h.render();
  assert.match(text(page), /上传次数不等于独立局数/);
  assert.equal(h.calls.length, 1);
  assert.notEqual(h.calls[0].options?.method, "POST");
});


test("dataset editing uses the shared refresh guard and retains unblurred draft input", async () => {
  const h = setup({view: "datasets", handler: async (url) => {
    if (url.includes("collections?")) return {items: [{upload_id: uploadId, status: "verified"}], total: 1};
    return {items: []};
  }});
  await action(await h.render(), "dataset-tab-create").onclick();
  const page = await h.render();
  assert.ok(walk(page).some(item => item.dataset?.projectEditor === "decision-dataset"));
  const name = field(page, "dataset-name");
  name.value = "Unblurred draft"; name.oninput();
  const source = field(page, `source-${uploadId}`);
  source.checked = true; source.onchange();
  const refreshed = await h.render();
  assert.equal(field(refreshed, "dataset-name").value, "Unblurred draft");
  assert.equal(field(refreshed, `source-${uploadId}`).checked, true);
  assert.equal(post(h.calls).length, 0);
});


test("signed-out or offline collector can pause its uploader without cloud enrollment", async () => {
  for (const signedOut of [true, false]) {
    const env = setup({ view: "campaigns", identity: signedOut ? {status:"signed_out", device_id:"this-pc"} : owner(), handler: (url, options) => {
      if (options.method === "POST") return {stage:"upload_paused"};
      assert.equal(url, "/api/member/collection-flow");
      return {schema:"stpd/local-collection-flow-v1", device_id:"this-pc", enrollment:null,
        stage:"unavailable", next_action:"reconnect", error:"hub_unavailable", upload:{enabled:true,process:"running"}};
    }});
    const page = await env.render();
    assert.equal(post(env.calls).length, 0);
    await action(page, "toggle-collection-upload").onclick();
    assert.deepEqual(body(post(env.calls)[0]), {enabled:false});
    assert.equal(post(env.calls)[0].url, "/api/member/collection-flow/upload");
  }
});

test("evaluation sharing requires its own explicit action and valid verified report", async () => {
  const env = setup({view:"evaluations", handler: (url, options) => {
    if (url === "/api/local-models") return {evaluations:[{evaluation_id:id("a"), evidence_verification:"pass", game_outcome:"not_measured"}]};
    if (url.endsWith("share-status")) return {status:"idle"};
    return options.method === "POST" ? {status:"preparing"} : emptyList();
  }});
  const page = await env.render();
  assert.equal(post(env.calls).length, 0);
  assert.match(text(page), /不会作为真人采集数据/);
  await action(page, `share-evaluation-${id("a")}`).onclick();
  assert.deepEqual(body(post(env.calls)[0]), {evaluation_id:id("a"), authorized:true});
});


test("dataset union explains an insufficient selection without submitting a job", async () => {
  const env = setup({view: "datasets", handler: (url) =>
    url.includes("/datasets?") ? {items: [{artifact_id: id("a")}]} : emptyList()
  });
  const page = await env.render();
  const merge = action(page, "preview-dataset-merge");
  await merge.onclick();
  assert.match(text(env.notice), /请选择至少两个决策数据集后再合并/);
  assert.doesNotMatch(text(env.notice), /服务暂时不可用/);
  const selected = field(page, `merge-${id("a")}`);
  selected.checked = true;
  selected.onchange();
  await merge.onclick();
  assert.match(text(env.notice), /请选择至少两个决策数据集后再合并/);
  assert.equal(post(env.calls).length, 0);
});

test("dataset progress stays visible and selected parent union is exact", async () => {
  const rules = {schema:"stpd/decision-selection-v1",complete_only:false,wins_only:false,no_failures_only:false,filters:{},seed:0};
  const job = {id:"b".repeat(32),state:"completed",request:{name:"union",datasets:[id("a"),id("b")],rules,curation:{purpose:"training",paired_training:null},preview_id:null},progress:{phase:"completed",completed:2,total:2,elapsed_seconds:1.5},result:{selected:7,exact_duplicate_decisions:2,split_status:"grouped"}};
  const env = setup({view:"datasets",handler:(url, options) => {
    if (options.method === "POST") return {id:job.id,state:"pending"};
    if (url === `/api/member/datasets/${job.id}`) return job;
    if (url.startsWith("/api/member/datasets?")) return {items:[job]};
    if (url.includes("/datasets?")) return {items:[{artifact_id:id("a"),metadata:{schema:"stpd/decision-dataset-v1"}},{artifact_id:id("b"),metadata:{schema:"stpd/decision-dataset-v1"}}]};
    return emptyList();
  }});
  const page = await env.render();
  assert.doesNotMatch(text(page), /保留决策/);
  for (const identity of [id("a"),id("b")]) { const checkbox = field(page,`merge-${identity}`); checkbox.checked=true; checkbox.onchange(); }
  await action(page,"preview-dataset-merge").onclick();
  assert.deepEqual(body(post(env.calls)[0]).datasets,[id("a"),id("b")]);
  assert.equal(body(post(env.calls)[0]).uploads,undefined);
  const tasks = await env.render();
  assert.match(text(tasks), /保留决策/);
  await action(tasks,`build-${job.id}`).onclick();
  assert.deepEqual(body(post(env.calls)[1]),{name:"union",datasets:[id("a"),id("b")],rules,curation:job.request.curation,preview_id:job.id});
});


test("dataset library leads with names, counts, search and a complete export", async () => {
  const item = {artifact_id:id("a"),display_name:"Defeat collection",metadata:{records:814,schema:"stpd/decision-dataset-v1",split_status:"assigned"},payloads:[{role:"records"},{role:"selection"}]};
  const h = setup({view:"datasets",handler:(url, options) => options.method === "POST" ? {export_id:id("b")} : {items:[item],total:1}});
  let page = await h.render();
  assert.match(text(page), /Defeat collection/); assert.match(text(page), /814/);
  assert.equal(walk(page).some(x => x.name === "dataset-name"), false);
  const search = field(page,"dataset-search"); search.value="Defeat"; search.oninput();
  await action(page,"search-datasets").onclick(); page=await h.render();
  assert.ok(h.calls.some(c => c.url.includes("q=Defeat")));
  await action(page,`download-dataset-${id("a")}`).onclick();
  assert.deepEqual(body(post(h.calls)[0]).artifacts,[{artifact_id:id("a"),roles:["records","selection"]}]);
});

test("preview removal and restore are explicit and never delete artifacts", async () => {
  const job={id:uploadId,state:"completed",request:{name:"draft",preview_id:null},result:{selected:12}};
  const h=setup({view:"datasets",handler: url => url.includes("/member/datasets") ? {items:[job]} : emptyList()});
  await action(await h.render(),"dataset-tab-previews").onclick();
  const page=await h.render();
  const report=find(page,x=>x.tag==="details" && x.dataset?.preserve===`dataset-report-${uploadId}`);
  assert.equal(report.open,undefined);
  assert.equal(post(h.calls).length,0);
  await action(page,`visibility-${uploadId}`).onclick();
  assert.deepEqual(body(post(h.calls)[0]),{ids:[uploadId],archived:true});
  await action(await h.render(),"dataset-tab-archived").onclick();
  await action(await h.render(),`visibility-${uploadId}`).onclick();
  assert.deepEqual(body(post(h.calls)[1]),{ids:[uploadId],archived:false});
});


test("returning from a dataset detail opens the library after preview browsing", async () => {
  const h = setup({view:"datasets"});
  const navigations = [];
  h.ui.navigate = (...args) => navigations.push(args);
  await action(await h.render(), "dataset-tab-previews").onclick();
  assert.equal(action(await h.render(), "dataset-tab-previews").attributes["aria-current"], "page");
  h.ui.openDatasetLibrary();
  assert.deepEqual(navigations, [["datasets"]]);
  assert.equal(action(await h.render(), "dataset-tab-library").attributes["aria-current"], "page");
});

for (const change of ["account", "page", "detail"]) test(`delayed dataset download ignores changed ${change}`, async () => {
  let resolve;
  const waiting = new Promise(done => { resolve = done; });
  const item = {artifact_id:id("a"),metadata:{},payloads:[{role:"records"}]};
  const h = setup({view:"datasets",handler:(url, options) => options.method === "POST" ? waiting : {items:[item]}});
  const navigations = [];
  h.ui.navigate = (...args) => navigations.push(args);
  const pending = action(await h.render(), `download-dataset-${id("a")}`).onclick();
  if (change === "account") h.account(owner("member", "another"));
  else h.navigate(change === "detail" ? "datasets" : "statistics", change === "detail" ? "&id=" + id("a") : "");
  if (change !== "detail") await h.render();
  resolve({export_id:id("b")});
  await pending;
  assert.deepEqual(navigations, []);
});

test("dataset download rejects an invalid export identity before navigating", async () => {
  const item = {artifact_id:id("a"),metadata:{},payloads:[{role:"records"}]};
  const h = setup({view:"datasets",handler:(url, options) => options.method === "POST" ? {export_id:"invalid"} : {items:[item]}});
  const navigations = [];
  h.ui.navigate = (...args) => navigations.push(args);
  await action(await h.render(), `download-dataset-${id("a")}`).onclick();
  assert.deepEqual(navigations, []);
  assert.match(text(h.notice), /invalid_export_identity/);
});


test("dataset without file inventory does not silently export only a manifest", async () => {
  const h = setup({view:"datasets",handler:() => ({items:[{artifact_id:id("a"),metadata:{}}]})});
  await action(await h.render(), `download-dataset-${id("a")}`).onclick();
  assert.equal(post(h.calls).length, 0);
  assert.match(text(h.notice), /尚未提供文件清单/);
});


test("dataset date range uses local inclusive days and select-all reaches beyond current page", async () => {
  const ids = Array.from({length:30},(_,i)=>i.toString(16).padStart(32,"0"));
  const h = setup({view:"datasets", handler:url => {
    if (!url.includes("collections?")) return emptyList();
    const query = new URL(url,"https://example.test").searchParams;
    return {items:ids.slice(0,Number(query.get("limit"))).map(upload_id=>({upload_id,status:"verified"})),total:30,next_offset:query.get("limit")==="25"?25:null};
  }});
  await action(await h.render(),"dataset-tab-create").onclick();
  let page = await h.render();
  field(page,"source-from").value="2026-09-15"; field(page,"source-from").oninput();
  field(page,"source-to").value="2026-09-16"; field(page,"source-to").oninput();
  await action(page,"filter-dataset-sources").onclick(); page=await h.render();
  await action(page,"select-all-dataset-sources").onclick();
  assert.match(text(page),/已选择 30 份/);
  const q = new URL(h.calls.at(-1).url,"https://example.test").searchParams;
  assert.equal(q.get("selectable"),"true"); assert.equal(q.get("limit"),"100");
  assert.equal(Number(q.get("from")),new Date(2026,8,15).getTime()/1000);
  assert.equal(Number(q.get("to")),new Date(2026,8,17).getTime()/1000);
  await action(page,"dataset-tab-library").onclick();
  await action(await h.render(),"dataset-tab-create").onclick(); page=await h.render();
  assert.equal(field(page,"source-to").value,"2026-09-16");
  assert.match(text(page),/已选择 30 份/);
  await action(page,"clear-dataset-selection").onclick(); assert.match(text(page),/已选择 0 份/);
  await action(page,"preview-dataset").onclick();
  assert.equal(post(h.calls).length,0); assert.match(text(h.notice),/先选择至少一份/);
});

test("over-limit select-all leaves existing selection unchanged and invalid dates make no request", async () => {
  const h = setup({view:"datasets",handler:url => url.includes("collections?") ? {items:[{upload_id:uploadId,status:"verified"}],total:101,next_offset:100} : emptyList()});
  await action(await h.render(),"dataset-tab-create").onclick(); const page=await h.render();
  field(page,`source-${uploadId}`).checked=true; field(page,`source-${uploadId}`).onchange();
  await action(page,"select-all-dataset-sources").onclick();
  assert.match(text(h.notice),/最多选择 100/); assert.match(text(page),/已选择 1 份/);
  field(page,"source-from").value="2026-09-17"; field(page,"source-from").oninput();
  field(page,"source-to").value="2026-09-16"; field(page,"source-to").oninput();
  const before=h.calls.length; await action(page,"filter-dataset-sources").onclick();
  assert.equal(h.calls.length,before); assert.match(text(h.notice),/结束日期不能早于/);
});

test("dataset shell selects a new tab before delayed data arrives and ignores older responses", async () => {
  let finish; const pending=new Promise(resolve=>{finish=resolve;});
  const h=setup({view:"datasets",handler:url=>url.includes("/datasets?") && !url.includes("/member/") ? pending : emptyList()});
  let shell; const old=h.render(value=>{shell=value;});
  assert.equal(action(shell,"dataset-tab-library").attributes["aria-current"],"page");
  assert.match(text(shell),/正在读取/);
  await action(shell,"dataset-tab-archived").onclick();
  let currentShell; const current=h.render(value=>{currentShell=value;});
  assert.equal(action(currentShell,"dataset-tab-archived").attributes["aria-current"],"page");
  await current; finish({items:[{artifact_id:id("c"),display_name:"old private result"}],total:1}); await old;
  assert.doesNotMatch(text(shell),/old private result/);
  assert.match(text(currentShell),/没有已移除/);
});

test("late select-all does not undo an explicit clear or changed date range", async () => {
  for(const change of ["clear","date"]) {
    let finish; const pending=new Promise(resolve=>{finish=resolve;});
    const h=setup({view:"datasets",handler:url=>url.includes("collections?")&&url.includes("limit=100")?pending:emptyList()});
    await action(await h.render(),"dataset-tab-create").onclick(); const page=await h.render();
    const selecting=action(page,"select-all-dataset-sources").onclick();
    if(change==="clear") await action(page,"clear-dataset-selection").onclick();
    else {field(page,"source-from").value="2026-09-16";field(page,"source-from").oninput();}
    finish({items:[{upload_id:uploadId,status:"verified"}],total:1,next_offset:null}); await selecting;
    assert.match(text(page),/已选择 0 份/);
  }
});

test("new preview follows its exact task and failed choices can be edited", async () => {
  const rules={complete_only:false,wins_only:false,filters:{character:["defect"]}};
  const job={id:uploadId,state:"failed",error:"environment_identity_conflict",request:{name:"My recording selection",uploads:[uploadId],rules,preview_id:null}};
  const h=setup({view:"datasets",handler:(url,options)=> options.method==="POST"?{id:uploadId}:url.endsWith(`/datasets/${uploadId}`)?job:url.includes("collections?")?{items:[{upload_id:uploadId,status:"verified"}],total:1}:emptyList()});
  await action(await h.render(),"dataset-tab-create").onclick(); let page=await h.render();
  field(page,`source-${uploadId}`).checked=true;field(page,`source-${uploadId}`).onchange();
  await action(page,"preview-dataset").onclick(); page=await h.render();
  assert.match(text(page),/环境身份存在冲突/);
  assert.ok(h.calls.some(c=>c.url.endsWith(`/datasets/${uploadId}`)));
  await action(page,`edit-dataset-${uploadId}`).onclick(); page=await h.render();
  assert.equal(field(page,"dataset-name").value,"My recording selection");
  assert.equal(field(page,`source-${uploadId}`).checked,true);
  assert.equal(field(page,"character").value,"defect");
});


test("a slow navigation does not disable returning to that tab", async () => {
  let finish; const waiting=new Promise(resolve=>{finish=resolve;});
  const h=setup({view:"datasets"}); const initial=await h.render();
  h.ui.reload=()=>waiting;
  const navigating=action(initial,"dataset-tab-create").onclick();
  let shell; const next=h.render(value=>{shell=value;});
  assert.equal(action(shell,"dataset-tab-create").disabled,false);
  await next; finish(); await navigating;
});

test("retry follows the new task instead of keeping the focused failed preview", async () => {
  const nextId="e".repeat(32);
  const failed={id:uploadId,state:"failed",error:"environment_identity_conflict",request:{name:"selection",uploads:[uploadId],rules:{},preview_id:null}};
  const next={...failed,id:nextId,state:"pending",error:null};
  const h=setup({view:"datasets",handler:(url,options)=> {
    if(options.method==="POST") return {id:url.endsWith("/retry")?nextId:uploadId};
    if(url.endsWith(`/datasets/${uploadId}`)) return failed;
    if(url.endsWith(`/datasets/${nextId}`)) return next;
    if(url.includes("collections?")) return {items:[{upload_id:uploadId,status:"verified"}],total:1};
    return emptyList();
  }});
  await action(await h.render(),"dataset-tab-create").onclick(); let page=await h.render();
  field(page,`source-${uploadId}`).checked=true;field(page,`source-${uploadId}`).onchange();
  await action(page,"preview-dataset").onclick(); page=await h.render();
  await action(page,`retry-dataset-${uploadId}`).onclick(); page=await h.render();
  assert.ok(h.calls.some(c=>c.url.endsWith(`/datasets/${nextId}`)));
  assert.doesNotMatch(text(page),/环境身份存在冲突/);
});

test("dataset background refresh retains unchanged cards and keeps navigation usable", async () => {
  let delayed = false, finish;
  const job = {id:uploadId,state:"pending",request:{name:"durable work",uploads:[uploadId],rules:{},preview_id:null}};
  const h = setup({view:"datasets",handler:url => {
    if(url.includes("/member/datasets?")) return delayed ? new Promise(resolve => {finish=resolve;}) : {items:[job],total:1};
    return emptyList();
  }});
  await action(await h.render(),"dataset-tab-previews").onclick();
  const page = await h.render(), card = find(page, node => node.dataset.refreshKey === uploadId);
  assert.equal(await h.ui.refresh("datasets"), true);
  assert.equal(find(page,node => node.dataset.refreshKey === uploadId),card);
  delayed = true; const refreshing = h.ui.refresh("datasets");
  await action(page,"dataset-tab-create").onclick();
  const next = await h.render();
  finish({items:[],total:0}); await refreshing;
  assert.equal(action(next,"dataset-tab-create").attributes["aria-current"],"page");
  assert.equal(find(page,node => node.dataset.refreshKey === uploadId),card);
  const calls = h.calls.length;
  field(next,"dataset-name").value = "unfinished draft";
  assert.equal(await h.ui.refresh("datasets"),true);
  assert.equal(h.calls.length,calls);
  assert.equal(field(next,"dataset-name").value,"unfinished draft");
});

test("background refresh updates changed task cards without a loading shell", async () => {
  let state = "pending";
  const h=setup({view:"datasets",handler:url=>url.includes("/member/datasets?") ? {
    items:[{id:uploadId,state,request:{name:"task",uploads:[uploadId],rules:{},preview_id:null}}],total:1,
  }:emptyList()});
  await action(await h.render(),"dataset-tab-previews").onclick();
  const page=await h.render(), card=find(page,node=>node.dataset.refreshKey===uploadId);
  // This fixture has no details; the production branch also preserves open details by key.
  card.querySelectorAll=()=>[];
  const make=h.context.document.createElement;
  h.context.document.createElement=tag=>{const n=make(tag);n.querySelectorAll=()=>[];return n;};
  state="running"; await h.ui.refresh("datasets");
  assert.notEqual(find(page,node=>node.dataset.refreshKey===uploadId),card);
  assert.match(text(page),/正在处理/);
  assert.doesNotMatch(text(page),/正在读取/);
});

test("background denial removes previously displayed private dataset data", async () => {
  let denied=false;
  const h=setup({view:"datasets",handler:()=>denied?{httpStatus:403}:{items:[{artifact_id:id("b"),display_name:"private dataset"}],total:1}});
  const page=await h.render(); assert.match(text(page),/private dataset/);
  denied=true; await h.ui.refresh("datasets");
  assert.doesNotMatch(text(page),/private dataset/);
  assert.match(text(page),/需要重新登录/);
});


test("legacy preview refresh creates a new selection instead of incompatible confirmation", async () => {
  const job = {id:"b".repeat(32),state:"completed",request:{name:"old",uploads:[uploadId],rules:{seed:0},preview_id:null},result:{selected:7}};
  const h = setup({view:"datasets",handler:(url, options) => {
    if (options.method === "POST") return {id:"c".repeat(32),state:"pending"};
    if (url.startsWith("/api/member/datasets?")) return {items:[job]};
    return emptyList();
  }});
  await action(await h.render(),"dataset-tab-previews").onclick();
  const button = action(await h.render(),`build-${job.id}`);
  assert.match(button.textContent,/按新规则重新预览/);
  await button.onclick();
  assert.deepEqual(body(post(h.calls)[0]),{name:"old",uploads:[uploadId],rules:{seed:0},preview_id:null});
});

test("research archives preserve project scope and restore exact artifact identities", async () => {
  const artifact = id("6");
  const env = setup({view:"research", handler: url => url.includes("/training?")
    ? {...emptyList(),items:[{artifact_id:artifact,kind:"training_input"}],total:1}
    : emptyList()});
  env.scope("other-pc");
  let page = await env.render();
  await action(page,`research-visibility-${artifact}`).onclick();
  assert.deepEqual(body(post(env.calls)[0]),{ids:[artifact],archived:true});
  await action(page,"research-archive-view").onclick();
  page = await env.render();
  assert.ok(env.calls.some(c => c.url.includes("archived=true") && c.url.includes("device=other-pc")));
  assert.match(text(page), /恢复显示/);
  await action(page,`research-visibility-${artifact}`).onclick();
  assert.deepEqual(body(post(env.calls).at(-1)),{ids:[artifact],archived:false});
});


test("runtime environment rejection is visible and cannot be blindly restarted", async () => {
  const env = setup({ view: "local-models", handler: (url, options) =>
    url === "/api/local-models/status" ? {
      status: "loaded", loaded: true,
      runtime: { lifecycle: "running", mode: "human", controller: "released",
        errors: ["environment_modset_fingerprint_drift"], last_receipt: null }
    } : modelHandler(url, options) });
  const page = await env.render();
  assert.match(text(page), /游戏环境与模型绑定不一致/);
  assert.match(text(page), /尚无游戏动作送达记录/);
  assert.equal(action(page, "model-command-auto").disabled, true);
  assert.equal(action(page, "model-command-stop").disabled, false);
});
