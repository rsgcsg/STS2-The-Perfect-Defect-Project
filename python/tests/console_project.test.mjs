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
  addEventListener() {}
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
const textMenuScratchModel = (artifactId = id("a"), recipe = "stage1a.b.s.v2") => ({
  artifact_id: artifactId,
  kind: "model",
  parameters: {
    schema: "stpd/stage1a-model-v1",
    config: {recipe, steps:3, device:"cpu"},
    backbone: {kind:"scratch"},
    serializer: {
      version:"stpd-text-menu-current-page-v1",
      profile:"text_menu_current_page",
      source_schema:"sts2.player-environment/text-menu-snapshot-1",
      input_profile:"text-menu-v1",
      status:"provisional",
    },
    steps:3,
    qualification:"engineering_only",
  },
  parents:[],
  payloads:[],
});
const memoryModel = (artifactId = id("a")) => ({
  artifact_id:artifactId, kind:"model",
  parameters:{schema:"stpd/experimental-m2-model-v1", partition:"train",
    qualification:"engineering_only", episodes:1,
    config:{slots:1, reset_each_step:false}},
  parents:[], payloads:[],
});
const modelExportStatus = (operation, extra = {}) => ({
  schema:"stpd/local-model-export-operation-v1",
  availability:"ready",
  operation,
  csrf_token:"export-csrf",
  ...extra,
});
const modelRegistrationStatus = (model, status = "not_registered", extra = {}) => ({
  schema:"stpd/local-model-registration-v1",
  model_id:model,
  status,
  loaded:false,
  runtime_profile:"text-menu-v1",
  csrf_token:"registration-csrf",
  ...extra,
});
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
  importStatus = {schema: "stpd/local-recording-import-operation-v1", status: "idle", csrf_token: "browser-csrf"},
  curationStatus = {schema: "stpd/local-curation-preparation-v1", status: "not_applicable"},
  query = "",
  renderOnReload = false,
} = {}) {
  const calls = [],
    notice = new Element("div"),
    confirms = [];
  let selectedScope = mode === "local" ? "local" : "project",
    generation = 0,
    reloads = 0,
    livePage = null;
  const timers = new Map();
  let nextTimer = 1;
  const context = vm.createContext({
    setTimeout: (callback) => {
      const id = nextTimer++;
      timers.set(id, callback);
      return id;
    },
    clearTimeout: (id) => timers.delete(id),
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
      const body = url === "/api/local-recordings/import/status"
        ? importStatus
        : url === "/api/local-workspace/curation"
          ? curationStatus : await handler(url, options);
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
    if (renderOnReload) livePage = await ui.render(view, identity);
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
    render: async (mount) => {
      const page = await ui.render(view, identity, mount);
      if (renderOnReload) livePage = page;
      return page;
    },
    get livePage() { return livePage; },
    get timerCount() { return timers.size; },
    advanceTimer: async () => {
      const first = timers.entries().next().value;
      assert.ok(first, "expected scheduled status read");
      timers.delete(first[0]);
      await first[1]();
    },
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
function localDatasetEnv({artifact = id("a"), sampleStatus = null, datasetStatus = null,
  managedStatus = null, curationStatus = undefined, datasetHandler = () => {}} = {}) {
  return setup({
    identity: {status: "signed_out"}, view: "local-workspace", query: `&id=${artifact}`,
    ...(curationStatus ? {curationStatus} : {}),
    handler: async (url, options) => {
      if (url === "/api/local-workspace/managed") return managedStatus || {
        schema: "stpd/managed-local-workspace-registration-v1", status: "ready",
        workspace_id: "c".repeat(32), curation_status: "ready",
      };
      if (url === `/api/local-workspace/artifacts/${artifact}`) return {
        kind: "evidence", artifact_id: artifact,
        parameters: {schema: "stpd/local-verified-bundle-v1"},
      };
      if (url === "/api/local-recordings/preview/status") return sampleStatus || {
        status: "completed", artifact_id: artifact, availability: "available",
        human_input_labels: 9, canonical_decisions: 3, human_input_total: 9,
        human_input_exclusions: {}, decision_exclusions: {}, run_ids_observed: 1,
        independent_run_qualification: "unknown", csrf_token: "dataset-csrf",
      };
      if (url === "/api/local-datasets/status") {
        const value = typeof datasetStatus === "function" ? datasetStatus() : datasetStatus;
        return value || {
          schema: "stpd/local-dataset-operation-v1", availability: "ready",
          paired_training: [{artifact_id: id("b"), records: 5}],
          operation: {status: "idle"}, csrf_token: "dataset-csrf",
        };
      }
      return datasetHandler(url, options);
    },
  });
}
function localTrainingEnv({artifact = id("a"), kind = "dataset", parameters = null,
  trainingStatus = null, trainingHandler = () => {}} = {}) {
  return setup({
    identity: {status:"signed_out"}, view:"local-workspace", query:`&id=${artifact}`,
    curationStatus:{schema:"stpd/local-curation-preparation-v1", status:"ready"},
    handler:async (url, options) => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      if (url === `/api/local-workspace/artifacts/${artifact}`) return {
        artifact_id:artifact, kind,
        parameters:parameters || {schema:"stpd/curated-decision-dataset-v1", purpose:"training", records:5},
      };
      if (url === "/api/local-training/status") return typeof trainingStatus === "function"
        ? trainingStatus(url, options)
        : trainingStatus || {
          schema:"stpd/local-training-operation-v1", availability:"ready", csrf_token:"training-csrf",
          operation:{status:"idle"},
        };
      return trainingHandler(url, options);
    },
  });
}
function localHumanDatasetEnv({items, total = null, operation = {status:"idle"}, detailId = null,
  binding = null, trainingStatus = null, onWrite = () => {}} = {}) {
  const sources = items || [{artifact_id:id("a"), kind:"evidence", parameters:{schema:"stpd/local-verified-bundle-v1"}}];
  return setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:detailId ? `&id=${detailId}` : "",
    curationStatus:{schema:"stpd/local-curation-preparation-v1", status:"ready"},
    handler:async (url, options) => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      if (url === "/api/local-recordings/import/status") return {status:"idle"};
      if (url.startsWith("/api/local-workspace?") || url === "/api/local-workspace") {
        const listed = typeof sources === "function" ? sources(url) : sources;
        return {items:listed, total:total ?? listed.length, status:"ready"};
      }
      if (url === "/api/local-datasets/status") return {
        schema:"stpd/local-dataset-operation-v1", availability:"ready", csrf_token:"human-csrf",
        operation:typeof operation === "function" ? operation() : operation,
      };
      if (detailId && url === `/api/local-workspace/artifacts/${detailId}`) return {
        artifact_id:detailId, kind:"dataset", parameters:{schema:"stpd/human-text-input-source-v1"},
      };
      if (detailId && url === `/api/local-datasets/binding/${detailId}`)
        return typeof binding === "function" ? binding() : binding;
      if (url === "/api/local-training/status") return trainingStatus || {
        schema:"stpd/local-training-operation-v1", availability:"ready", csrf_token:"train-csrf",
        operation:{status:"idle"},
      };
      if (options.method === "POST") return onWrite(url, options);
      throw new Error(`unexpected route ${url}`);
    },
  });
}
const post = (calls) => calls.filter((call) => call.options.method === "POST");
const body = (call) => JSON.parse(call.options.body);

test("local research workspace browses the local API without project identity", async () => {
  const artifactId = id("a");
  const env = setup({
    identity: {status: "signed_out"},
    view: "local-workspace",
    handler: async (url) => {
      if (url === "/api/local-workspace/managed") return {
        schema: "stpd/managed-local-workspace-registration-v1",
        status: "not_created",
        csrf_token: "session-csrf",
        orphaned_initializations: 0,
        requires_cloud_account: false,
      };
      if (url.startsWith("/api/local-workspace?")) return {
        schema: "stpd/local-workspace-inventory-v1",
        source: "configured_local_artifact_store",
        total: 1,
        items: [{
          artifact_id: artifactId,
          kind: "dataset",
          payloads: [{role: "records", sha256: id("b"), size: 24, media_type: "application/json"}],
          registry_indexed: true,
          registry_cached: false,
        }, {
          artifact_id: id("d"), kind: "evidence", payloads: [],
          registry_indexed: true, registry_cached: false,
        }],
      };
      throw new Error(`unexpected route ${url}`);
    },
  });
  const page = await env.render();
  assert.match(text(page), /本机资料/);
  assert.match(text(page), /数据集/);
  assert.match(text(page), /证据/);
  assert.match(text(page), /本机索引/);
  assert.equal(env.calls.length, 3);
  assert.equal(env.calls[0].url === "/api/local-workspace/managed", true);
  assert.equal(env.calls[1].url, "/api/local-recordings/import/status");
  assert.equal(env.calls[2].url.startsWith("/api/local-workspace?"), true);
  assert.equal(env.calls[0].options.method || "GET", "GET");
});

test("command pending is reconciled into a replacement render without overriding current eligibility", async () => {
  const source = id("a");
  const env = setup({
    identity:{status:"signed_out"}, view:"local-workspace", renderOnReload:true,
    handler:async url => {
      if (url === "/api/local-workspace/managed")
        return {status:"ready", workspace_id:id("c")};
      if (url === "/api/local-recordings/import/status") return {status:"idle"};
      if (url.startsWith("/api/local-workspace?"))
        return {schema:"stpd/local-workspace-inventory-v1", total:0, items:[]};
      throw new Error(`unexpected route ${url}`);
    },
  });
  let page = await env.render();
  field(page, "local-workspace-search").value = "new query";
  await action(page, "search-local-workspace").onclick();
  assert.ok(env.livePage, "reload performs a project render before the click resolves");
  assert.equal(action(env.livePage, "search-local-workspace").disabled, false,
    "the fresh search control is enabled after its GET and render finish");
  assert.equal(post(env.calls).length, 0);

  let previewStatus = {status:"completed", artifact_id:source, availability:"available",
    human_input_labels:2, canonical_decisions:0, human_input_total:2,
    human_input_exclusions:{}, decision_exclusions:{}, run_ids_observed:1};
  const previewEnv = setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${source}`,
    renderOnReload:true,
    handler:async (url, options) => {
      if (url === "/api/local-workspace/managed")
        return {status:"ready", workspace_id:id("c")};
      if (url === `/api/local-workspace/artifacts/${source}`)
        return {artifact_id:source, kind:"evidence", parameters:{schema:"stpd/local-verified-bundle-v1"}};
      if (url === "/api/local-recordings/preview/status")
        return {...previewStatus, csrf_token:"preview-csrf"};
      if (url === "/api/local-recordings/preview") {
        assert.equal(options.method, "POST");
        previewStatus = {status:"pending", artifact_id:source};
        return {status:"pending"};
      }
      throw new Error(`unexpected route ${url}`);
    },
  });
  page = await previewEnv.render();
  assert.equal(action(page, "preview-local-recording").disabled, false);
  await action(page, "preview-local-recording").onclick();
  assert.equal(post(previewEnv.calls).length, 1);
  assert.equal(action(previewEnv.livePage, "preview-local-recording").disabled, true,
    "the new page's backend-derived pending state stays disabled after the old action settles");

  const otherSource = id("b");
  let finishPost, postStarted;
  const dispatched = new Promise(resolve => { postStarted = resolve; });
  previewStatus = {status:"completed", artifact_id:source, availability:"available",
    human_input_labels:2, canonical_decisions:0, human_input_total:2,
    human_input_exclusions:{}, decision_exclusions:{}, run_ids_observed:1};
  const contextEnv = setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${source}`,
    renderOnReload:true,
    handler:async (url, options) => {
      if (url === "/api/local-workspace/managed")
        return {status:"ready", workspace_id:id("c")};
      const artifact = url.match(/^\/api\/local-workspace\/artifacts\/([a-f0-9]{64})$/);
      if (artifact) return {artifact_id:artifact[1], kind:"evidence",
        parameters:{schema:"stpd/local-verified-bundle-v1"}};
      if (url === "/api/local-recordings/preview/status")
        return {...previewStatus, csrf_token:"preview-csrf"};
      if (url === "/api/local-recordings/preview") {
        assert.equal(options.method, "POST");
        previewStatus = {status:"pending", artifact_id:source};
        postStarted();
        return new Promise(resolve => {
          finishPost = () => {
            previewStatus = {status:"completed", artifact_id:source, availability:"available",
              human_input_labels:2, canonical_decisions:0, human_input_total:2,
              human_input_exclusions:{}, decision_exclusions:{}, run_ids_observed:1};
            resolve({status:"completed"});
          };
        });
      }
      throw new Error(`unexpected route ${url}`);
    },
  });
  page = await contextEnv.render();
  const oldContextClick = action(page, "preview-local-recording").onclick();
  await dispatched;
  contextEnv.navigate("local-workspace", `&id=${otherSource}`);
  const otherPage = await contextEnv.render();
  assert.equal(action(otherPage, "preview-local-recording").disabled, true,
    "a new artifact remains blocked by its current shared pending DTO");
  finishPost();
  await oldContextClick;
  assert.equal(action(otherPage, "preview-local-recording").disabled, true,
    "the old context releases its client lock using the new page's own eligibility");

  let attempts = 0;
  const rejected = setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${source}`,
    handler:async (url, options) => {
      if (url === "/api/local-workspace/managed")
        return {status:"ready", workspace_id:id("c")};
      if (url === `/api/local-workspace/artifacts/${source}`)
        return {artifact_id:source, kind:"evidence", parameters:{schema:"stpd/local-verified-bundle-v1"}};
      if (url === "/api/local-recordings/preview/status")
        return {...previewStatus, csrf_token:"preview-csrf"};
      if (url === "/api/local-recordings/preview") {
        assert.equal(options.method, "POST");
        attempts++;
        return {httpStatus:409, error:"preview_unavailable"};
      }
      throw new Error(`unexpected route ${url}`);
    },
  });
  page = await rejected.render();
  const retry = action(page, "preview-local-recording");
  await retry.onclick();
  assert.equal(post(rejected.calls).length, 1, "a rejected POST is not automatically retried");
  assert.equal(retry.disabled, false, "a rejected promise releases the old control lock");
  await retry.onclick();
  assert.equal(attempts, 2, "a second POST requires another explicit user click");
});

test("local curation preparation is GET-only until one explicit empty-body POST", async () => {
  const env = setup({
    identity: {status: "signed_out"}, view: "local-workspace",
    curationStatus: {
      schema: "stpd/local-curation-preparation-v1", status: "preparation_required",
      legacy_dataset_count: 4, unknown_dataset_count: 4, csrf_token: "browser-csrf",
    },
    handler: async (url, options) => {
      if (url === "/api/local-workspace/managed") return {status: "ready", workspace_id: "c".repeat(32)};
      if (url === "/api/local-workspace?limit=25&offset=0") return {total: 0, items: []};
      if (url === "/api/local-workspace/curation/prepare") {
        assert.equal(options.method, "POST");
        return {status: "preparing"};
      }
      throw new Error(`unexpected route ${url}`);
    },
  });
  const page = await env.render();
  assert.match(text(page), /首次准备会在本机建立用途记录/);
  assert.match(text(page), /用途未知：4 份/);
  assert.doesNotMatch(text(page), /browser-csrf/);
  assert.equal(post(env.calls).length, 0, "GET/render must not prepare the ledger");
  await action(page, "prepare-local-curation").onclick();
  const writes = post(env.calls);
  assert.equal(writes.length, 1);
  assert.equal(writes[0].url, "/api/local-workspace/curation/prepare");
  assert.deepEqual(body(writes[0]), {});
  assert.equal(writes[0].options.headers["X-CSRF-Token"], "browser-csrf");
});

test("curation progress is read-only and recovery only resumes when backend permits it", async () => {
  for (const [status, retryAvailable, hasPrepare] of [
    ["preparing", false, false],
    ["recovery_required", false, false],
    ["recovery_required", true, true],
  ]) {
    const env = setup({
      identity: {status: "local_only"}, view: "local-workspace",
      curationStatus: {
        schema: "stpd/local-curation-preparation-v1", status, retry_available: retryAvailable,
        phase: "registering_sources", processed: 2, total: 5,
        reason: status === "recovery_required" ? "curation_plan_interrupted" : undefined,
        csrf_token: "browser-csrf",
      },
      handler: async url => {
        if (url === "/api/local-workspace/managed") return {status: "legacy_workspace_configured"};
        if (url === "/api/local-workspace?limit=25&offset=0") return {total: 0, items: []};
        throw new Error(`unexpected route ${url}`);
      },
    });
    const page = await env.render();
    if (retryAvailable || status === "preparing") assert.match(text(page), /2 \/ 5/);
    assert.equal(Boolean(walk(page).find(element => element.dataset?.action === "prepare-local-curation")), hasPrepare);
    if (status === "preparing") assert.equal(action(page, "refresh-local-curation").disabled, false);
    assert.equal(post(env.calls).length, 0, "render/refresh affordances never auto-resume");
    if (hasPrepare) {
      await action(page, "prepare-local-curation").onclick();
      assert.equal(post(env.calls).length, 1);
      assert.deepEqual(body(post(env.calls)[0]), {});
    }
  }
});

test("ready curation preserves ordinary training and test use while Gold history stays explicit", async () => {
  const env = setup({
    identity: {status: "signed_out"}, view: "local-workspace",
    curationStatus: {
      schema: "stpd/local-curation-preparation-v1", status: "ready",
      historical_use_history: "unknown", known_dataset_count: 2,
      unknown_dataset_count: 3, unknown_source_count: 1,
    },
    handler: async url => {
      if (url === "/api/local-workspace/managed") return {status: "ready", workspace_id: "c".repeat(32)};
      if (url === "/api/local-workspace?limit=25&offset=0") return {total: 0, items: []};
      throw new Error(`unexpected route ${url}`);
    },
  });
  const page = await env.render();
  assert.match(text(page), /训练和测试数据仍按各自规则检查/);
  assert.match(text(page), /旧资料的用途历史未完全可证/);
  assert.match(text(page), /Gold 仍按来源与已有用途限制/);
  assert.equal(walk(page).some(element => element.dataset?.action === "prepare-local-curation"), false);
  assert.equal(post(env.calls).length, 0);
});

test("local recording catalog is fetched only on explicit refresh and missing tool is not zero records", async () => {
  const env = setup({
    identity: {status: "signed_out"},
    view: "local-workspace",
    handler: async (url) => {
      if (url === "/api/local-workspace/managed") return {
        schema: "stpd/managed-local-workspace-registration-v1",
        status: "ready",
        workspace_id: "c".repeat(32),
      };
      if (url === "/api/local-workspace?limit=25&offset=0") return {
        schema: "stpd/local-workspace-inventory-v1", total: 0, items: [],
      };
      if (url === "/api/local-recordings") return {
        schema: "stpd/local-recording-catalog-v1",
        status: "tool_registration_missing",
        error_code: "collection_tool_registration_missing",
        candidate_count: 0,
        candidates: [],
      };
      throw new Error(`unexpected route ${url}`);
    },
  });
  const initial = await env.render();
  assert.match(text(initial), /查看录制来源/);
  assert.equal(env.calls.filter(call => call.url === "/api/local-recordings").length, 0);
  await action(initial, "read-local-recordings").onclick();
  assert.equal(env.calls.filter(call => call.url === "/api/local-recordings").length, 1);
  assert.equal(env.calls.find(call => call.url === "/api/local-recordings").options.method || "GET", "GET");
  const refreshed = await env.render();
  assert.match(text(refreshed), /尚未注册本机录制组件/);
  assert.doesNotMatch(text(refreshed), /没有检测到已结束的录制/);
  assert.equal(env.calls.filter(call => call.url === "/api/local-recordings").length, 1);
  assert.equal(post(env.calls).length, 0);
});

test("local recording import uses one explicit selection and resets attestation when it changes", async () => {
  const candidate = id("a");
  const env = setup({
    identity: {status: "local_only"},
    view: "local-workspace",
    handler: async (url, options) => {
      if (url === "/api/local-workspace/managed") return {
        schema: "stpd/managed-local-workspace-registration-v1", status: "ready",
        workspace_id: "c".repeat(32),
      };
      if (url === "/api/local-workspace?limit=25&offset=0") return {
        schema: "stpd/local-workspace-inventory-v1", total: 0, items: [],
      };
      if (url === "/api/local-recordings") return {
        schema: "stpd/local-recording-catalog-v1", status: "ready",
        observed_at: "2026-09-27T00:00:00Z", root_basis: "configured_only",
        candidate_count: 2, candidates: [{
          candidate_id: candidate, session_id: "session-1", timeline_id: "timeline-1",
          closed_at: "2026-09-27T00:00:00Z",
        }, {
          candidate_id: id("b"), session_id: "session-2", timeline_id: "timeline-2",
          closed_at: "2026-09-27T00:01:00Z",
        }],
      };
      if (url === "/api/local-recordings/import") {
        assert.equal(options.method, "POST");
        return {status: "pending"};
      }
      throw new Error(`unexpected route ${url}`);
    },
  });
  const initial = await env.render();
  await action(initial, "read-local-recordings").onclick();
  const page = await env.render();
  const button = action(page, "import-local-recording");
  const checkbox = field(page, "local-recording-attestation");
  const selection = field(page, "local-recording-selection");
  assert.equal(walk(page).filter(element => element.dataset?.action === "import-local-recording").length, 1);
  assert.equal(checkbox.checked, false);
  assert.equal(button.disabled, true);
  await button.onclick();
  assert.equal(post(env.calls).length, 0);
  checkbox.checked = true;
  checkbox.onchange();
  assert.equal(button.disabled, true);
  selection.value = id("b");
  selection.onchange();
  checkbox.checked = true;
  checkbox.onchange();
  assert.equal(button.disabled, false);
  selection.value = candidate;
  selection.onchange();
  assert.equal(checkbox.checked, false);
  assert.equal(button.disabled, true);
  await button.onclick();
  assert.equal(post(env.calls).length, 0);
  checkbox.checked = true;
  checkbox.onchange();
  assert.equal(button.disabled, false);
  await button.onclick();
  const writes = post(env.calls);
  assert.equal(writes.length, 1);
  assert.equal(writes[0].url, "/api/local-recordings/import");
  assert.deepEqual(body(writes[0]), {candidate_id: candidate, human_origin_attested: true});
  assert.equal(writes[0].options.headers["X-CSRF-Token"], "browser-csrf");
});

test("local verified artifact preview is explicit and scoped to the selected detail", async () => {
  const artifact = id("a"), other = id("b");
  let previewStatus = {status: "completed", artifact_id: other, human_input_labels: 99};
  const env = setup({
    identity: {status: "local_only"}, view: "local-workspace", query: `&id=${artifact}`,
    handler: async (url, options) => {
      if (url === "/api/local-workspace/managed") return {status: "legacy_workspace_configured"};
      if (url === `/api/local-workspace/artifacts/${artifact}`) return {
        kind: "evidence", artifact_id: artifact,
        parameters: {schema: "stpd/local-verified-bundle-v1"},
      };
      if (url === "/api/local-recordings/preview/status")
        return {...previewStatus, csrf_token: "preview-csrf"};
      if (url === "/api/local-recordings/preview") {
        assert.equal(options.method, "POST");
        previewStatus = {status: "completed", artifact_id: artifact, availability: "available",
          human_input_labels: 5, canonical_decisions: 0, human_input_total: 6,
          human_input_exclusions: {"rejected_or_cancelled:cancelled": 1},
          decision_exclusions: {}, run_ids_observed: 1,
          independent_run_qualification: "insufficient_canonical_decisions"};
        return {status: "pending"};
      }
      throw new Error(`unexpected route ${url}`);
    },
  });
  const first = await env.render();
  assert.doesNotMatch(text(first), /99/);
  assert.match(text(first), /录制来源/);
  assert.match(text(first), new RegExp(artifact.slice(0, 16)));
  assert.equal(env.calls.some(call => call.url === "/api/local-recordings/import/status"), false,
    "artifact details should not load the repeated inventory import card");
  assert.equal(post(env.calls).length, 0);
  await action(first, "preview-local-recording").onclick();
  const writes = post(env.calls);
  assert.equal(writes.length, 1);
  assert.equal(writes[0].url, "/api/local-recordings/preview");
  assert.deepEqual(body(writes[0]), {artifact_id: artifact});
  assert.equal(writes[0].options.headers["X-CSRF-Token"], "preview-csrf");
  const second = await env.render();
  assert.match(text(second), /操作标签 5 条 \/ 完整决策 0 条/);
  assert.match(text(second), /样本预览本身不会创建数据集/);
  assert.equal(post(env.calls).length, 1);
});

test("local dataset checks are explicit for each purpose and keep labels separate", async () => {
  const artifact = id("a"), train = id("b");
  for (const purpose of ["training", "test", "gold"]) {
    const env = localDatasetEnv({artifact});
    const page = await env.render();
    assert.match(text(page), /完整决策 3 条/);
    assert.match(text(page), /输入标签不能当作完整转移/);
    assert.match(text(page), /不代表数据量已足以训练/);
    assert.equal(post(env.calls).length, 0, "GET/render must not start a dataset operation");
    const purposeField = field(page, "local-dataset-purpose");
    const parentField = field(page, "local-dataset-paired-training");
    purposeField.value = purpose;
    purposeField.onchange();
    parentField.value = train;
    parentField.onchange();
    if (purpose === "training") assert.equal(parentField.disabled, true);
    else assert.equal(parentField.disabled, false);
    await action(page, "check-local-dataset").onclick();
    const writes = post(env.calls);
    assert.equal(writes.length, 1);
    assert.equal(writes[0].url, "/api/local-datasets/preview");
    assert.deepEqual(body(writes[0]), {
      artifact_id: artifact,
      purpose,
      paired_training: purpose === "training" ? null : train,
    });
    assert.equal(writes[0].options.headers["X-CSRF-Token"], "dataset-csrf");
  }
});

test("human input dataset selection is limited to verified evidence on the visible page", async () => {
  const first = id("a"), second = id("b"), invalid = id("c");
  const env = localHumanDatasetEnv({items:[
    {artifact_id:first, kind:"evidence", parameters:{schema:"stpd/local-verified-bundle-v1"}},
    {artifact_id:second, kind:"evidence", parameters:{schema:"stpd/local-verified-bundle-v1"}},
    {artifact_id:invalid, kind:"evidence", parameters:{schema:"stpd/unverified-v1"}},
    {artifact_id:id("d"), kind:"dataset", parameters:{schema:"stpd/local-verified-bundle-v1"}},
  ]});
  const page = await env.render();
  assert.equal(walk(page).filter(element => element.name === "local-human-source").length, 2);
  assert.match(text(page), /已选 0 份录制（本页 0 份）/);
  assert.equal(post(env.calls).length, 0, "render and status GET never preview or save");
  const boxes = walk(page).filter(element => element.name === "local-human-source");
  assert.equal(action(page, "preview-human-input-dataset").disabled, true);
  boxes[0].checked = true;
  boxes[0].onchange();
  boxes[1].checked = true;
  boxes[1].onchange();
  assert.equal(action(page, "preview-human-input-dataset").disabled, false);
  await action(page, "preview-human-input-dataset").onclick();
  const writes = post(env.calls);
  assert.equal(writes.length, 1);
  assert.equal(writes[0].url, "/api/local-datasets/human-preview");
  assert.deepEqual(body(writes[0]), {artifact_ids:[first, second]});
  assert.equal(writes[0].options.headers["X-CSRF-Token"], "human-csrf");
  assert.equal(writes.some(call => call.url === "/api/local-datasets/preview"), false);
  assert.equal(writes.some(call => call.url === "/api/local-training/start"), false);
});

test("human recording selection survives pagination and search and is cleared explicitly", async () => {
  const first = id("a"), second = id("b"), preview = "e".repeat(32);
  let operation = {status:"idle"};
  const env = localHumanDatasetEnv({total:26, operation:() => operation,
    items:url => new URLSearchParams(url.split("?")[1]).get("offset") === "25"
      ? [{artifact_id:second, kind:"evidence", parameters:{schema:"stpd/local-verified-bundle-v1"}}]
      : [{artifact_id:first, kind:"evidence", parameters:{schema:"stpd/local-verified-bundle-v1"}}],
    onWrite:(url, options) => {
      if (url === "/api/local-datasets/human-preview") {
        const value = JSON.parse(options.body);
        operation = {status:"preview_ready", kind:"human_input", sample_type:"human_input", purpose:"training",
          artifact_id:value.artifact_ids[0], artifact_ids:value.artifact_ids, accepted_labels:11,
          split_status:"not_checked_for_training", can_publish:true, preview_id:preview};
        return {status:"preview_ready"};
      }
      return {status:"completed"};
    },
  });
  let page = await env.render();
  field(page, "local-human-source").checked = true;
  field(page, "local-human-source").onchange();
  await action(page, "local-workspace-next").onclick();
  page = await env.render();
  assert.match(text(page), /已选 1 份录制（本页 0 份）/);
  assert.equal(field(page, "local-human-source").checked, false);
  field(page, "local-human-source").checked = true;
  field(page, "local-human-source").onchange();
  assert.match(text(page), /已选 2 份录制（本页 1 份）/);
  await action(page, "refresh-human-input-dataset-status").onclick();
  assert.equal(post(env.calls).length, 0, "status refresh never creates a preview");
  await action(page, "preview-human-input-dataset").onclick();
  assert.deepEqual(body(post(env.calls)[0]), {artifact_ids:[first, second]});
  assert.equal(post(env.calls)[0].options.headers["X-CSRF-Token"], "human-csrf");
  page = await env.render();
  assert.ok(action(page, "publish-human-input-dataset"));

  const search = field(page, "local-workspace-search");
  search.value = "other filter";
  await action(page, "search-local-workspace").onclick();
  page = await env.render();
  assert.match(text(page), /已选 2 份录制/);
  await action(page, "clear-human-input-selection").onclick();
  assert.equal(action(page, "clear-human-input-selection").disabled, true,
    "the clear action stays disabled after clearing the current selection");
  page = await env.render();
  assert.match(text(page), /已选 0 份录制/);
  assert.equal(walk(page).some(element => element.dataset?.action === "publish-human-input-dataset"), false,
    "clearing selection invalidates an old preview");
  assert.equal(post(env.calls).length, 1, "selection clearing and redraw do not write");
});

test("human preview and publish require the exact ordered source selection", async () => {
  const first = id("a"), second = id("b"), preview = "e".repeat(32), result = id("f");
  let operation = {status:"idle"};
  const env = localHumanDatasetEnv({items:[
    {artifact_id:first, kind:"evidence", parameters:{schema:"stpd/local-verified-bundle-v1"}},
    {artifact_id:second, kind:"evidence", parameters:{schema:"stpd/local-verified-bundle-v1"}},
  ], operation:() => operation});
  let page = await env.render();
  const boxes = walk(page).filter(element => element.name === "local-human-source");
  boxes[0].checked = true; boxes[0].onchange();
  boxes[1].checked = true; boxes[1].onchange();
  operation = {status:"preview_ready", kind:"human_input", sample_type:"human_input", purpose:"training",
    artifact_id:first, artifact_ids:[first, second], accepted_labels:17,
    split_status:"not_checked_for_training", can_publish:true, preview_id:preview};
  page = await env.render();
  assert.match(text(page), /已接受操作标签[\s\S]*17/);
  assert.match(text(page), /尚未检查；不表示独立训练分组已就绪/);
  assert.equal(post(env.calls).length, 0, "matching preview remains read-only until confirmation");
  await action(page, "publish-human-input-dataset").onclick();
  assert.equal(post(env.calls).length, 1);
  assert.equal(post(env.calls)[0].url, "/api/local-datasets/publish");
  assert.deepEqual(body(post(env.calls)[0]), {preview_id:preview});
  assert.equal(post(env.calls)[0].options.headers["X-CSRF-Token"], "human-csrf");
  assert.equal(walk(page).some(element => element.dataset?.action === "start-local-training"), false);

  const mismatched = localHumanDatasetEnv({items:[
    {artifact_id:first, kind:"evidence", parameters:{schema:"stpd/local-verified-bundle-v1"}},
    {artifact_id:second, kind:"evidence", parameters:{schema:"stpd/local-verified-bundle-v1"}},
  ], operation:{status:"preview_ready", kind:"human_input", sample_type:"human_input", purpose:"training",
    artifact_id:first, artifact_ids:[second, first], accepted_labels:99, split_status:"assigned",
    can_publish:true, preview_id:preview}});
  const mismatchedPage = await mismatched.render();
  const mismatchedBoxes = walk(mismatchedPage).filter(element => element.name === "local-human-source");
  mismatchedBoxes[0].checked = true; mismatchedBoxes[0].onchange();
  mismatchedBoxes[1].checked = true; mismatchedBoxes[1].onchange();
  const rerenderedMismatch = await mismatched.render();
  assert.match(text(rerenderedMismatch), /另一组录制/);
  assert.doesNotMatch(text(rerenderedMismatch), /已接受操作标签[\s\S]*99/);
  assert.equal(walk(rerenderedMismatch).some(element => element.dataset?.action === "publish-human-input-dataset"), false);
  assert.equal(post(mismatched.calls).length, 0);
});

test("shared pending and unresolved publication recovery block a new human preview", async () => {
  const first = id("a");
  for (const operation of [
    {status:"pending", kind:"canonical", artifact_id:first, purpose:"training"},
    {status:"failed", kind:"human_input", sample_type:"human_input", purpose:"training",
      artifact_id:first, artifact_ids:[first], recovery_available:false,
      error_code:"publication_recovery_required"},
  ]) {
    const env = localHumanDatasetEnv({operation});
    const page = await env.render();
    const checkbox = field(page, "local-human-source");
    checkbox.checked = true; checkbox.onchange();
    assert.equal(action(page, "preview-human-input-dataset").disabled, true);
    assert.equal(walk(page).some(element => element.dataset?.action === "recover-human-dataset-publish"), false);
    assert.equal(post(env.calls).length, 0);
  }
  const earlyPending = localHumanDatasetEnv({operation:{status:"pending", kind:"human_input",
    purpose:"training", artifact_ids:[first]}});
  const earlyPendingPage = await earlyPending.render();
  field(earlyPendingPage, "local-human-source").checked = true;
  field(earlyPendingPage, "local-human-source").onchange();
  const earlyPendingRerender = await earlyPending.render();
  assert.match(text(earlyPendingRerender), /正在检查所选操作标签来源/,
    "early pending DTO has no sample_type or artifact_id yet but still identifies its selection");
  assert.equal(action(earlyPendingRerender, "preview-human-input-dataset").disabled, true);
  assert.equal(post(earlyPending.calls).length, 0);

  const earlyFailed = localHumanDatasetEnv({operation:{status:"failed", kind:"human_input",
    purpose:"training", artifact_ids:[first], error_code:"independent_groups_required"}});
  const earlyFailedPage = await earlyFailed.render();
  field(earlyFailedPage, "local-human-source").checked = true;
  field(earlyFailedPage, "local-human-source").onchange();
  const earlyFailedRerender = await earlyFailed.render();
  assert.match(text(earlyFailedRerender), /当前划分无法形成训练和开发两组/,
    "early failure reports its blocker without requiring sample_type");
  assert.match(text(earlyFailedRerender), /上次操作失败或中断/);

  const preview = "e".repeat(32);
  const recoverable = localHumanDatasetEnv({operation:{status:"interrupted", kind:"human_input",
    sample_type:"human_input", purpose:"training", artifact_id:first, artifact_ids:[first],
    recovery_available:true, can_publish:true, preview_id:preview}});
  let page = await recoverable.render();
  const checkbox = field(page, "local-human-source");
  checkbox.checked = true; checkbox.onchange();
  page = await recoverable.render();
  assert.equal(action(page, "preview-human-input-dataset").disabled, true);
  assert.equal(post(recoverable.calls).length, 0, "recovery must be explicitly clicked");
  await action(page, "recover-human-dataset-publish").onclick();
  assert.equal(post(recoverable.calls).length, 1);
  assert.equal(post(recoverable.calls)[0].url, "/api/local-datasets/publish");
  assert.deepEqual(body(post(recoverable.calls)[0]), {preview_id:preview});
});

test("a training-group warning does not block saving the Human input dataset", async () => {
  const source = id("a"), preview = "e".repeat(32);
  let operation = {status:"idle"};
  const env = localHumanDatasetEnv({operation:() => operation});
  let page = await env.render();
  const checkbox = field(page, "local-human-source");
  checkbox.checked = true; checkbox.onchange();
  operation = {status:"preview_ready", kind:"human_input", sample_type:"human_input", purpose:"training",
    artifact_id:source, artifact_ids:[source], accepted_labels:4,
    split_status:"not_checked_for_training", error_code:"independent_groups_required",
    can_publish:true, preview_id:preview};
  page = await env.render();
  assert.match(text(page), /当前划分无法形成训练和开发两组/);
  assert.ok(action(page, "publish-human-input-dataset"), "saving the input dataset remains available");
  await action(page, "publish-human-input-dataset").onclick();
  assert.equal(post(env.calls).length, 1);
  assert.equal(post(env.calls)[0].url, "/api/local-datasets/publish");
  assert.equal(post(env.calls).some(call => call.url === "/api/local-training/start"), false);
});

test("canonical dataset detail does not claim a shared human input preview", async () => {
  const artifact = id("a"), humanSources = [id("a"), id("b")];
  const env = localDatasetEnv({artifact, datasetStatus:{
    schema:"stpd/local-dataset-operation-v1", availability:"ready", csrf_token:"dataset-csrf",
    operation:{status:"preview_ready", kind:"human_input", sample_type:"human_input", purpose:"training",
      artifact_id:humanSources[0], artifact_ids:humanSources, accepted_labels:123, can_publish:true,
      preview_id:"e".repeat(32)},
  }});
  const page = await env.render();
  assert.match(text(page), /操作标签数据集，不是完整决策检查/);
  assert.doesNotMatch(text(page), /预览保留决策[\s\S]*123/);
  assert.equal(walk(page).some(element => element.dataset?.action === "publish-local-dataset"), false);
  assert.equal(post(env.calls).length, 0);
});

test("human dataset training requires an exact local purpose binding", async () => {
  const dataset = id("a");
  for (const binding of [null,
    {schema:"stpd/local-dataset-binding-v1", artifact_id:dataset, sample_type:"human_input", curation_purpose:null},
    {schema:"stpd/local-dataset-binding-v1", artifact_id:id("b"), sample_type:"human_input", curation_purpose:"training"},
  ]) {
    const env = localHumanDatasetEnv({detailId:dataset, binding});
    const page = await env.render();
    assert.equal(env.calls.some(call => call.url === `/api/local-datasets/binding/${dataset}`), true);
    assert.equal(env.calls.some(call => call.url === `/api/local-workspace/binding/${dataset}`), false);
    assert.equal(env.calls.some(call => call.url === "/api/local-training/status"), false);
    assert.equal(walk(page).some(element => element.dataset?.action === "start-local-training"), false);
    assert.equal(post(env.calls).length, 0);
  }
  const env = localHumanDatasetEnv({detailId:dataset,
    binding:{schema:"stpd/local-dataset-binding-v1", artifact_id:dataset, sample_type:"human_input", curation_purpose:"training"},
    trainingStatus:{schema:"stpd/local-training-operation-v1", availability:"ready", csrf_token:"train-csrf",
      operation:{status:"failed", dataset_id:dataset, error_code:"human_engineering_sample_limit"}},
  });
  const page = await env.render();
  assert.equal(env.calls.some(call => call.url === "/api/local-training/status"), true);
  assert.match(text(page), /当前短训练只支持至多 32 条训练样本和 8 条开发样本/);
  assert.match(text(page), /数据仍保留/);
  assert.match(text(page), /另建数据集；不会自动截断/);
  assert.equal(walk(page).some(element => element.dataset?.action === "start-local-training"), false,
    "the immutable over-limit dataset cannot be retried as if its inputs had changed");
  assert.ok(walk(page).some(element => element.tagName === "A" && element.href === "?view=local-workspace"));
  assert.equal(post(env.calls).length, 0);
});

test("zero canonical decisions never expose dataset purpose controls", async () => {
  const artifact = id("a");
  const env = localDatasetEnv({artifact, sampleStatus: {
    status: "completed", artifact_id: artifact, availability: "available",
    human_input_labels: 9, canonical_decisions: 0, human_input_total: 9,
    human_input_exclusions: {}, decision_exclusions: {}, run_ids_observed: 1,
  }});
  const page = await env.render();
  assert.match(text(page), /操作标签 9 条 \/ 完整决策 0 条/);
  assert.match(text(page), /不会补成完整决策/);
  assert.doesNotMatch(text(page), /Gold 评估/);
  assert.equal(walk(page).some(element => element.name === "local-dataset-purpose"), false);
  assert.equal(env.calls.some(call => call.url === "/api/local-datasets/status"), false);
  assert.equal(post(env.calls).length, 0);
});

test("legacy and recovery-required dataset status preserve browsing without enabling writes", async () => {
  const artifact = id("a");
  for (const availability of ["workspace_required", "preparation_required", "recovery_required"]) {
    const env = localDatasetEnv({artifact, datasetStatus: {
      schema: "stpd/local-dataset-operation-v1", availability,
      reason: availability === "recovery_required" ? "curation_owner_recovery_required" : undefined,
      paired_training: [], operation: {status: "idle"}, csrf_token: "dataset-csrf",
    }});
    const page = await env.render();
    assert.match(text(page), /本机数据集检查/);
    assert.match(text(page), /仍可浏览/);
    if (availability === "preparation_required") {
      assert.match(text(page), /本机用途记录尚未准备/);
      assert.doesNotMatch(text(page), /用途记录需要恢复/);
    }
    assert.equal(action(page, "local-workspace-back").textContent, "返回本机资料目录");
    assert.equal(walk(page).some(element => element.dataset?.action === "check-local-dataset"), false);
    assert.equal(post(env.calls).length, 0);
  }
  const legacy = localDatasetEnv({artifact, managedStatus: {
    schema: "stpd/managed-local-workspace-registration-v1",
    status: "legacy_workspace_configured", curation_status: "recovery_required",
  }, datasetStatus: {
    schema: "stpd/local-dataset-operation-v1", availability: "recovery_required",
    reason: "legacy_history_requires_explicit_migration", paired_training: [],
    operation: {status: "idle"},
  }});
  const legacyPage = await legacy.render();
  assert.match(text(legacyPage), /正在使用现有本机资料库/);
  assert.match(text(legacyPage), /准备用途记录后/);
  assert.match(text(legacyPage), /仍可浏览/);
  assert.equal(post(legacy.calls).length, 0);
});

test("ready legacy curation suppresses setup cards while dataset purpose controls remain", async () => {
  const artifact = id("a");
  const env = localDatasetEnv({artifact,
    managedStatus: {
      schema: "stpd/managed-local-workspace-registration-v1",
      status: "legacy_workspace_configured", curation_status: "recovery_required",
    },
    curationStatus: {
      schema: "stpd/local-curation-preparation-v1", status: "ready",
      historical_use_history: "unknown", known_dataset_count: 1,
      unknown_dataset_count: 2, unknown_source_count: 1,
    },
  });
  const page = await env.render();
  assert.doesNotMatch(text(page), /正在使用现有本机资料库|本机用途记录已准备/);
  assert.match(text(page), /完整决策数量来自已核验的当前录制/);
  assert.equal(walk(page).some(element => element.name === "local-dataset-purpose"), true);
  assert.equal(env.calls.some(call => call.url === "/api/local-workspace/curation"), true);
  assert.equal(post(env.calls).length, 0);
});

test("ready legacy workspace directory does not say that preparation is still pending", async () => {
  const env = setup({
    identity: {status: "signed_out"}, view: "local-workspace",
    curationStatus: {schema: "stpd/local-curation-preparation-v1", status: "ready"},
    handler: async url => {
      if (url === "/api/local-workspace/managed") return {status: "legacy_workspace_configured"};
      if (url === "/api/local-workspace?limit=25&offset=0") return {total: 0, items: []};
      throw new Error(`unexpected route ${url}`);
    },
  });
  const page = await env.render();
  assert.match(text(page), /用途记录已准备/);
  assert.doesNotMatch(text(page), /准备用途记录后/);
  assert.equal(post(env.calls).length, 0);
});

test("prepared managed dataset detail leads with manifest facts and links its exact parents", async () => {
  const dataset = id("a"), source = id("b"), paired = id("c");
  const env = setup({
    identity: {status: "signed_out"}, view: "local-workspace", query: `&id=${dataset}`,
    curationStatus: {
      schema: "stpd/local-curation-preparation-v1", status: "not_applicable",
    },
    handler: async url => {
      if (url === "/api/local-workspace/managed") return {
        status: "ready", curation_status: "ready", workspace_id: id("d"),
      };
      if (url === `/api/local-workspace/artifacts/${dataset}`) return {
        schema: "stpd/local-workspace-artifact-v1", kind: "dataset", artifact_id: dataset,
        parameters: {
          schema: "stpd/curated-decision-dataset-v1", display_name: "test split",
          records: 17, purpose: "test", split_status: "purpose_assigned", paired_training: paired,
        },
        parents: [{role: `source_${source}`, artifact_id: source}], payloads: [],
      };
      throw new Error(`unexpected route ${url}`);
    },
  });
  const page = await env.render();
  assert.match(text(page), /数据集概览/);
  assert.match(text(page), /test split/);
  assert.match(text(page), /17/);
  assert.match(text(page), /测试/);
  assert.match(text(page), /已按所选评测用途分配/);
  assert.doesNotMatch(text(page), /本机工作空间已就绪|本机用途记录已准备|此页在本机读取/);
  assert.equal(env.calls.some(call => call.url === "/api/local-workspace/curation"), true,
    "managed workspaces may report not_applicable from the configured-workspace owner");
  assert.equal(find(page, element => element.tagName === "A" && element.href === `?view=local-workspace&id=${source}`).textContent,
    `查看来源 · ${source.slice(0, 16)}`);
  assert.equal(find(page, element => element.tagName === "A" && element.href === `?view=local-workspace&id=${paired}`).textContent,
    `查看配对训练数据集 · ${paired.slice(0, 16)}`);
  assert.equal(post(env.calls).length, 0);
});

test("dataset detail shows unknown when purpose metadata or source parents are absent", async () => {
  const dataset = id("a");
  const env = setup({
    identity: {status: "local_only"}, view: "local-workspace", query: `&id=${dataset}`,
    curationStatus: {schema: "stpd/local-curation-preparation-v1", status: "ready"},
    handler: async url => {
      if (url === "/api/local-workspace/managed") return {status: "legacy_workspace_configured"};
      if (url === `/api/local-workspace/artifacts/${dataset}`) return {
        kind: "dataset", artifact_id: dataset,
        parameters: {schema: "stpd/curated-decision-dataset-v1"}, parents: [], payloads: [],
      };
      throw new Error(`unexpected route ${url}`);
    },
  });
  const page = await env.render();
  assert.match(text(page), /样本数\n未知/);
  assert.match(text(page), /用途\n未知/);
  assert.match(text(page), /数据划分\n未知/);
  assert.match(text(page), /来源\n未知/);
  assert.equal(walk(page).some(element => element.tagName === "A" && element.href?.includes("id=")), false);
  assert.equal(post(env.calls).length, 0);
});

test("local training appears only on a fixed training dataset and starts once on click", async () => {
  const dataset = id("a");
  let finishStart;
  const env = localTrainingEnv({
    artifact:dataset,
    trainingHandler:async (url, options) => {
      if (url === "/api/local-training/start" && options.method === "POST")
        return new Promise(resolve => { finishStart = () => resolve({
          schema:"stpd/local-training-operation-v1", availability:"ready",
          operation:{status:"pending", dataset_id:dataset, stage:"reserving"},
        }); });
      throw new Error(`unexpected route ${url}`);
    },
  });
  const page = await env.render();
  assert.match(text(page), /本机短训练/);
  assert.match(text(page), /新启动的任务默认使用 D-Simple-S v1、CPU 2 线程和 3 步/);
  assert.match(text(page), /不代表模型策略质量/);
  assert.doesNotMatch(text(page), /training-csrf/);
  assert.equal(env.calls.filter(call => call.url === "/api/local-training/status").length, 1);
  assert.equal(post(env.calls).length, 0, "GET and rendering never start a training job");
  const button = action(page, "start-local-training");
  assert.equal(button.disabled, false);
  const first = button.onclick();
  const duplicate = button.onclick();
  assert.equal(post(env.calls).length, 1, "double click is guarded while the POST is unresolved");
  const start = post(env.calls)[0];
  assert.equal(start.url, "/api/local-training/start");
  assert.deepEqual(JSON.parse(start.options.body), {dataset_id:dataset});
  assert.equal(start.options.headers["X-CSRF-Token"], "training-csrf");
  finishStart();
  await Promise.all([first, duplicate]);
  assert.equal(button.disabled, true, "one uncertain start cannot be resent from the same rendered card");
});

test("local training hides for non-training purposes and unsupported dataset schemas", async () => {
  for (const [kind, parameters] of [
    ["dataset", {schema:"stpd/curated-decision-dataset-v1", purpose:"test"}],
    ["dataset", {schema:"stpd/curated-decision-dataset-v1", purpose:"gold"}],
    ["dataset", {schema:"future/dataset-v9", purpose:"training"}],
    ["evidence", {schema:"stpd/curated-decision-dataset-v1", purpose:"training"}],
  ]) {
    const env = localTrainingEnv({kind, parameters});
    const page = await env.render();
    assert.doesNotMatch(text(page), /本机短训练/);
    assert.equal(env.calls.some(call => call.url === "/api/local-training/status"), false);
    assert.equal(post(env.calls).length, 0);
  }
});

test("local training state gates pending and unknown outcomes, and links only completed outputs", async () => {
  const dataset = id("a"), runResult = id("b"), model = id("c"), evaluation = id("d");
  for (const operation of [
    {status:"pending", dataset_id:dataset, stage:"training"},
    {status:"interrupted_unknown", dataset_id:dataset, stage:"training"},
    {status:"recovery_required", dataset_id:dataset, stage:"verifying_result"},
  ]) {
    const env = localTrainingEnv({artifact:dataset, trainingStatus:{
      schema:"stpd/local-training-operation-v1", availability:"ready", csrf_token:"training-csrf", operation,
    }});
    const page = await env.render();
    assert.match(text(page), /既有任务的配方以其模型记录为准/);
    assert.doesNotMatch(text(page), /本机 D-Simple 短训练/);
    assert.equal(walk(page).some(element => element.dataset?.action === "start-local-training"), false);
    assert.equal(post(env.calls).length, 0);
    if (operation.status === "pending") {
      assert.match(text(page), /关闭游戏不代表训练暂停/);
      assert.ok(action(page, "refresh-local-training-status"));
      await action(page, "refresh-local-training-status").onclick();
      assert.equal(post(env.calls).length, 0, "refresh never repeats the start command");
    }
    else assert.match(text(page), /结果.*未能确认/);
  }
  const done = localTrainingEnv({artifact:dataset, trainingStatus:{
    schema:"stpd/local-training-operation-v1", availability:"ready", csrf_token:"training-csrf",
    operation:{status:"completed", dataset_id:dataset, run_id:id("e"), result_id:runResult,
      model_id:model, evaluation_id:evaluation},
  }});
  const resultPage = await done.render();
  assert.match(text(resultPage), /这不表示模型已加载到游戏/);
  assert.match(text(resultPage), /当前任务配方：以模型记录为准/);
  for (const [output, view] of [[runResult, "local-workspace"], [model, "local-workspace"], [evaluation, "local-workspace"]])
    assert.equal(find(resultPage, element => element.tagName === "A" && element.href === `?view=${view}&id=${output}`) !== null, true);
  assert.equal(walk(resultPage).some(element => element.dataset?.action === "start-local-training"), false);
  assert.equal(post(done.calls).length, 0);
});

test("completed training offers one explicit new experiment with exact prior identity", async () => {
  const dataset = id("a"), operationId = "1".repeat(32);
  const result = id("b"), model = id("c"), evaluation = id("d");
  let finishStart;
  const env = localTrainingEnv({artifact:dataset, trainingStatus:{
    schema:"stpd/local-training-operation-v1", availability:"ready", csrf_token:"training-csrf",
    operation:{status:"completed", operation_id:operationId, dataset_id:dataset,
      run_id:id("e"), result_id:result, model_id:model, evaluation_id:evaluation},
  }, trainingHandler:async (url, options) => {
    if (url === "/api/local-training/start" && options.method === "POST")
      return new Promise(resolve => { finishStart = () => resolve({
        schema:"stpd/local-training-operation-v1", availability:"ready",
        operation:{status:"pending", operation_id:"2".repeat(32), dataset_id:dataset},
      }); });
    throw new Error(`unexpected route ${url}`);
  }});
  const page = await env.render();
  assert.equal(post(env.calls).length, 0, "render never starts another experiment");
  await action(page, "refresh-local-training-status").onclick();
  assert.equal(post(env.calls).length, 0, "refresh remains read only");
  const button = action(page, "start-local-training-new");
  assert.equal(button.disabled, false);
  const first = button.onclick(), duplicate = button.onclick();
  assert.equal(post(env.calls).length, 1);
  assert.deepEqual(body(post(env.calls)[0]), {
    dataset_id:dataset, after_completed_operation_id:operationId,
  });
  finishStart();
  await Promise.all([first, duplicate]);
  assert.equal(button.disabled, true);
  for (const artifact of [result, model, evaluation])
    assert.ok(find(page, element => element.tagName === "A" && element.href === `?view=local-workspace&id=${artifact}`));
});

test("experimental M2 is explicit and completed status has no invented evaluation", async () => {
  const dataset = id("a"), result = id("b"), model = id("c");
  const recipe = "stage1a.dsimple.m2.k1.experimental.v1";
  const ready = localTrainingEnv({artifact:dataset, trainingStatus:{
    schema:"stpd/local-training-operation-v1", availability:"ready", csrf_token:"training-csrf",
    operation:{status:"idle"},
  }});
  const page = await ready.render();
  assert.equal(post(ready.calls).length, 0);
  const selection = find(page, element => element.tagName === "SELECT"
    && element.name === "local-training-recipe");
  assert.ok(selection);
  selection.value = recipe;
  await action(page, "start-local-training").onclick();
  assert.deepEqual(body(post(ready.calls)[0]), {dataset_id:dataset, recipe});

  const done = localTrainingEnv({artifact:dataset, trainingStatus:{
    schema:"stpd/local-training-operation-v2", availability:"ready", csrf_token:"training-csrf",
    operation:{status:"completed", operation_id:"1".repeat(32), dataset_id:dataset,
      recipe, result_type:"train_only", evaluation_status:"not_run",
      run_id:id("e"), checkpoint_id:id("f"), input_id:id("9"),
      result_id:result, model_id:model},
  }});
  const completed = await done.render();
  assert.match(text(completed), /此任务不包含开发集评估指标/);
  assert.equal(text(completed).includes("查看开发集结果"), false);
  assert.equal(post(done.calls).length, 0);
  assert.ok(action(completed, "start-local-training-new"));
});

const memoryEvaluationEnv = ({operation = {status:"idle"}, availability = "ready",
  sources = [{artifact_id:id("b"), kind:"dataset",
    parameters:{schema:"stpd/human-text-input-source-v1", display_name:"开发观察"}}],
  total = sources.length, statusSchema = "stpd/local-memory-evaluation-operation-v1"} = {}) => {
  const model = id("a");
  return setup({identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${model}`,
    handler:async url => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready"};
      if (url === `/api/local-workspace/artifacts/${model}`) return {
        artifact_id:model, kind:"model", parameters:{schema:"stpd/experimental-m2-model-v1"}};
      if (url === "/api/local-memory-evaluations/status") return {
        schema:statusSchema, availability, operation, csrf_token:"memory-csrf",
        filesystem_path:"/private/hidden"};
      if (url.startsWith("/api/local-workspace?")) return {
        schema:"stpd/local-workspace-inventory-v1", items:sources, total};
      if (url === "/api/local-memory-evaluations/start") return {
        schema:statusSchema, availability:"ready", operation:{status:"pending"}};
      throw new Error(`unexpected route ${url}`);
    }});
};

test("M2 model detail selects an existing Human source and starts dev evaluation only on click", async () => {
  const env = memoryEvaluationEnv();
  const page = await env.render();
  assert.match(text(page), /独立来源开发集评估/);
  assert.match(text(page), /不是 Gold、独立游戏局或科学质量证明/);
  assert.doesNotMatch(text(page), /\/private\/hidden/);
  assert.equal(post(env.calls).length, 0);
  const source = field(page, "local-memory-dev-source");
  assert.equal(source.value, "");
  await action(page, "start-local-memory-evaluation").onclick();
  assert.equal(post(env.calls).length, 0, "empty selection cannot start");
  source.value = id("b");
  await action(page, "start-local-memory-evaluation").onclick();
  assert.equal(post(env.calls).length, 1);
  assert.equal(post(env.calls)[0].url, "/api/local-memory-evaluations/start");
  assert.deepEqual(body(post(env.calls)[0]), {model_id:id("a"), source_id:id("b")});
  assert.equal(post(env.calls)[0].options.headers["X-CSRF-Token"], "memory-csrf");
});

test("M2 evaluation pending, unknown, and unrecognized statuses never offer another start", async () => {
  for (const scenario of [
    {operation:{status:"pending", model_id:id("a")}},
    {operation:{status:"interrupted_unknown", model_id:id("a")}},
    {operation:{status:"idle"}, availability:"recovery_required"},
    {operation:{status:"future"}},
    {operation:{status:"idle"}, statusSchema:"future/local-memory-evaluation-v9"},
  ]) {
    const env = memoryEvaluationEnv(scenario);
    const page = await env.render();
    assert.equal(walk(page).some(element => element.dataset?.action === "start-local-memory-evaluation"), false);
    assert.equal(post(env.calls).length, 0);
    assert.equal(env.calls.some(call => call.url.startsWith("/api/local-workspace?")), false);
  }
});

test("M2 failed evaluation shows bounded error and source inventory does not expose private names", async () => {
  const env = memoryEvaluationEnv({operation:{status:"failed", model_id:id("a"),
    error_code:"train_dev_source_overlap"}, sources:[{artifact_id:id("b"), kind:"dataset",
      parameters:{schema:"stpd/human-text-input-source-v1", display_name:"/private/Human/raw.json"}}]});
  const page = await env.render();
  assert.match(text(page), /开发集评估未完成/);
  assert.match(text(page), /train_dev_source_overlap/);
  assert.doesNotMatch(text(page), /\/private\/Human\/raw.json/);
  assert.equal(post(env.calls).length, 0);
});

test("M2 evaluation completed links only the recorded dev report and reports semantic overlap as diagnostic", async () => {
  const result = id("c");
  const env = memoryEvaluationEnv({operation:{status:"completed", model_id:id("a"),
    source_id:id("b"), evaluation_id:result, semantic_overlap:true}});
  const page = await env.render();
  assert.match(text(page), /语义重叠/);
  assert.ok(walk(page).some(element => element.tagName === "A"
    && element.href === `?view=local-workspace&id=${result}`));
  assert.equal(post(env.calls).length, 0);
});

test("M2 offline report reads the owner summary with no independence claim", async () => {
  const result = id("c");
  const env = setup({identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${result}`,
    handler:async url => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready"};
      if (url === `/api/local-workspace/artifacts/${result}`) return {
        artifact_id:result, kind:"offline_evaluation",
        parameters:{schema:"stpd/experimental-m2-offline-evaluation-v1", partition:"dev"}};
      if (url === `/api/local-workspace/evaluations/${result}`) return {
        schema:"stpd/local-offline-evaluation-summary-v1", evaluation_id:result,
        evaluation_schema:"stpd/experimental-m2-offline-evaluation-v1", partition:"dev",
        validation_scope:"recorded_report_and_parent_identities",
        interpretation:"producer_recorded_summary_not_full_lineage_or_quality_verification",
        model_id:id("a"), dev_source_id:id("b"), semantic_overlap:true,
        strict_deduplicated_benchmark:false, native_run_independence:false,
        model_selection_exposure:"unknown", decision_count:2, reported_run_groups:1,
        multi_candidate_count:2, overall:{count:2,top1:0.5,mrr:0.5,nll:1,confidence:0.5,margin:0.1}};
      throw new Error(`unexpected route ${url}`);
    }});
  const page = await env.render();
  assert.match(text(page), /已记录语义重叠诊断/);
  assert.match(text(page), /严格去重基准\n未建立/);
  assert.match(text(page), /独立性\n未知/);
  assert.equal(env.calls.filter(call => call.url === `/api/local-workspace/evaluations/${result}`).length, 1);
  assert.equal(post(env.calls).length, 0);
});

test("pending new training preserves completed result links without another start", async () => {
  const dataset = id("a"), result = id("b"), model = id("c"), evaluation = id("d");
  const env = localTrainingEnv({artifact:dataset, trainingStatus:{
    schema:"stpd/local-training-operation-v1", availability:"ready", csrf_token:"training-csrf",
    operation:{status:"pending", dataset_id:dataset, stage:"training", previous_completed:{
      operation_id:"1".repeat(32), dataset_id:dataset, result_id:result,
      model_id:model, evaluation_id:evaluation,
    }},
  }});
  const page = await env.render();
  for (const artifact of [result, model, evaluation])
    assert.ok(find(page, element => element.tagName === "A" && element.href === `?view=local-workspace&id=${artifact}`));
  assert.equal(post(env.calls).length, 0);
  assert.equal(walk(page).some(element => element.dataset?.action === "start-local-training-new"), false);
});

test("local training surfaces preparation and failed reasons without guessing retryability", async () => {
  const prepared = localTrainingEnv({trainingStatus:{
    schema:"stpd/local-training-operation-v1", availability:"preparation_required",
    reason:"curation_preparation_required", operation:{status:"idle"},
  }});
  const preparationPage = await prepared.render();
  assert.match(text(preparationPage), /用途记录尚未准备/);
  assert.ok(walk(preparationPage).some(element => element.tagName === "A" && element.href === "?view=local-workspace"));
  assert.equal(walk(preparationPage).some(element => element.dataset?.action === "start-local-training"), false);
  assert.equal(post(prepared.calls).length, 0);

  const failed = localTrainingEnv({trainingStatus:{
    schema:"stpd/local-training-operation-v1", availability:"ready", csrf_token:"training-csrf",
    operation:{status:"failed", dataset_id:id("a"), error_code:"insufficient_independent_components"},
  }});
  const failurePage = await failed.render();
  assert.match(text(failurePage), /独立对局数量不足/);
  assert.equal(action(failurePage, "start-local-training").disabled, false,
    "a new explicit attempt is separate from silent retry");
  assert.equal(post(failed.calls).length, 0);

  const failedWithRun = localTrainingEnv({trainingStatus:{
    schema:"stpd/local-training-operation-v1", availability:"ready", csrf_token:"training-csrf",
    operation:{status:"failed", dataset_id:id("f"), run_id:id("e"), error_code:"worker_failure"},
  }});
  const uncertain = await failedWithRun.render();
  assert.match(text(uncertain), /已有训练运行记录/);
  assert.equal(walk(uncertain).some(element => element.dataset?.action === "start-local-training"), false);
  assert.equal(post(failedWithRun.calls).length, 0);
});

test("unknown local training schema does not expose unrecognized fields or tokens", async () => {
  const env = localTrainingEnv({trainingStatus:{
    schema:"future/local-training-v9", availability:"ready", csrf_token:"must-not-render",
    operation:{status:"idle"}, filesystem_path:"/private/local/path", diagnostic_text:"secret marker",
  }});
  const page = await env.render();
  assert.match(text(page), /训练状态格式未知/);
  assert.match(text(page), /unknown_local_training_status_schema/);
  assert.doesNotMatch(text(page), /must-not-render|\/private\/local\/path|secret marker/);
  assert.equal(walk(page).some(element => element.dataset?.action === "start-local-training"), false);
  assert.equal(post(env.calls).length, 0);
});

test("known Stage 1a model detail summarizes its recipe and links exact parents", async () => {
  const model = id("a"), run = id("b"), view = id("c"), checkpoint = id("d");
  const env = setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${model}`,
    handler:async url => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      if (url === `/api/local-workspace/artifacts/${model}`) return {
        schema:"stpd/local-workspace-artifact-v1", artifact_id:model, kind:"model",
        parameters:{schema:"stpd/stage1a-model-v1", config:{recipe:"stage1a.b.s.v2", steps:3, device:"cpu"},
          backbone:{kind:"scratch", shape:{width:48, layers:1}}, steps:3, qualification:"engineering_only"},
        parents:[{role:"run", artifact_id:run}, {role:"model_view", artifact_id:view},
          {role:"checkpoint", artifact_id:checkpoint}, {role:"training_input", artifact_id:id("e")},
          {role:"run", artifact_id:"not-a-hash"}],
        payloads:[{role:"weights", sha256:id("f"), size:128, media_type:"application/vnd.safetensors"}],
      };
      throw new Error(`unexpected route ${url}`);
    },
  });
  const page = await env.render();
  assert.match(text(page), /模型概览/);
  assert.match(text(page), /B v2/);
  assert.match(text(page), /从头训练/);
  assert.match(text(page), /训练步数[\s\S]*3/);
  assert.match(text(page), /设备[\s\S]*CPU/);
  assert.match(text(page), /工程验证用途；不代表模型质量或游戏实战资格/);
  for (const [artifact, label] of [[run, "关联训练运行"], [view, "关联输入视图"], [checkpoint, "关联检查点"]]) {
    const parent = find(page, element => element.tagName === "A" && element.href === `?view=local-workspace&id=${artifact}`);
    assert.match(parent.textContent, new RegExp(label));
  }
  assert.equal(walk(page).filter(element => element.tagName === "A" && element.href.includes(id("e"))).length, 0,
    "training input is not presented as one of the requested run/view/checkpoint links");
  assert.equal(walk(page).some(element => element.tagName === "A" && element.href.includes("not-a-hash")), false,
    "malformed parent identities are never linked");
  assert.match(text(page), /stpd\/stage1a-model-v1/, "exact manifest metadata remains available as a technical fallback");
  assert.equal(post(env.calls).length, 0);
});

test("Stage 1a model source requires a matching manifest backbone", async () => {
  const model = id("a");
  for (const [recipe, backbone, expected, unexpected] of [
    ["stage1a.b.pf.v2", {kind:"pf", qwen:{snapshot:"fixture"}}, /冻结预训练骨干/, /未知（配方与模型来源记录不一致）/],
    ["stage1a.b.s.v2", {kind:"pf", qwen:{snapshot:"fixture"}}, /未知（配方与模型来源记录不一致）/, /从头训练/],
    ["stage1a.b.s.v2", undefined, /模型来源\n未知/, /从头训练/],
  ]) {
    const env = setup({
      identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${model}`,
      handler:async url => {
        if (url === "/api/local-workspace/managed") return {
          schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
        };
        if (url === `/api/local-workspace/artifacts/${model}`) return {
          artifact_id:model, kind:"model",
          parameters:{schema:"stpd/stage1a-model-v1", config:{recipe, steps:3, device:"cpu"},
            ...(backbone ? {backbone} : {}), steps:3, qualification:"engineering_only"},
          parents:[], payloads:[],
        };
        throw new Error(`unexpected route ${url}`);
      },
    });
    const page = await env.render();
    assert.match(text(page), expected);
    assert.doesNotMatch(text(page), unexpected);
    assert.equal(post(env.calls).length, 0);
  }
});

test("unknown model schemas keep metadata fallback and do not invent a model overview", async () => {
  const model = id("a");
  const env = setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${model}`,
    handler:async url => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      if (url === `/api/local-workspace/artifacts/${model}`) return {
        artifact_id:model, kind:"model", parameters:{schema:"future/model-v9", recipe:"unknown", steps:9},
        parents:[{role:"run", artifact_id:"not-a-hash"}], payloads:[],
      };
      throw new Error(`unexpected route ${url}`);
    },
  });
  const page = await env.render();
  assert.doesNotMatch(text(page), /模型概览|从头训练|冻结预训练骨干/);
  assert.match(text(page), /future\/model-v9/);
  assert.match(text(page), /recipe[\s\S]*unknown/);
  assert.equal(walk(page).some(element => element.tagName === "A" && element.href.includes("not-a-hash")), false);
  assert.equal(post(env.calls).length, 0);
});

test("local model export status is read-only until one explicit export click", async () => {
  const model = id("a");
  let status = modelExportStatus({status:"idle"});
  const env = setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${model}`,
    renderOnReload:true,
    handler:async (url, options) => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      if (url === `/api/local-workspace/artifacts/${model}`) return textMenuScratchModel(model);
      if (url === "/api/local-model-exports/status") return status;
      if (url === "/api/local-model-exports/start") {
        assert.equal(options.method, "POST");
        status = modelExportStatus({status:"pending", model_id:model});
        return status;
      }
      throw new Error(`unexpected route ${url}`);
    },
  });
  const page = await env.render();
  assert.equal(action(page, "start-local-model-export").disabled, false);
  assert.match(text(page), /导出并校验/);
  assert.doesNotMatch(text(page), /export-csrf/);
  assert.equal(env.calls.some(call => call.url === "/api/local-model-exports/status"
    && call.options.method === "GET"), true);
  assert.equal(post(env.calls).length, 0, "render only reads export status");

  const button = action(page, "start-local-model-export");
  await Promise.all([button.onclick(), button.onclick()]);
  const writes = post(env.calls);
  assert.equal(writes.length, 1, "a double click issues one explicit start request");
  assert.equal(writes[0].url, "/api/local-model-exports/start");
  assert.deepEqual(body(writes[0]), {model_id:model});
  assert.equal(writes[0].options.headers["X-CSRF-Token"], "export-csrf");
  assert.equal(action(env.livePage, "start-local-model-export").disabled, true,
    "replacement render preserves backend pending eligibility");
});

test("M2 export describes its own scope alongside a completed dev report and guarded registration", async () => {
  const model = id("a"), run = id("b"), evaluation = id("c");
  let state = modelExportStatus({status:"idle"});
  const env = setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${model}`,
    renderOnReload:true,
    handler:async (url, options) => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      if (url === `/api/local-workspace/artifacts/${model}`) return memoryModel(model);
      if (url === "/api/local-memory-evaluations/status") return {
        schema:"stpd/local-memory-evaluation-operation-v1", availability:"ready",
        csrf_token:"memory-csrf", operation:{status:"completed", model_id:model,
          source_id:id("d"), evaluation_id:evaluation, semantic_overlap:false}};
      if (url.startsWith("/api/local-workspace?")) return {
        schema:"stpd/local-workspace-inventory-v1", items:[], total:0};
      if (url === "/api/local-model-exports/status") return state;
      if (url.startsWith("/api/local-model-registrations/status?")) return {
        schema:"stpd/local-model-registration-v1", model_id:model,
        status:"not_registered", loaded:false, runtime_profile:"text-menu-m2-v1",
        csrf_token:"synthetic-csrf",
      };
      if (url === "/api/local-model-exports/start") {
        assert.equal(options.method, "POST");
        assert.deepEqual(body({options}), {model_id:model});
        state = modelExportStatus({status:"completed", model_id:model,
          model_type:"memory", run_id:run, payload_bytes:123},
        {schema:"stpd/local-model-export-operation-v2"});
        return state;
      }
      throw new Error(`unexpected route ${url}`);
    },
  });
  const page = await env.render();
  assert.match(text(page), /模型概览/);
  assert.match(text(page), /实验性 D-Simple M2-K1/);
  assert.match(text(page), /训练产物；后续评估结果另见关联报告/);
  assert.match(text(page), /导出校验不包含评估结论/);
  assert.match(text(page), /开发用途离线工程评估已完成/);
  assert.doesNotMatch(text(page), /没有独立评估|仍没有独立评估/);
  assert.ok(walk(page).some(element => element.tagName === "A"
    && element.href === `?view=local-workspace&id=${evaluation}`));
  assert.match(text(page), /才能登记或加载/);
  assert.equal(post(env.calls).length, 0);
  assert.equal(post(env.calls).some(call => call.url.includes("local-model-registrations")), false);
  await action(page, "start-local-model-export").onclick();
  assert.equal(post(env.calls).length, 1);
  assert.match(text(env.livePage), /评估结果另见关联报告/);
  assert.doesNotMatch(text(env.livePage), /没有独立评估|仍没有独立评估/);
  assert.equal(walk(env.livePage).some(element => element.dataset?.action === "register-local-model"), true);
  assert.equal(post(env.calls).some(call => call.url.includes("local-model-registrations")), false);
});

test("older M2 receipt and registration timeout explain explicit recovery without replay", async () => {
  const model = id("a");
  let failure = "verified_export_receipt_required";
  const env = setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${model}`,
    renderOnReload:true,
    handler:async url => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      if (url === `/api/local-workspace/artifacts/${model}`) return memoryModel(model);
      if (url === "/api/local-model-exports/status") return modelExportStatus({
        status:"completed", model_id:model, model_type:"memory", payload_bytes:123,
      }, {schema:"stpd/local-model-export-operation-v2"});
      if (url === `/api/local-model-registrations/status?model_id=${model}`) return {
        schema:"stpd/local-model-registration-v1", model_id:model, status:"not_registered",
        loaded:false, runtime_profile:"text-menu-m2-v1", csrf_token:"synthetic-csrf",
      };
      if (url === "/api/local-model-registrations/register") return {httpStatus:409, error:failure};
      throw new Error(`unexpected route ${url}`);
    },
  });
  const page = await env.render();
  assert.equal(action(page, "start-local-model-export").textContent, "重新核验导出");
  await action(page, "register-local-model").onclick();
  assert.match(text(env.notice), /缺少校验回执.*重新核验导出/);
  assert.equal(post(env.calls).length, 1);
  failure = "registration_timeout";
  await action(env.livePage, "register-local-model").onclick();
  assert.match(text(env.notice), /刷新状态核对结果.*明确重试/);
  assert.equal(post(env.calls).length, 2);
  assert.equal(env.calls.some(call => call.url === "/api/local-models/prepare"
    || call.url === "/api/local-models/start"), false);
});

test("late model export completion refreshes current same-profile status without showing the old model result", async () => {
  const model = id("a"), other = id("b");
  let state = modelExportStatus({status:"idle"});
  let resolveStart, announceStart;
  const startStarted = new Promise(resolve => { announceStart = resolve; });
  const env = setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${model}`,
    renderOnReload:true,
    handler:async (url, options) => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      const artifact = url.match(/^\/api\/local-workspace\/artifacts\/([a-f0-9]{64})$/);
      if (artifact) return textMenuScratchModel(artifact[1]);
      if (url === "/api/local-model-exports/status") return state;
      if (url === "/api/local-model-exports/start") {
        assert.deepEqual(JSON.parse(options.body), {model_id:model});
        announceStart();
        return new Promise(resolve => { resolveStart = resolve; });
      }
      throw new Error(`unexpected route ${url}`);
    },
  });
  const firstPage = await env.render();
  const pendingClick = action(firstPage, "start-local-model-export").onclick();
  await startStarted;
  env.navigate("local-workspace", `&id=${other}`);
  const otherPage = await env.render();
  assert.equal(action(otherPage, "start-local-model-export").disabled, true,
    "the existing account-wide command lock blocks another model while the POST is in flight");
  state = modelExportStatus({status:"pending", model_id:model});
  resolveStart(state);
  await pendingClick;
  assert.equal(action(env.livePage, "start-local-model-export").disabled, true,
    "after the old POST settles, a fresh GET preserves the other-model busy state");
  assert.doesNotMatch(text(env.livePage), /导出校验完成|尚未登记为游戏模型/,
    "the old model result is never shown on the new model detail");
  assert.equal(post(env.calls).length, 1);
});

test("local model export only reports the matching model and safely handles shared operations", async () => {
  const model = id("a"), other = id("b");
  const scenarios = [
    {
      operation:{status:"completed", model_id:model, payload_bytes:2048,
        error_code:"/private/local/secret", export_path:"/private/local/secret"},
      success:/导出校验本身不会加载模型；当前运行状态请到模型页查看/,
      enabled:true,
      hidden:/\/private\/local\/secret|export-csrf/,
    },
    {
      operation:{status:"completed", model_id:other, payload_bytes:9999},
      success:/最近的导出记录属于另一模型/,
      enabled:true,
      hidden:/导出校验完成|9999 B/,
    },
    {
      operation:{status:"pending", model_id:other},
      success:/另一模型的导出正在进行/,
      enabled:false,
    },
    {
      operation:{status:"failed", model_id:model, error_code:"/private/diagnostic/path"},
      success:/上次导出未完成。你可以明确再次发起/,
      enabled:true,
      hidden:/\/private\/diagnostic\/path/,
    },
    {
      operation:{status:"interrupted", model_id:model},
      success:/结果尚未确认。再次点击会明确核对此模型/,
      enabled:true,
    },
    {
      operation:{status:"interrupted", model_id:other},
      success:/另一模型的导出结果尚未确认/,
      enabled:false,
    },
  ];
  for (const scenario of scenarios) {
    const env = setup({
      identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${model}`,
      handler:async url => {
        if (url === "/api/local-workspace/managed") return {
          schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
        };
        if (url === `/api/local-workspace/artifacts/${model}`) return textMenuScratchModel(model);
        if (url === "/api/local-model-exports/status") return modelExportStatus(scenario.operation);
        throw new Error(`unexpected route ${url}`);
      },
    });
    const page = await env.render();
    assert.match(text(page), scenario.success);
    if (scenario.hidden) assert.doesNotMatch(text(page), scenario.hidden);
    assert.equal(action(page, "start-local-model-export").disabled, !scenario.enabled);
    assert.equal(post(env.calls).length, 0);
  }
});

test("local model export stays unavailable for unsupported models and changed workspaces", async () => {
  const model = id("a"), other = id("b");
  let exportStatusReads = 0;
  const unsupported = setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${model}`,
    handler:async url => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      if (url === `/api/local-workspace/artifacts/${model}`) {
        const value = textMenuScratchModel(model);
        value.parameters.serializer.status = "future";
        return value;
      }
      if (url === "/api/local-model-exports/status") exportStatusReads++;
      throw new Error(`unexpected route ${url}`);
    },
  });
  const unsupportedPage = await unsupported.render();
  assert.equal(exportStatusReads, 0, "unsupported metadata does not query export status");
  assert.equal(walk(unsupportedPage).some(element => element.dataset?.action === "start-local-model-export"), false);

  const extraIdentity = setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${model}`,
    handler:async url => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      if (url === `/api/local-workspace/artifacts/${model}`) {
        const value = textMenuScratchModel(model);
        value.parameters.serializer.unrecognized = true;
        return value;
      }
      if (url === "/api/local-model-exports/status") exportStatusReads++;
      throw new Error(`unexpected route ${url}`);
    },
  });
  const extraIdentityPage = await extraIdentity.render();
  assert.equal(exportStatusReads, 0, "serializer identity must match the known fields exactly");
  assert.equal(walk(extraIdentityPage).some(element => element.dataset?.action === "start-local-model-export"), false);

  let changedStatus = modelExportStatus(
    {status:"completed", model_id:model, payload_bytes:123},
    {availability:"workspace_changed"},
  );
  const changed = setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${model}`,
    renderOnReload:true,
    handler:async url => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      if (url === `/api/local-workspace/artifacts/${model}`) return textMenuScratchModel(model);
      if (url === "/api/local-model-exports/status") return changedStatus;
      if (url === "/api/local-model-exports/start") {
        changedStatus = modelExportStatus({status:"pending", model_id:model});
        return changedStatus;
      }
      throw new Error(`unexpected route ${url}`);
    },
  });
  const changedPage = await changed.render();
  assert.match(text(changedPage), /上次记录来自之前的本机资料空间/);
  assert.match(text(changedPage), /为当前资料空间重新导出并校验/);
  assert.doesNotMatch(text(changedPage), /导出校验完成|123 B/);
  assert.equal(action(changedPage, "start-local-model-export").disabled, false,
    "a historical terminal result does not block an explicit export in the current workspace");
  assert.equal(post(changed.calls).length, 0);
  await action(changedPage, "start-local-model-export").onclick();
  assert.equal(post(changed.calls).length, 1, "the current model can be explicitly re-exported after the old terminal result");
  assert.equal(action(changed.livePage, "start-local-model-export").disabled, true);

  const previousPending = setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${model}`,
    handler:async url => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      if (url === `/api/local-workspace/artifacts/${model}`) return textMenuScratchModel(model);
      if (url === "/api/local-model-exports/status") return modelExportStatus(
        {status:"pending", model_id:other}, {availability:"workspace_changed"},
      );
      throw new Error(`unexpected route ${url}`);
    },
  });
  const pendingPage = await previousPending.render();
  assert.match(text(pendingPage), /之前本机资料空间的导出仍在处理中/,
    JSON.stringify(previousPending.calls.map(call => call.url)));
  assert.equal(action(pendingPage, "start-local-model-export").disabled, true,
    "an old unresolved operation still protects the shared export slot");
  assert.equal(post(previousPending.calls).length, 0);
});

test("text-menu D-Simple and legacy B expose export; PF and other inputs do not", async () => {
  const model = id("a");
  const cases = [
    {recipe:"stage1a.dsimple.s.v1", allowed:true, label:/D-Simple v1/},
    {recipe:"stage1a.b.s.v2", allowed:true, label:/B v2/},
    {recipe:"stage1a.dsimple.pf.v1", allowed:false, modify:value => {
      value.parameters.backbone = {kind:"pf"};
    }},
    {recipe:"stage1a.dsimple.s.v1", allowed:false, modify:value => {
      value.parameters.serializer.profile = "public_compact";
    }},
  ];
  for (const scenario of cases) {
    let statusReads = 0;
    const env = setup({
      identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${model}`,
      handler:async url => {
        if (url === "/api/local-workspace/managed") return {
          schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
        };
        if (url === `/api/local-workspace/artifacts/${model}`) {
          const value = textMenuScratchModel(model, scenario.recipe);
          scenario.modify?.(value);
          return value;
        }
        if (url === "/api/local-model-exports/status") {
          statusReads++;
          return modelExportStatus({status:"idle"});
        }
        throw new Error(`unexpected route ${url}`);
      },
    });
    const page = await env.render();
    if (scenario.label) assert.match(text(page), scenario.label);
    assert.equal(walk(page).some(element => element.dataset?.action === "start-local-model-export"), scenario.allowed);
    assert.equal(statusReads, scenario.allowed ? 1 : 0);
  }
});

test("completed local model export registers only on one explicit click and links the exact selection", async () => {
  const model = id("a"), selection = "text-model-a";
  let registration = modelRegistrationStatus(model);
  const env = setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${model}`,
    renderOnReload:true,
    handler:async (url, options) => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      if (url === `/api/local-workspace/artifacts/${model}`) return textMenuScratchModel(model);
      if (url === "/api/local-model-exports/status")
        return modelExportStatus({status:"completed", model_id:model, payload_bytes:2048});
      if (url === `/api/local-model-registrations/status?model_id=${model}`) {
        assert.equal(options.method, "GET");
        return registration;
      }
      if (url === "/api/local-model-registrations/register") {
        assert.equal(options.method, "POST");
        assert.deepEqual(JSON.parse(options.body), {model_id:model});
        registration = modelRegistrationStatus(model, "registered", {
          selection_id:selection, private_path:"/private/model/secret",
        });
        return registration;
      }
      throw new Error(`unexpected route ${url}`);
    },
  });
  const page = await env.render();
  assert.match(text(page), /登记会依据本机文本菜单运行环境/);
  assert.match(text(page), /不会安装运行组件、加载模型或进入游戏/);
  assert.doesNotMatch(text(page), /尚未加载/);
  assert.equal(action(page, "register-local-model").disabled, false);
  assert.equal(post(env.calls).length, 0, "detail render only reads registration state");
  assert.equal(env.calls.some(call => call.url === "/api/local-models/prepare"), false);

  await Promise.all([
    action(page, "register-local-model").onclick(),
    action(page, "register-local-model").onclick(),
  ]);
  const writes = post(env.calls);
  assert.equal(writes.length, 1, "a double click creates one registration request");
  assert.equal(writes[0].url, "/api/local-model-registrations/register");
  assert.deepEqual(body(writes[0]), {model_id:model});
  assert.equal(writes[0].options.headers["X-CSRF-Token"], "registration-csrf");
  assert.equal(action(env.livePage, "register-local-model").textContent, "重新核对登记",
    "a confirmed registration exposes an explicit recheck, never an automatic one");
  const selectionLink = find(env.livePage, element => element.tagName === "A"
    && element.href === `?view=local-models&id=${selection}`);
  assert.equal(selectionLink.textContent, "打开此模型选择");
  assert.doesNotMatch(text(env.livePage), /\/private\/model\/secret|registration-csrf/);
  assert.equal(post(env.calls).length, 1, "status refresh does not prepare or load a model");
  assert.equal(env.calls.some(call => call.url === "/api/local-models/prepare"
    || call.url === "/api/local-models/start"), false);
});

test("registration precondition failures need another explicit click and do not strand the control", async () => {
  const model = id("d"), selection = "text-model-d";
  let status = modelRegistrationStatus(model), attempts = 0;
  const env = setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${model}`,
    renderOnReload:true,
    handler:async (url, options) => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      if (url === `/api/local-workspace/artifacts/${model}`) return textMenuScratchModel(model);
      if (url === "/api/local-model-exports/status")
        return modelExportStatus({status:"completed", model_id:model});
      if (url === `/api/local-model-registrations/status?model_id=${model}`) return status;
      if (url === "/api/local-model-registrations/register") {
        attempts++;
        if (attempts === 1) return {httpStatus:409, error:"text_menu_capabilities_unavailable"};
        status = modelRegistrationStatus(model, "registered", {selection_id:selection});
        return status;
      }
      throw new Error(`unexpected route ${url}`);
    },
  });
  const page = await env.render();
  const register = action(page, "register-local-model");
  await register.onclick();
  assert.equal(attempts, 1);
  assert.equal(register.disabled, false, "a known precondition failure leaves a deliberate retry available");
  assert.match(text(env.notice), /请打开游戏后刷新，再明确重试/);
  assert.equal(post(env.calls).length, 1, "a precondition failure does not automatically replay POST");
  await register.onclick();
  assert.equal(attempts, 2, "the next attempt requires a second explicit click");
  assert.ok(walk(env.livePage).some(element => element.tagName === "A"
    && element.href === `?view=local-models&id=${selection}`));
});

test("current registration integrity blockers are reconciled by GET and disable another attempt", async () => {
  const model = id("e");
  let status = modelRegistrationStatus(model), statusReads = 0;
  const env = setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${model}`,
    renderOnReload:true,
    handler:async url => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      if (url === `/api/local-workspace/artifacts/${model}`) return textMenuScratchModel(model);
      if (url === "/api/local-model-exports/status")
        return modelExportStatus({status:"completed", model_id:model});
      if (url === `/api/local-model-registrations/status?model_id=${model}`) {
        statusReads++;
        return status;
      }
      if (url === "/api/local-model-registrations/register") {
        status = modelRegistrationStatus(model, "unavailable", {reason_code:"registration_metadata_invalid"});
        return {httpStatus:409, error:"registration_metadata_invalid"};
      }
      throw new Error(`unexpected route ${url}`);
    },
  });
  const page = await env.render();
  await action(page, "register-local-model").onclick();
  assert.equal(post(env.calls).length, 1);
  assert.ok(statusReads >= 2, "a current integrity blocker triggers read-only status reconciliation");
  assert.equal(walk(env.livePage).some(element => element.dataset?.action === "register-local-model"), false);
  assert.match(text(env.livePage), /本机模型登记资料无法安全确认/);
  assert.equal(post(env.calls).length, 1, "reconciliation does not replay the rejected POST");
});

test("registration only appears for the matching completed export and unavailable reasons stay bounded", async () => {
  const model = id("a"), other = id("b");
  const cases = [
    {export:modelExportStatus({status:"pending", model_id:model}), registration:null, action:false, request:false},
    {export:modelExportStatus({status:"completed", model_id:other}), registration:null, action:false, request:false},
    {export:modelExportStatus({status:"completed", model_id:model}, {availability:"workspace_changed"}), registration:null, action:false, request:false},
    {
      export:modelExportStatus({status:"completed", model_id:model}),
      registration:modelRegistrationStatus(model, "unavailable", {
        reason_code:"registration_metadata_invalid", private_path:"/secret/path",
      }),
      action:false, request:true,
      message:/本机模型登记资料无法安全确认/,
      hidden:/registration_metadata_invalid|\/secret\/path/,
    },
    {
      export:modelExportStatus({status:"completed", model_id:model}),
      registration:modelRegistrationStatus(model, "unavailable", {reason_code:"future_private_reason"}),
      action:false, request:true,
      message:/当前无法完成登记/,
      hidden:/future_private_reason/,
    },
    {
      export:modelExportStatus({status:"completed", model_id:model}),
      registration:modelRegistrationStatus(model, "not_registered", {reason_code:"source_binding_changed"}),
      action:true, request:true,
      message:/运行源码已变化；旧选择保留。可明确重新登记并生成新选择/,
    },
  ];
  for (const scenario of cases) {
    let registrationReads = 0;
    const env = setup({
      identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${model}`,
      handler:async url => {
        if (url === "/api/local-workspace/managed") return {
          schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
        };
        if (url === `/api/local-workspace/artifacts/${model}`) return textMenuScratchModel(model);
        if (url === "/api/local-model-exports/status") return scenario.export;
        if (url === `/api/local-model-registrations/status?model_id=${model}`) {
          registrationReads++;
          return scenario.registration;
        }
        throw new Error(`unexpected route ${url}`);
      },
    });
    const page = await env.render();
    assert.equal(registrationReads > 0, scenario.request);
    assert.equal(walk(page).some(element => element.dataset?.action === "register-local-model"), scenario.action);
    if (scenario.message) assert.match(text(page), scenario.message);
    if (scenario.hidden) assert.doesNotMatch(text(page), scenario.hidden);
    assert.equal(post(env.calls).length, 0);
  }
});

test("registered selection link focuses only its local model row without invoking model commands", async () => {
  const model = id("a"), selection = "text-model-a";
  const env = setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${model}`,
    handler:async url => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      if (url === `/api/local-workspace/artifacts/${model}`) return textMenuScratchModel(model);
      if (url === "/api/local-model-exports/status")
        return modelExportStatus({status:"completed", model_id:model});
      if (url === `/api/local-model-registrations/status?model_id=${model}`)
        return modelRegistrationStatus(model, "registered", {selection_id:selection});
      if (url === "/api/local-models") return {
        policies:[
          {selection_id:selection, label:"Text menu model", artifact_sha256:model},
          {selection_id:"other-model", label:"Other", artifact_sha256:id("b")},
        ], downloaded_models:[],
      };
      if (url === "/api/local-models/status") return {status:"idle", loaded:false};
      throw new Error(`unexpected route ${url}`);
    },
  });
  const detail = await env.render();
  assert.match(text(detail), /登记本身不会加载模型/);
  assert.ok(find(detail, element => element.tagName === "A"
    && element.href === `?view=local-models&id=${selection}`));
  env.navigate("local-models", `&id=${selection}`);
  const page = await env.render();
  assert.match(text(page), /刚登记的模型选择/);
  assert.equal(post(env.calls).length, 0, "deep-link preselection is read-only");
  assert.equal(env.calls.some(call => call.url.startsWith("/api/local-models/readiness")), false);
  assert.equal(env.calls.some(call => call.url === "/api/local-models/prepare"
    || call.url === "/api/local-models/start"), false);
});

test("registered model can be explicitly rechecked while preserving the old selection", async () => {
  const model = id("f"), oldSelection = "text-model-old", newSelection = "text-model-current";
  let registration = modelRegistrationStatus(model, "registered", {selection_id:oldSelection});
  const env = setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${model}`,
    renderOnReload:true,
    handler:async (url, options) => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      if (url === `/api/local-workspace/artifacts/${model}`) return textMenuScratchModel(model);
      if (url === "/api/local-model-exports/status")
        return modelExportStatus({status:"completed", model_id:model});
      if (url === `/api/local-model-registrations/status?model_id=${model}`) return registration;
      if (url === "/api/local-model-registrations/register") {
        assert.equal(options.method, "POST");
        assert.deepEqual(JSON.parse(options.body), {model_id:model},
          "recheck submits the source identity only; the old selection is never rewritten");
        registration = modelRegistrationStatus(model, "registered", {selection_id:newSelection});
        return registration;
      }
      throw new Error(`unexpected route ${url}`);
    },
  });
  const page = await env.render();
  assert.match(text(page), /Runtime 会在决策前重新检查/);
  assert.match(text(page), /登记本身不会加载模型/);
  assert.ok(find(page, element => element.tagName === "A"
    && element.href === `?view=local-models&id=${oldSelection}`));
  assert.equal(action(page, "register-local-model").textContent, "重新核对登记");
  assert.equal(post(env.calls).length, 0, "registered status render preserves the old entry and performs no POST");

  await action(page, "register-local-model").onclick();
  assert.equal(post(env.calls).length, 1, "only the explicit recheck click writes");
  assert.equal(post(env.calls)[0].options.headers["X-CSRF-Token"], "registration-csrf");
  assert.ok(walk(env.livePage).some(element => element.tagName === "A"
    && element.href === `?view=local-models&id=${newSelection}`));
  assert.equal(post(env.calls).length, 1, "refresh reads the current binding without an extra write");
});

test("late or unknown registration response reconciles by GET without replay or stale-model display", async () => {
  const model = id("a"), other = id("b"), selection = "text-model-a";
  let resolveRegistration, notifyRegistration, modelState = modelRegistrationStatus(model);
  const started = new Promise(resolve => { notifyRegistration = resolve; });
  let statusReads = 0, currentArtifact = model;
  const env = setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${model}`,
    renderOnReload:true,
    handler:async (url, options) => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      const artifact = url.match(/^\/api\/local-workspace\/artifacts\/([a-f0-9]{64})$/);
      if (artifact) {
        currentArtifact = artifact[1];
        return textMenuScratchModel(artifact[1]);
      }
      if (url === "/api/local-model-exports/status")
        return modelExportStatus({status:"completed", model_id:currentArtifact});
      const status = url.match(/^\/api\/local-model-registrations\/status\?model_id=([a-f0-9]{64})$/);
      if (status) {
        statusReads++;
        return status[1] === model ? modelState : modelRegistrationStatus(other);
      }
      if (url === "/api/local-model-registrations/register") {
        assert.deepEqual(JSON.parse(options.body), {model_id:model});
        notifyRegistration();
        return new Promise(resolve => { resolveRegistration = () => {
          modelState = modelRegistrationStatus(model, "registered", {selection_id:selection});
          resolve(modelState);
        }; });
      }
      throw new Error(`unexpected route ${url}`);
    },
  });
  const first = await env.render();
  const click = action(first, "register-local-model").onclick();
  await started;
  env.navigate("local-workspace", `&id=${other}`);
  const second = await env.render();
  assert.equal(action(second, "register-local-model").disabled, true,
    "the account-wide command lock blocks another registration while the POST is pending");
  resolveRegistration();
  await click;
  assert.ok(statusReads >= 3, "late completion refreshes the current detail by GET");
  assert.equal(action(env.livePage, "register-local-model").disabled, false,
    "the new model keeps its own eligibility after the previous request settles");
  assert.doesNotMatch(text(env.livePage), /打开此模型选择/,
    "the previous model's selection result is not shown on the new detail");
  assert.equal(post(env.calls).length, 1);

  const unknownModel = id("c"), unknownSelection = "text-model-c";
  let unknownState = modelRegistrationStatus(unknownModel), unknownPosts = 0, unknownReads = 0;
  const unknown = setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${unknownModel}`,
    renderOnReload:true,
    handler:async url => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      if (url === `/api/local-workspace/artifacts/${unknownModel}`) return textMenuScratchModel(unknownModel);
      if (url === "/api/local-model-exports/status")
        return modelExportStatus({status:"completed", model_id:unknownModel});
      if (url === `/api/local-model-registrations/status?model_id=${unknownModel}`) {
        unknownReads++;
        return unknownState;
      }
      if (url === "/api/local-model-registrations/register") {
        unknownPosts++;
        unknownState = modelRegistrationStatus(unknownModel, "registered", {selection_id:unknownSelection});
        throw new Error("connection_lost_after_dispatch");
      }
      throw new Error(`unexpected route ${url}`);
    },
  });
  const unknownPage = await unknown.render();
  await action(unknownPage, "register-local-model").onclick();
  assert.equal(unknownPosts, 1, "an uncertain registration is never automatically replayed");
  assert.ok(unknownReads >= 2, "uncertain outcome uses only a read-only GET reconciliation");
  assert.ok(walk(unknown.livePage).some(element => element.tagName === "A"
    && element.href === `?view=local-models&id=${unknownSelection}`));
});

test("late export status for a previous model cannot repaint the current model detail", async () => {
  const first = id("a"), second = id("b");
  let resolveFirst, markFirstStarted;
  const firstStarted = new Promise(resolve => { markFirstStarted = resolve; });
  let statusReads = 0;
  const env = setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${first}`,
    handler:async url => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      const artifact = url.match(/^\/api\/local-workspace\/artifacts\/([a-f0-9]{64})$/);
      if (artifact) return textMenuScratchModel(artifact[1]);
      if (url === "/api/local-model-exports/status") {
        statusReads++;
        if (statusReads === 1) return new Promise(resolve => {
          resolveFirst = resolve;
          markFirstStarted();
        });
        return modelExportStatus({status:"idle"});
      }
      throw new Error(`unexpected route ${url}`);
    },
  });
  const previousPage = env.render();
  await firstStarted;
  env.navigate("local-workspace", `&id=${second}`);
  const currentPage = await env.render();
  resolveFirst(modelExportStatus({status:"completed", model_id:first, payload_bytes:777}));
  await previousPage;
  assert.doesNotMatch(text(currentPage), /导出校验完成|777 B/);
  assert.match(text(currentPage), /尚无导出结果/);
  assert.equal(post(env.calls).length, 0);
});

test("known local training schema only exposes safe codes, stages and exact artifact identities", async () => {
  const cases = [
    {
      availability:"preparation_required", reason:"secretcredential",
      operation:{status:"idle"},
    },
    {
      availability:"ready", csrf_token:"training-csrf",
      operation:{status:"pending", dataset_id:id("a"), stage:"/private/secret/stage.txt"},
    },
    {
      availability:"ready", csrf_token:"training-csrf",
      operation:{status:"failed", dataset_id:id("a"), error_code:"secretcredential"},
    },
    {
      availability:"ready", csrf_token:"training-csrf",
      operation:{status:"completed", dataset_id:id("a"), operation_id:id("b").slice(0, 32),
        run_id:"/private/secret/run", checkpoint_id:"/private/secret/checkpoint",
        result_id:"/private/secret/result", model_id:id("c"), evaluation_id:"/private/secret/evaluation"},
    },
  ];
  for (const status of cases) {
    const env = localTrainingEnv({trainingStatus:{schema:"stpd/local-training-operation-v1", ...status}});
    const page = await env.render();
    assert.doesNotMatch(text(page), /\/private\/secret/);
    assert.doesNotMatch(text(page), /secretcredential/);
    if (status.operation.stage) assert.match(text(page), /训练阶段未知/);
    if (status.operation.error_code) assert.match(text(page), /训练条件暂不可用/);
    assert.equal(post(env.calls).length, 0);
  }
});

test("empty local training status response is rejected before reading csrf", async () => {
  const env = localTrainingEnv({trainingStatus:() => null});
  const page = await env.render();
  assert.match(text(page), /训练服务暂不可用/);
  assert.equal(walk(page).some(element => element.dataset?.action === "start-local-training"), false);
  assert.equal(post(env.calls).length, 0);
});

test("late local training status cannot render a start action on a changed page", async () => {
  let releaseStatus;
  const env = localTrainingEnv({trainingStatus:() => new Promise(resolve => { releaseStatus = resolve; })});
  const rendering = env.render();
  for (let index = 0; index < 10 && !releaseStatus; index++)
    await new Promise(resolve => setImmediate(resolve));
  assert.equal(typeof releaseStatus, "function");
  env.navigate("evaluations");
  releaseStatus({schema:"stpd/local-training-operation-v1", availability:"ready", csrf_token:"token",
    operation:{status:"idle"}});
  const page = await rendering;
  assert.equal(walk(page).some(element => element.dataset?.action === "start-local-training"), false);
  assert.equal(post(env.calls).length, 0);
});

test("dataset detail retains preparation and recoverable continuation until curation is ready", async () => {
  const dataset = id("a");
  for (const [status, retryAvailable, actionExpected] of [
    ["preparation_required", false, true],
    ["recovery_required", false, false],
    ["recovery_required", true, true],
  ]) {
    const env = setup({
      identity: {status: "local_only"}, view: "local-workspace", query: `&id=${dataset}`,
      curationStatus: {
        schema: "stpd/local-curation-preparation-v1", status, retry_available: retryAvailable,
        reason: status === "recovery_required" ? "preparation_interrupted" : undefined,
        csrf_token: "browser-csrf",
      },
      handler: async url => {
        if (url === "/api/local-workspace/managed") return {status: "ready", workspace_id: id("d")};
        if (url === `/api/local-workspace/artifacts/${dataset}`) return {
          kind: "dataset", artifact_id: dataset,
          parameters: {schema: "stpd/curated-decision-dataset-v1", records: 3, purpose: "training"},
          parents: [], payloads: [],
        };
        throw new Error(`unexpected route ${url}`);
      },
    });
    const page = await env.render();
    assert.equal(Boolean(walk(page).find(element => element.dataset?.action === "prepare-local-curation")), actionExpected);
    if (status === "preparation_required") assert.match(text(page), /首次准备会在本机建立用途记录/);
    if (status === "recovery_required") assert.match(text(page), retryAvailable
      ? /上次准备遇到可继续的暂时错误/ : /用途记录需要恢复核对/);
    assert.equal(post(env.calls).length, 0);
  }
});

test("offline evaluation catalog is a no-login metadata page and never reads summaries", async () => {
  const evaluation = id("a"), sealed = id("b");
  const env = setup({
    identity: {status: "signed_out"}, view: "evaluations",
    handler: async url => {
      if (url === "/api/local-workspace?kind=offline_evaluation&limit=25&offset=0") return {
        schema: "stpd/local-workspace-inventory-v1", kind: "offline_evaluation", total: 2,
        items: [{artifact_id: evaluation, kind: "offline_evaluation", parents: [], payloads: [],
          parameters: {schema: "stpd/offline-ranking-evaluation-v1", partition: "dev", baseline: "model"}},
        {artifact_id: sealed, kind: "offline_evaluation", parents: [], payloads: [],
          parameters: {schema: "stpd/offline-ranking-evaluation-v1", partition: "test"}}],
      };
      if (url === "/api/local-models") return {evaluations: []};
      throw new Error(`unexpected route ${url}`);
    },
  });
  const page = await env.render();
  assert.match(text(page), /本机离线评估/);
  assert.match(text(page), /描述性离线统计/);
  assert.match(text(page), /开发集/);
  assert.doesNotMatch(text(page), new RegExp(sealed));
  assert.equal(find(page, element => element.tagName === "A" && element.href === `?view=local-workspace&id=${evaluation}`).textContent,
    `离线评估 · ${evaluation.slice(0, 16)}`);
  assert.equal(env.calls.some(call => call.url === `/api/local-workspace/evaluations/${evaluation}`), false);
  assert.equal(env.calls[0].url, "/api/local-workspace?kind=offline_evaluation&limit=25&offset=0");
  assert.equal(post(env.calls).length, 0);
});

test("offline evaluation catalog distinguishes empty, missing, unavailable, and unknown", async () => {
  for (const [response, expected, unexpected] of [
    [{schema:"stpd/local-workspace-inventory-v1", total:0, items:[]}, /还没有本机离线评估/, /读取失败/],
    [{schema:"stpd/local-workspace-status-v1", status:"not_configured"}, /尚未建立本机资料空间/, /还没有本机离线评估/],
    [{schema:"stpd/local-workspace-status-v1", status:"unavailable", error_code:"registry_unavailable"}, /本机离线评估暂不可用/, /还没有本机离线评估/],
    [{schema:"future/local-evaluation-index-v9", total:0, items:[]}, /目录格式未知/, /还没有本机离线评估/],
  ]) {
    const env = setup({
      identity: {status: "local_only"}, view: "evaluations",
      handler: async url => {
        if (url === "/api/local-workspace?kind=offline_evaluation&limit=25&offset=0") return response;
        if (url === "/api/local-models") return {evaluations: []};
        throw new Error(`unexpected route ${url}`);
      },
    });
    const page = await env.render();
    assert.match(text(page), expected);
    assert.doesNotMatch(text(page), unexpected);
    assert.equal(post(env.calls).length, 0);
  }
});

test("offline evaluation catalog paginates the exact metadata inventory query", async () => {
  const offsets = [];
  const env = setup({
    identity: {status: "signed_out"}, view: "evaluations",
    handler: async url => {
      if (url.startsWith("/api/local-workspace?")) {
        offsets.push(url);
        const offset = Number(new URL(url, "http://localhost").searchParams.get("offset"));
        const evaluation = id(offset === 0 ? "a" : "b");
        return {schema: "stpd/local-workspace-inventory-v1", kind: "offline_evaluation",
          total: 26, offset, limit: 25,
          items: [{artifact_id: evaluation, kind: "offline_evaluation", parents: [], payloads: [],
            parameters: {schema: "unknown-evaluation-v7", partition: "dev"}}]};
      }
      if (url === "/api/local-models") return {evaluations: []};
      throw new Error(`unexpected route ${url}`);
    },
  });
  const first = await env.render();
  assert.match(text(first), /unknown-evaluation-v7/);
  assert.match(text(first), /metadata页 1–25 \/ 26/);
  assert.ok(action(first, "local-offline-evaluations-next"));
  assert.equal(env.calls.some(call => call.url.includes("/api/local-workspace/evaluations/")), false);
  await action(first, "local-offline-evaluations-next").onclick();
  const second = await env.render();
  assert.match(text(second), /metadata页 26–26 \/ 26/);
  assert.ok(action(second, "local-offline-evaluations-prev"));
  assert.equal(offsets[0], "/api/local-workspace?kind=offline_evaluation&limit=25&offset=0");
  assert.equal(offsets.at(-1), "/api/local-workspace?kind=offline_evaluation&limit=25&offset=25");
  assert.equal(post(env.calls).length, 0);
});

test("offline evaluation detail reads one exact dev recorded-report summary", async () => {
  const evaluation = id("c");
  const env = setup({
    identity: {status: "signed_out"}, view: "local-workspace", query: `&id=${evaluation}`,
    curationStatus: {schema:"stpd/local-curation-preparation-v1", status:"ready"},
    handler: async url => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      if (url === `/api/local-workspace/artifacts/${evaluation}`) return {
        artifact_id:evaluation, kind:"offline_evaluation",
        parameters:{schema:"stpd/offline-ranking-evaluation-v1", partition:"dev"},
      };
      if (url === `/api/local-workspace/evaluations/${evaluation}`) return {
        schema:"stpd/local-offline-evaluation-summary-v1", evaluation_id:evaluation,
        evaluation_schema:"stpd/offline-ranking-evaluation-v1", model_id:id("a"), model_view_id:id("b"),
        model_recipe:null, view_schema:"stpd/fullrun-model-view-v1", partition:"dev", baseline:"model",
        qualification:"not_claimed", scientific_verdict:"not_claimed",
        validation_scope:"recorded_report_and_parent_identities", decision_count:12,
        reported_run_groups:2, multi_candidate_count:8,
        overall:{count:12, top1:0.123456, mrr:0.7, nll:0.9, confidence:0.4, margin:0.2},
        interpretation:"producer_recorded_summary_not_full_lineage_or_quality_verification",
      };
      throw new Error(`unexpected route ${url}`);
    },
  });
  const page = await env.render();
  assert.match(text(page), /已记录的开发集结果/);
  assert.match(text(page), /未重新核验原始数据、模型权重或完整训练来源/);
  assert.match(text(page), /记录中的对局分组数（未复核独立性）/);
  assert.doesNotMatch(text(page), /录制分组数（不代表独立游戏局）/);
  assert.match(text(page), /总体记录指标/);
  assert.match(text(page), /首选命中率（非胜率，Top-1）/);
  assert.match(text(page), /0\.1235/);
  assert.doesNotMatch(text(page), /0\.123456/);
  assert.equal(find(page, element => element.tagName === "A" && element.href === `?view=local-workspace&id=${id("a")}`).textContent,
    `查看本机模型 · ${id("a").slice(0, 16)}`);
  assert.equal(find(page, element => element.tagName === "A" && element.href === `?view=local-workspace&id=${id("b")}`).textContent,
    `查看本机模型视图 · ${id("b").slice(0, 16)}`);
  assert.equal(env.calls.filter(call => call.url === `/api/local-workspace/evaluations/${evaluation}`).length, 1);
  assert.equal(post(env.calls).length, 0);
});

test("Human input report uses session-scoped grouping language and unknown independence conservatively", async () => {
  const evaluation = id("9"), schema = "stpd/stage1a-ranking-evaluation-v1";
  for (const [grouping, independence, expectedLabel, expectedNote, hidden] of [
    ["session_scoped_run_group", "unknown_across_sessions",
      /录制分组数（不代表独立游戏局）/, /独立性[\s\S]*未知（按录制分组计数，不证明来自不同游戏局）/, null],
    ["future_grouping", "future_independence",
      /记录中的对局分组数（未复核独立性）/, /未知（分组信息未确认，不据此认定为独立游戏局）/,
      /future_grouping|future_independence/],
  ]) {
    const env = setup({
      identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${evaluation}`,
      handler:async url => {
        if (url === "/api/local-workspace/managed") return {
          schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
        };
        if (url === `/api/local-workspace/artifacts/${evaluation}`) return {
          artifact_id:evaluation, kind:"offline_evaluation",
          parameters:{schema, partition:"dev"},
        };
        if (url === `/api/local-workspace/evaluations/${evaluation}`) return {
          schema:"stpd/local-offline-evaluation-summary-v1", evaluation_id:evaluation,
          evaluation_schema:schema, model_id:id("a"), model_view_id:id("b"),
          model_recipe:"stage1a.b.s.v2", view_schema:"stpd/human-text-input-bc-view-v2",
          partition:"dev", baseline:"model", qualification:"engineering_only",
          scientific_verdict:"not_claimed", validation_scope:"recorded_report_and_parent_identities",
          decision_count:5, reported_run_groups:2, multi_candidate_count:0,
          grouping, native_run_independence:independence,
          overall:{count:5, top1:1, mrr:1, nll:0, confidence:0, margin:0},
          interpretation:"producer_recorded_summary_not_full_lineage_or_quality_verification",
        };
        throw new Error(`unexpected route ${url}`);
      },
    });
    const page = await env.render();
    assert.match(text(page), expectedLabel);
    assert.match(text(page), expectedNote);
    if (hidden) assert.doesNotMatch(text(page), hidden);
    assert.equal(env.calls.filter(call => call.url === `/api/local-workspace/evaluations/${evaluation}`).length, 1);
    assert.equal(post(env.calls).length, 0);
  }
});

test("offline evaluation detail supports Stage1a recorded summaries and optional baselines", async () => {
  const evaluation = id("e");
  const stage1a = "stpd/stage1a-ranking-evaluation-v1";
  const metrics = {count:4, top1:0.25, mrr:0.5, nll:1.2, confidence:0.3, margin:0.1};
  const env = setup({
    identity: {status: "signed_out"}, view: "local-workspace", query: `&id=${evaluation}`,
    curationStatus: {schema:"stpd/local-curation-preparation-v1", status:"ready"},
    handler: async url => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      if (url === `/api/local-workspace/artifacts/${evaluation}`) return {
        artifact_id:evaluation, kind:"offline_evaluation",
        parameters:{schema:stage1a, partition:"dev"},
      };
      if (url === `/api/local-workspace/evaluations/${evaluation}`) return {
        schema:"stpd/local-offline-evaluation-summary-v1", evaluation_id:evaluation,
        evaluation_schema:stage1a, model_id:id("a"), model_view_id:id("b"),
        model_recipe:"stage1a.b.s.v2", view_schema:"stpd/decision-model-view-v1", partition:"dev",
        baseline:"model", qualification:"engineering_only", scientific_verdict:"not_claimed",
        validation_scope:"recorded_report_and_parent_identities", decision_count:4,
        reported_run_groups:1, multi_candidate_count:3, overall:metrics,
        baselines:{uniform_legal:metrics, action_only:metrics},
        interpretation:"producer_recorded_summary_not_full_lineage_or_quality_verification",
      };
      throw new Error(`unexpected route ${url}`);
    },
  });
  const page = await env.render();
  assert.match(text(page), new RegExp(stage1a.replaceAll("/", "\\/")));
  assert.match(text(page), /均匀合法动作基准/);
  assert.match(text(page), /仅动作基准/);
  assert.equal(env.calls.filter(call => call.url === `/api/local-workspace/evaluations/${evaluation}`).length, 1);
  assert.equal(post(env.calls).length, 0);
});

test("unknown offline summary does not render arbitrary response payloads", async () => {
  const evaluation = id("d");
  const env = setup({
    identity:{status:"signed_out"}, view:"local-workspace", query:`&id=${evaluation}`,
    curationStatus:{schema:"stpd/local-curation-preparation-v1", status:"ready"},
    handler: async url => {
      if (url === "/api/local-workspace/managed") return {
        schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
      };
      if (url === `/api/local-workspace/artifacts/${evaluation}`) return {
        artifact_id:evaluation, kind:"offline_evaluation",
        parameters:{schema:"stpd/offline-ranking-evaluation-v1", partition:"dev"},
      };
      if (url === `/api/local-workspace/evaluations/${evaluation}`) return {
        schema:"future-summary", rows:[{state_text:"PRIVATE_ROW_SENTINEL"}],
        path:"PRIVATE_PATH_SENTINEL",
      };
      throw new Error(`unexpected route ${url}`);
    },
  });
  const page = await env.render();
  assert.match(text(page), /评估摘要格式或核验范围未知/);
  assert.doesNotMatch(text(page), /PRIVATE_ROW_SENTINEL|PRIVATE_PATH_SENTINEL/);
  assert.equal(post(env.calls).length, 0);
});

test("offline evaluation detail never requests sealed-test or unknown-partition summaries", async () => {
  for (const [parameters, message] of [
    [{schema:"stpd/offline-ranking-evaluation-v1", partition:"test"}, /封存测试评估不会在此读取或展示/],
    [{schema:"future/evaluation-v8", partition:"unknown"}, /未标明可展示的开发集分区/],
    [{schema:"future/evaluation-v8", partition:"dev"}, /格式暂不支持指标摘要/],
  ]) {
    const evaluation = id("d");
    const env = setup({
      identity: {status: "signed_out"}, view: "local-workspace", query: `&id=${evaluation}`,
      curationStatus: {schema:"stpd/local-curation-preparation-v1", status:"ready"},
      handler: async url => {
        if (url === "/api/local-workspace/managed") return {
          schema:"stpd/managed-local-workspace-registration-v1", status:"ready", curation_status:"ready",
        };
        if (url === `/api/local-workspace/artifacts/${evaluation}`) return {
          artifact_id:evaluation, kind:"offline_evaluation", parameters,
        };
        if (url.includes("/api/local-workspace/evaluations/")) throw new Error("sealed/unknown summary requested");
        throw new Error(`unexpected route ${url}`);
      },
    });
    const page = await env.render();
    assert.match(text(page), message);
    assert.equal(env.calls.some(call => call.url.includes("/api/local-workspace/evaluations/")), false);
    assert.equal(post(env.calls).length, 0);
  }
});

test("local inventory kind filter uses exact backend kinds and preserves search", async () => {
  const requested = [];
  const env = setup({
    identity: {status: "signed_out"}, view: "local-workspace",
    handler: async url => {
      if (url === "/api/local-workspace/managed") return {status: "ready", workspace_id: "c".repeat(32)};
      if (url.startsWith("/api/local-workspace?")) {
        requested.push(url);
        return {schema: "stpd/local-workspace-inventory-v1", total: 0, items: []};
      }
      throw new Error(`unexpected route ${url}`);
    },
  });
  const initial = await env.render();
  assert.match(text(initial), /全部类型/);
  const search = field(initial, "local-workspace-search");
  search.value = "my run";
  await action(initial, "search-local-workspace").onclick();
  const searched = await env.render();
  const kind = field(searched, "local-workspace-kind");
  assert.deepEqual(kind.children.map(option => option.value), [
    "", "evidence", "dataset", "model_view", "feature_set", "feature_job", "training_input",
    "experiment", "run", "checkpoint", "model", "offline_evaluation", "live_evaluation",
    "performance", "run_event", "run_result", "gold_tasks", "gold_labels", "protocol", "analysis",
  ]);
  kind.value = "checkpoint";
  kind.onchange();
  await env.render();
  assert.equal(requested[0], "/api/local-workspace?limit=25&offset=0");
  assert.equal(requested.at(-1), "/api/local-workspace?limit=25&offset=0&q=my+run&kind=checkpoint");
  assert.equal(post(env.calls).length, 0);
});

test("local workspace category shortcuts are read-only and preserve Human selection", async () => {
  const source = id("a"), secondSource = id("e"), dataset = id("b"), model = id("c"), report = id("d");
  const requests = [];
  const itemsByCategory = {
    recordings: [source, secondSource].map(artifact_id => ({artifact_id, kind:"evidence",
      parameters:{schema:"stpd/local-verified-bundle-v1"}})),
    datasets: [{artifact_id:dataset, kind:"dataset", parameters:{schema:"stpd/curated-decision-dataset-v1"}}],
    models: [{artifact_id:model, kind:"model", parameters:{schema:"stpd/stage1a-model-v1"}}],
    reports: [{artifact_id:report, kind:"offline_evaluation", parameters:{schema:"stpd/stage1a-evaluation-v1"}}],
    all: [
      {artifact_id:source, kind:"evidence", parameters:{schema:"stpd/local-verified-bundle-v1"}},
      {artifact_id:secondSource, kind:"evidence", parameters:{schema:"stpd/local-verified-bundle-v1"}},
      {artifact_id:dataset, kind:"dataset", parameters:{schema:"stpd/curated-decision-dataset-v1"}},
    ],
  };
  const env = localHumanDatasetEnv({items:url => {
    requests.push(url);
    const params = new URLSearchParams(url.split("?")[1]);
    return itemsByCategory[params.get("category") || "all"];
  }, total:26});

  let page = await env.render();
  assert.match(text(page), /全部 · 共 26 项/);
  for (const label of ["录制", "数据集", "模型", "报告", "全部"])
    assert.ok(walk(page).some(element => element.tagName === "BUTTON" && element.textContent === label));
  for (const checkbox of walk(page).filter(element => element.name === "local-human-source")) {
    checkbox.checked = true;
    checkbox.onchange();
  }
  assert.match(text(page), /已选 2 份录制/);
  assert.equal(post(env.calls).length, 0);

  const search = field(page, "local-workspace-search");
  search.value = "keep this search";
  await action(page, "search-local-workspace").onclick();
  page = await env.render();
  const kind = field(page, "local-workspace-kind");
  kind.value = "checkpoint";
  kind.onchange();
  page = await env.render();
  await action(page, "local-workspace-next").onclick();
  page = await env.render();
  assert.equal(new URLSearchParams(requests.at(-1).split("?")[1]).get("offset"), "25");
  await action(page, "local-workspace-category-recordings").onclick();
  page = await env.render();
  assert.match(text(page), /录制 · 共 26 项/);
  assert.equal(field(page, "local-workspace-kind").value, "");
  assert.equal(walk(page).filter(element => element.name === "local-human-source").length, 2);
  assert.equal(walk(page).filter(element => element.name === "local-human-source").every(element => element.checked), true);
  const recordingQuery = new URLSearchParams(requests.at(-1).split("?")[1]);
  assert.equal(recordingQuery.get("category"), "recordings");
  assert.equal(recordingQuery.get("q"), "keep this search");
  assert.equal(recordingQuery.get("kind"), null, "category shortcut clears the old advanced type filter");
  assert.equal(recordingQuery.get("offset"), "0");

  await action(page, "local-workspace-category-datasets").onclick();
  page = await env.render();
  assert.match(text(page), /数据集 · 共 26 项/);
  assert.match(text(page), /已选 2 份录制/);
  assert.equal(walk(page).some(element => element.name === "local-human-source"), false,
    "filtered-out selection remains in the shared draft while its row is hidden");

  await action(page, "local-workspace-category-models").onclick();
  page = await env.render();
  assert.match(text(page), /模型 · 共 26 项/);
  await action(page, "local-workspace-category-reports").onclick();
  page = await env.render();
  assert.match(text(page), /报告 · 共 26 项/);
  await action(page, "local-workspace-category-all").onclick();
  page = await env.render();
  assert.match(text(page), /全部 · 共 26 项/);
  assert.equal(walk(page).filter(element => element.name === "local-human-source").length, 2);
  assert.equal(walk(page).filter(element => element.name === "local-human-source").every(element => element.checked), true,
    "returning to all restores both checkboxes from the same ordered selection draft");
  const allQuery = new URLSearchParams(requests.at(-1).split("?")[1]);
  assert.equal(allQuery.has("category"), false, "all remains compatible with the unfiltered listing");
  assert.equal(allQuery.get("q"), "keep this search");
  for (const category of ["recordings", "datasets", "models", "reports"])
    assert.ok(requests.some(url => new URLSearchParams(url.split("?")[1]).get("category") === category),
      `${category} shortcut sends its typed category to the owner API`);
  assert.equal(post(env.calls).length, 0, "category changes and rendering issue GETs only");
});

test("pending dataset checks do not resubmit; failed and interrupted checks need a new click", async () => {
  const artifact = id("a");
  const pending = localDatasetEnv({artifact, datasetStatus: {
    schema: "stpd/local-dataset-operation-v1", availability: "ready", paired_training: [],
    operation: {status: "pending", artifact_id: artifact, purpose: "training", paired_training: null},
    csrf_token: "dataset-csrf",
  }});
  const pendingPage = await pending.render();
  assert.match(text(pendingPage), /不会重复提交/);
  assert.equal(action(pendingPage, "check-local-dataset").disabled, true);
  await action(pendingPage, "refresh-local-dataset-status").onclick();
  assert.equal(post(pending.calls).length, 0);

  const publishing = localDatasetEnv({artifact, datasetStatus: {
    schema: "stpd/local-dataset-operation-v1", availability: "ready", paired_training: [],
    operation: {status: "pending", artifact_id: artifact, purpose: "training",
      paired_training: null, preview_id: "c".repeat(32)},
    csrf_token: "dataset-csrf",
  }});
  const publishingPage = await publishing.render();
  assert.match(text(publishingPage), /正在创建这份数据集/);
  assert.equal(action(publishingPage, "check-local-dataset").textContent, "正在创建");
  assert.equal(action(publishingPage, "refresh-local-dataset-status").textContent, "刷新创建状态");
  assert.equal(post(publishing.calls).length, 0);

  for (const status of ["failed", "interrupted"]) {
    const env = localDatasetEnv({artifact, datasetStatus: {
      schema: "stpd/local-dataset-operation-v1", availability: "ready", paired_training: [],
      operation: {status, artifact_id: artifact, purpose: "training", paired_training: null,
        error_code: "synthetic_failure"}, csrf_token: "dataset-csrf",
    }, datasetHandler: async (url) => {
      if (url === "/api/local-datasets/preview") return {status: "pending"};
      throw new Error(`unexpected route ${url}`);
    }});
    const page = await env.render();
    assert.match(text(page), /不会自动重试/);
    assert.equal(post(env.calls).length, 0);
    await action(page, "check-local-dataset").onclick();
    assert.equal(post(env.calls).length, 1);
    assert.equal(post(env.calls)[0].url, "/api/local-datasets/preview");
  }
});

test("recoverable local publication is checked only on explicit click", async () => {
  const artifact = id("a"), previewId = "b".repeat(32);
  let publishes = 0;
  const env = localDatasetEnv({artifact, datasetStatus: {
    schema: "stpd/local-dataset-operation-v1", availability: "ready", paired_training: [],
    operation: {status: "interrupted", artifact_id: artifact, purpose: "training",
      paired_training: null, preview_id: previewId, recovery_available: true,
      can_publish: false},
    csrf_token: "dataset-csrf",
  }, datasetHandler: async (url, options) => {
    assert.equal(url, "/api/local-datasets/publish");
    assert.equal(options.headers["X-CSRF-Token"], "dataset-csrf");
    publishes += 1;
    return {status: "failed", recovery_available: false};
  }});
  const page = await env.render();
  const recover = action(page, "recover-local-dataset-publication");
  assert.equal(action(page, "check-local-dataset").disabled, true);
  assert.match(text(page), /请先点击“核对上次创建结果”/);
  assert.equal(post(env.calls).length, 0);
  await action(page, "refresh-local-dataset-status").onclick();
  assert.equal(post(env.calls).length, 0);
  await recover.onclick();
  assert.equal(publishes, 1);
  assert.equal(post(env.calls).length, 1);
  assert.deepEqual(body(post(env.calls)[0]), {preview_id: previewId});
  assert.equal(recover.disabled, true);
  await recover.onclick();
  assert.equal(post(env.calls).length, 1);
});

test("local publication recovery requires exact backend availability and CSRF", async () => {
  const artifact = id("a");
  for (const operation of [
    {status: "failed", artifact_id: artifact, purpose: "training", paired_training: null,
      preview_id: "c".repeat(32), recovery_available: false, can_publish: false},
    {status: "interrupted", artifact_id: artifact, purpose: "training", paired_training: null,
      preview_id: "not-a-preview-id", recovery_available: true, can_publish: false},
  ]) {
    const env = localDatasetEnv({artifact, datasetStatus: {
      schema: "stpd/local-dataset-operation-v1", availability: "ready", paired_training: [],
      operation, csrf_token: "dataset-csrf",
    }});
    const page = await env.render();
    assert.equal(walk(page).some(element =>
      element.dataset?.action === "recover-local-dataset-publication"), false);
    assert.equal(post(env.calls).length, 0);
  }

  const noCsrf = localDatasetEnv({artifact, datasetStatus: {
    schema: "stpd/local-dataset-operation-v1", availability: "ready", paired_training: [],
    operation: {status: "failed", artifact_id: artifact, purpose: "training",
      paired_training: null, preview_id: "d".repeat(32), recovery_available: true,
      can_publish: false},
  }});
  const page = await noCsrf.render();
  const recover = action(page, "recover-local-dataset-publication");
  assert.equal(recover.disabled, true);
  await recover.onclick();
  assert.equal(post(noCsrf.calls).length, 0);

  const recoveryRequired = localDatasetEnv({artifact, datasetStatus: {
    schema: "stpd/local-dataset-operation-v1", availability: "ready", paired_training: [],
    operation: {status: "failed", artifact_id: artifact, purpose: "training",
      paired_training: null, recovery_available: false,
      error_code: "publication_recovery_required", can_publish: false},
    csrf_token: "dataset-csrf",
  }});
  const blockedPage = await recoveryRequired.render();
  assert.match(text(blockedPage), /恢复用途记录后才能继续/);
  assert.equal(action(blockedPage, "check-local-dataset").disabled, true);
  assert.equal(walk(blockedPage).some(element =>
    element.dataset?.action === "recover-local-dataset-publication"), false);
  assert.equal(post(recoveryRequired.calls).length, 0);
});

test("matching preview publishes once and links only its local result", async () => {
  const artifact = id("a"), dataset = id("d"), previewId = "c".repeat(32);
  let operation = {status: "preview_ready", artifact_id: artifact, purpose: "training",
    paired_training: null, preview_id: previewId, selected: 3,
    split_status: "insufficient_independent_run_components",
    exclusions: {duplicate: 1}, can_publish: true};
  const env = localDatasetEnv({artifact, datasetStatus: () => ({
      schema: "stpd/local-dataset-operation-v1", availability: "ready",
      paired_training: [], operation, csrf_token: "dataset-csrf",
    }),
    datasetHandler: async (url, options) => {
      if (url === "/api/local-datasets/publish") {
        operation = {...operation, status: "completed", result_artifact_id: dataset};
        return {status: "completed"};
      }
      throw new Error(`unexpected route ${url} ${options.method}`);
    }});
  const page = await env.render();
  assert.match(text(page), /3/);
  assert.match(text(page), /独立对局不足，尚不能形成独立划分/);
  assert.match(text(page), /insufficient_independent_run_components/);
  assert.match(text(page), /duplicate/);
  assert.equal(post(env.calls).length, 0);
  await action(page, "publish-local-dataset").onclick();
  assert.equal(post(env.calls).length, 1);
  assert.equal(post(env.calls)[0].url, "/api/local-datasets/publish");
  assert.deepEqual(body(post(env.calls)[0]), {preview_id: previewId});
  const completed = await env.render();
  const resultLink = find(completed, element => element.tagName === "A"
    && element.textContent === "打开本机数据集");
  assert.equal(resultLink.href, `?view=local-workspace&id=${dataset}`);
});

test("changed or mismatched selection cannot confirm an old preview or show another result", async () => {
  const artifact = id("a"), dataset = id("d"), other = id("e");
  const env = localDatasetEnv({artifact, datasetStatus: {
    schema: "stpd/local-dataset-operation-v1", availability: "ready",
    paired_training: [{artifact_id: id("b"), records: 4}],
    operation: {status: "preview_ready", artifact_id: artifact, purpose: "training",
      paired_training: null, preview_id: "f".repeat(32), can_publish: true},
    csrf_token: "dataset-csrf",
  }, datasetHandler: async (url) => {
    if (url === "/api/local-datasets/preview") return {status: "pending"};
    throw new Error(`unexpected route ${url}`);
  }});
  const page = await env.render();
  const confirm = action(page, "publish-local-dataset");
  const purpose = field(page, "local-dataset-purpose");
  purpose.value = "test";
  purpose.onchange();
  assert.equal(confirm.disabled, true);
  assert.match(text(page), /旧预览失效，请重新检查/);
  await confirm.onclick();
  assert.equal(post(env.calls).length, 0);
  await action(page, "check-local-dataset").onclick();
  assert.equal(post(env.calls).length, 1);
  assert.equal(post(env.calls)[0].url, "/api/local-datasets/preview");
  assert.deepEqual(body(post(env.calls)[0]), {
    artifact_id: artifact, purpose: "test", paired_training: null,
  });

  const stalePreview = localDatasetEnv({artifact, datasetStatus: {
    schema: "stpd/local-dataset-operation-v1", availability: "ready", paired_training: [],
    operation: {status: "preview_ready", artifact_id: other, purpose: "training",
      paired_training: null, preview_id: "e".repeat(32), can_publish: true},
  }});
  const stalePage = await stalePreview.render();
  assert.match(text(stalePage), /对应另一份录制/);
  assert.equal(walk(stalePage).some(element => element.dataset?.action === "publish-local-dataset"), false);
  assert.equal(post(stalePreview.calls).length, 0);

  const wrongResult = localDatasetEnv({artifact, datasetStatus: {
    schema: "stpd/local-dataset-operation-v1", availability: "ready", paired_training: [],
    operation: {status: "completed", artifact_id: other, purpose: "training",
      paired_training: null, result_artifact_id: dataset},
  }});
  const wrongPage = await wrongResult.render();
  assert.doesNotMatch(text(wrongPage), /打开本机数据集/);
  assert.equal(post(wrongResult.calls).length, 0);
});

test("Gold preview cannot publish when the backend reports it is not ready", async () => {
  const artifact = id("a");
  const env = localDatasetEnv({artifact, datasetStatus: {
    schema: "stpd/local-dataset-operation-v1", availability: "ready", paired_training: [],
    operation: {status: "preview_ready", artifact_id: artifact, purpose: "gold",
      paired_training: null, preview_id: "d".repeat(32), selected: 2,
      split_status: "purpose_assigned", exclusions: {}, can_publish: false,
      error_code: "gold_reserved_data"},
    csrf_token: "dataset-csrf",
  }});
  const page = await env.render();
  assert.match(text(page), /后端尚未确认/);
  assert.match(text(page), /已按所选评测用途分配/);
  assert.match(text(page), /该来源已保留为 Gold/);
  assert.match(text(page), /gold_reserved_data/);
  assert.equal(walk(page).some(element => element.dataset?.action === "publish-local-dataset"), false);
  assert.equal(post(env.calls).length, 0);
});

test("local dataset blockers explain known reasons and retain unknown codes", async () => {
  const artifact = id("a");
  const cases = [
    ["legacy_gold_history_unknown", "相关旧资料的历史用途无法完整核实"],
    ["gold_source_inventory_pending", "还有来源未完成索引"],
    ["gold_already_in_other_dataset", "该来源已进入其他数据集"],
    ["gold_requires_gold_merge", "不能作为新的 Gold 重复创建"],
    ["gold_previously_used_for_training", "已有训练使用记录"],
    ["gold_reserved_data", "该来源已保留为 Gold"],
    ["empty_selection", "当前选择没有可保留的样本"],
  ];
  for (const [errorCode, message] of cases) {
    const env = localDatasetEnv({artifact, datasetStatus: {
      schema: "stpd/local-dataset-operation-v1", availability: "ready", paired_training: [],
      operation: {status: "preview_ready", artifact_id: artifact, purpose: "gold",
        paired_training: null, preview_id: "e".repeat(32), selected: 0,
        can_publish: false, error_code: errorCode},
      csrf_token: "dataset-csrf",
    }});
    const page = await env.render();
    assert.match(text(page), new RegExp(message));
    assert.match(text(page), new RegExp(errorCode));
    assert.equal(walk(page).some(element => element.dataset?.action === "publish-local-dataset"), false);
    assert.equal(post(env.calls).length, 0);
  }

  const unknown = localDatasetEnv({artifact, datasetStatus: {
    schema: "stpd/local-dataset-operation-v1", availability: "ready", paired_training: [],
    operation: {status: "preview_ready", artifact_id: artifact, purpose: "gold",
      paired_training: null, preview_id: "f".repeat(32), selected: 1,
      can_publish: false, error_code: "future_unmapped_reason"},
    csrf_token: "dataset-csrf",
  }});
  const unknownPage = await unknown.render();
  assert.match(text(unknownPage), /future_unmapped_reason/);
  assert.doesNotMatch(text(unknownPage), /future_unmapped_reason.*(已|不能|需要)/);
  assert.equal(post(unknown.calls).length, 0);
});

test("local dataset publish requires a backend-shaped preview identity", async () => {
  const artifact = id("a");
  const env = localDatasetEnv({artifact, datasetStatus: {
    schema: "stpd/local-dataset-operation-v1", availability: "ready", paired_training: [],
    operation: {status: "preview_ready", artifact_id: artifact, purpose: "training",
      paired_training: null, preview_id: "not-a-backend-id", selected: 2,
      split_status: "assigned", exclusions: {}, can_publish: true},
    csrf_token: "dataset-csrf",
  }});
  const page = await env.render();
  assert.match(text(page), /已分配训练\/开发样本/);
  assert.equal(walk(page).some(element => element.dataset?.action === "publish-local-dataset"), false);
  assert.match(text(page), /后端尚未确认/);
  assert.equal(post(env.calls).length, 0);
});

test("unconfigured local research workspace explains explicit registration", async () => {
  const env = setup({
    identity: {status: "signed_out"},
    view: "local-workspace",
    handler: async (url) => {
      if (url === "/api/local-workspace/managed") return {
        schema: "stpd/managed-local-workspace-registration-v1",
        status: "not_created",
        csrf_token: "session-csrf",
        orphaned_initializations: 0,
        requires_cloud_account: false,
      };
      assert.equal(url, "/api/local-workspace?limit=25&offset=0");
      return {
        schema: "stpd/local-workspace-status-v1",
        status: "not_configured",
        requires_cloud_account: false,
      };
    },
  });
  const page = await env.render();
  assert.match(text(page), /新建本机工作空间/);
  assert.match(text(page), /不会导入资料、开始训练或替换已连接的旧资料库/);
  assert.match(text(page), /尚未建立本机资料空间/);
  assert.equal(post(env.calls).length, 0);
});

test("empty local workspace reports zero items without an inverted range", async () => {
  const env = setup({
    identity: {status: "signed_out"},
    view: "local-workspace",
    handler: async (url) => url === "/api/local-workspace/managed" ? {
      schema: "stpd/managed-local-workspace-registration-v1",
      status: "ready",
      workspace_id: "c".repeat(32),
      created_at: "2026-09-27T00:00:00+00:00",
    } : {
      schema: "stpd/local-workspace-inventory-v1",
      source: "configured_local_artifact_store",
      total: 0,
      items: [],
    },
  });
  const page = await env.render();
  assert.match(text(page), /共 0 项/);
  assert.doesNotMatch(text(page), /1–0/);
});

test("explicit local workspace create uses the browser session and empty command body", async () => {
  let created = false;
  const localIdentity = {status: "local_only"};
  const env = setup({
    identity: localIdentity,
    view: "local-workspace",
    handler: async (url, options) => {
      if (url === "/api/local-workspace/managed") return {
        schema: "stpd/managed-local-workspace-registration-v1",
        status: created ? "ready" : "not_created",
        csrf_token: created ? undefined : "browser-csrf",
        orphaned_initializations: 0,
      };
      if (url === "/api/local-workspace/managed/create") {
        created = true;
        return {
          schema: "stpd/managed-local-workspace-registration-v1",
          status: "ready",
          workspace_id: "d".repeat(32),
        };
      }
      if (url === "/api/local-workspace?limit=25&offset=0") return created
        ? {schema: "stpd/local-workspace-inventory-v1", total: 0, items: []}
        : {schema: "stpd/local-workspace-status-v1", status: "not_configured"};
      throw new Error(`unexpected route ${url} ${options.method}`);
    },
  });
  const page = await env.render();
  assert.equal(localIdentity.csrf_token, undefined);
  assert.equal(env.calls[0].url, "/api/local-workspace/managed");
  assert.equal(env.calls[0].options.method || "GET", "GET");
  assert.equal(Object.keys(env.calls[0].options.headers).length, 0);
  await action(page, "create-managed-local-workspace").onclick();
  const writes = post(env.calls);
  assert.equal(writes.length, 1);
  assert.equal(writes[0].url, "/api/local-workspace/managed/create");
  assert.deepEqual(body(writes[0]), {});
  assert.equal(writes[0].options.headers["X-CSRF-Token"], "browser-csrf");
  assert.equal(writes[0].options.credentials, "same-origin");
  assert.doesNotMatch(text(page), /browser-csrf/);
  assert.equal(env.reloads, 1);
});
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
      policies: [{ selection_id: "audited-cpu", label: "Reviewed CPU",
        default_run_profile:"short", run_profiles:[
          {id:"short", label:"短局", limits:{max_submissions:16, max_policy_calls:32, deadline_ms:60000}},
          {id:"extended", label:"较长局（最多 30 分钟）", limits:{max_submissions:2000,
            max_policy_calls:4000, deadline_ms:1800000}},
        ]}],
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
  assert.deepEqual(body(post(env.calls)[0]), { selection_id: "audited-cpu", run_profile:"short" });
  assert.match(text(env.notice), /尚需完成实际加载/);
});

test("run profile is bound to the exact selection before load and never starts Auto", async () => {
  const profiles = modelHandler("/api/local-models", {method:"GET"}).policies[0];
  let status = {status:"idle", loaded:false, operation:null};
  const env = setup({view:"local-models", renderOnReload:true, handler:(url, options) => {
    if (url === "/api/local-models") return {policies:[
      {...profiles, selection_id:"audited-cpu", label:"Same model"},
      {...profiles, selection_id:"second-cpu", label:"Same model"},
    ], downloaded_models:[], evaluations:[]};
    if (url === "/api/local-models/status") return status;
    if (url === "/api/local-models/prepare") {
      status = {status:"loading", loaded:false, selection_id:"second-cpu",
        run_profile:"extended", operation:{id:"prepare-2", action:"prepare-and-load", status:"pending"}};
      return status;
    }
    return emptyList();
  }});
  let page = await env.render();
  assert.equal(field(page, "model-run-profile-audited-cpu").value, "short");
  const chosen = field(page, "model-run-profile-second-cpu");
  chosen.value = "extended"; chosen.onchange();
  page = await env.render();
  assert.equal(field(page, "model-run-profile-audited-cpu").value, "short");
  assert.equal(field(page, "model-run-profile-second-cpu").value, "extended");
  await action(page, "model-start-second-cpu").onclick();
  assert.deepEqual(body(post(env.calls)[0]), {selection_id:"second-cpu", run_profile:"extended"});
  assert.equal(post(env.calls).length, 1);
  assert.equal(action(env.livePage, "model-command-auto").disabled, true);
  status = {status:"loaded", loaded:true, selection_id:"second-cpu", run_profile:"extended",
    operation:{id:"prepare-2", action:"prepare-and-load", status:"completed"},
    runtime:{run_id:"run-2", lifecycle:"running", mode:"human", controller:"released",
      tainted:false, errors:[]}};
  await env.advanceTimer();
  assert.equal(field(env.livePage, "model-run-profile-second-cpu").disabled, true);
  assert.match(text(env.livePage), /较长局/);
  assert.equal(post(env.calls).length, 1, "loading and read-only convergence cannot enter Auto");
});

test("unknown run profile cannot become a prepare command or leak its label", async () => {
  const env = setup({view:"local-models", handler:(url, options) => {
    if (url === "/api/local-models") return {policies:[{selection_id:"audited-cpu",
      label:"Reviewed CPU", default_run_profile:"future-secret",
      run_profiles:[{id:"future-secret", label:"/private/secret", limits:null}]}],
      downloaded_models:[], evaluations:[]};
    return modelHandler(url, options);
  }});
  const page = await env.render();
  assert.equal(action(page, "model-start-audited-cpu").disabled, true);
  assert.doesNotMatch(text(page), /\/private\/secret/);
  await action(page, "model-start-audited-cpu").onclick();
  assert.equal(post(env.calls).length, 0);
});

test("a legacy short-only choice and an unknown loaded profile stay distinct", async () => {
  const env = setup({view:"local-models", handler:(url, options) => {
    if (url === "/api/local-models") return {policies:[{selection_id:"legacy-cpu",
      label:"Old model", default_run_profile:"short", run_profile_unavailable_reason:
        "extended_requires_text_menu_runtime", run_profiles:[{id:"short", label:"默认短局", limits:null}]}],
      downloaded_models:[], evaluations:[]};
    if (url === "/api/local-models/status") return {status:"loaded", loaded:true,
      selection_id:"legacy-cpu", run_profile:"/private/secret", operation:null,
      runtime:{run_id:"run-1", lifecycle:"running", mode:"human", controller:"released",
        tainted:false, errors:[]}};
    return modelHandler(url, options);
  }});
  const page = await env.render();
  assert.match(text(page), /运行配置未能核对/);
  assert.match(text(page), /较长局需要文本菜单运行环境/);
  assert.equal(field(page, "model-run-profile-legacy-cpu").disabled, true);
  assert.equal(action(page, "model-start-legacy-cpu").disabled, true);
  assert.doesNotMatch(text(page), /\/private\/secret/);
  assert.equal(post(env.calls).length, 0);
});

test("verified report shows action, delivery and budget facts without claiming a game outcome", async () => {
  const evaluation = {evaluation_id:id("e"), selection_id:"audited-cpu", run_id:"run-1",
    evidence_verification:"pass", event_count:12, game_outcome:"not_measured",
    recorded_action_verbs:{select:5, confirm:2}, native_submission_attempts:7,
    delivery_counts:{delivered:6, unknown:1}, budget_end_reason:"submission_attempt_limit",
    terminal_screen_observation:{status:"observed", result:"win", observation_count:1,
      first_event_sequence:11, last_event_sequence:11},
    budget_summary:{state:"exhausted", max_submissions:16, submissions_used:16,
      max_policy_calls:32, policy_calls_used:20, deadline_ms:60000, elapsed_ms:45000,
      exhausted_reason:"submission_attempt_limit", ended_reason:null}};
  const env = setup({view:"evaluations", handler:url =>
    url === "/api/local-models" ? {evaluations:[evaluation]} : emptyList()});
  const page = await env.render();
  assert.match(text(page), /select\s+5/);
  assert.match(text(page), /delivered\s+6/);
  assert.match(text(page), /自主提交次数已到限额/);
  assert.match(text(page), /本次记录观察到的结局页\s+胜利/);
  assert.match(text(page), /游戏结果\s+未测量/);
  assert.match(text(page), /不证明模型从开局完成整局/);
  evaluation.budget_end_reason = "/private/secret";
  evaluation.recorded_action_verbs = null;
  evaluation.terminal_screen_observation = {status:"ambiguous", result:null,
    observation_count:2, first_event_sequence:11, last_event_sequence:12};
  const unknown = await env.render();
  assert.match(text(unknown), /未从已验证证据确认/);
  assert.match(text(unknown), /本次记录观察到的结局页\s+信息冲突/);
  assert.doesNotMatch(text(unknown), /\/private\/secret/);
  delete evaluation.terminal_screen_observation;
  assert.match(text(await env.render()), /旧报告未提供此项观测/);
  evaluation.terminal_screen_observation = null;
  assert.match(text(await env.render()), /本次记录观察到的结局页\s+未观察到/);
  evaluation.evidence_verification = "failed";
  evaluation.recorded_action_verbs = {select:5};
  evaluation.budget_end_reason = "deadline";
  evaluation.terminal_screen_observation = {status:"observed", result:"loss",
    observation_count:1, first_event_sequence:12, last_event_sequence:12};
  const failed = text(await env.render());
  assert.match(failed, /证据未通过核验/);
  assert.doesNotMatch(failed, /select\s+5|自主运行时间已到限额|本次记录观察到的结局页\s+失败/);
});

test("accepted model load converges from pending by read-only status without another click", async () => {
  let status = {status:"idle", loaded:false, operation:null};
  const env = setup({view:"local-models", renderOnReload:true, handler:(url, options) => {
    if (url === "/api/local-models/status") return status;
    if (url === "/api/local-models/prepare" && options.method === "POST") {
      status = {status:"loading", loaded:false,
        operation:{id:"prepare-1", action:"prepare-and-load", status:"pending"}};
      return status;
    }
    return modelHandler(url, options);
  }});
  const page = await env.render();
  await action(page, "model-start-audited-cpu").onclick();
  assert.match(text(env.livePage), /服务处理中/);
  assert.equal(env.timerCount, 1);
  status = {status:"loaded", loaded:true,
    operation:{id:"prepare-1", action:"prepare-and-load", status:"completed"},
    runtime:{run_id:"run-1", lifecycle:"running", mode:"human", controller:"released",
      tainted:false, errors:[], last_receipt:null}};
  const before = env.reloads;
  await env.advanceTimer();
  assert.equal(env.reloads, before + 1);
  assert.match(text(env.livePage), /服务报告已加载/);
  assert.equal(env.timerCount, 1, "a loaded Human page can observe another client changing mode");
  await env.advanceTimer();
  assert.equal(env.reloads, before + 1);
  assert.equal(post(env.calls).length, 1);
});

test("accepted Auto reaches budget handoff by status reads and never restarts itself", async () => {
  const budget = (state, reason = null) => ({state, max_submissions:16, submissions_used:16,
    max_policy_calls:32, policy_calls_used:20, deadline_ms:60000, elapsed_ms:45000,
    remaining_ms:15000, exhausted_reason:reason, ended_reason:null});
  let status = {status:"loaded", loaded:true, operation:null,
    runtime:{run_id:"run-1", lifecycle:"running", mode:"human", controller:"released",
      tainted:false, errors:[], last_receipt:null}};
  const env = setup({view:"local-models", renderOnReload:true, handler:(url, options) => {
    if (url === "/api/local-models/status") return status;
    if (url === "/api/local-models/command" && options.method === "POST") {
      status = {...status, operation:{id:"auto-1", action:"auto", status:"pending"}};
      return status;
    }
    return modelHandler(url, options);
  }});
  const page = await env.render();
  await action(page, "model-command-auto").onclick();
  assert.equal(env.timerCount, 1);
  status = {...status, operation:{id:"auto-1", action:"auto", status:"completed"},
    runtime:{...status.runtime, mode:"auto", controller:"held", autonomy_budget:budget("active")}};
  await env.advanceTimer();
  assert.match(text(env.livePage), /正在使用/);
  assert.equal(env.timerCount, 1);
  status = {...status, runtime:{...status.runtime, mode:"human", controller:"released",
    autonomy_budget:budget("exhausted", "submission_attempt_limit")}};
  await env.advanceTimer();
  assert.match(text(env.livePage), /自主提交次数已到限额/);
  assert.match(text(env.livePage), /已释放/);
  assert.equal(env.timerCount, 1, "released Human status remains observable within the bound");
  await env.advanceTimer();
  assert.equal(env.timerCount, 1);
  assert.equal(post(env.calls).length, 1);
});

test("unchanged autonomous status updates budget without repeatedly reloading the page", async () => {
  let status = {status:"loaded", loaded:true,
    operation:{id:"auto-1", action:"auto", status:"completed"},
    runtime:{run_id:"run-1", lifecycle:"running", mode:"auto", controller:"held",
      tainted:false, errors:[], autonomy_budget:{state:"active", max_submissions:16,
        submissions_used:1, max_policy_calls:32, policy_calls_used:2, deadline_ms:60000,
        elapsed_ms:1000, remaining_ms:59000, exhausted_reason:null, ended_reason:null}}};
  const env = setup({view:"local-models", renderOnReload:true, handler:(url, options) =>
    url === "/api/local-models/status" ? status : modelHandler(url, options)});
  await env.render();
  status = {...status, runtime:{...status.runtime,
    autonomy_budget:{...status.runtime.autonomy_budget, submissions_used:2,
      elapsed_ms:3000, remaining_ms:57000}}};
  await env.advanceTimer();
  assert.equal(env.reloads, 0);
  assert.match(text(env.livePage), /2 \/ 16/);
  assert.equal(env.timerCount, 1);
  assert.equal(post(env.calls).length, 0);
});

test("late model status after leaving page cannot redraw or restart a command", async () => {
  let resolveStatus;
  let reads = 0;
  const status = {status:"loading", loaded:false,
    operation:{id:"prepare-1", action:"prepare-and-load", status:"pending"}};
  const env = setup({view:"local-models", renderOnReload:true, handler:(url, options) => {
    if (url === "/api/local-models/status") {
      reads++;
      return reads === 1 ? status : new Promise((resolve) => { resolveStatus = resolve; });
    }
    return modelHandler(url, options);
  }});
  await env.render();
  const lateRead = env.advanceTimer();
  assert.equal(typeof resolveStatus, "function");
  env.navigate("statistics");
  await env.render();
  resolveStatus({...status, status:"loaded", loaded:true,
    operation:{...status.operation, status:"completed"}});
  await lateRead;
  assert.equal(env.reloads, 0);
  assert.equal(env.timerCount, 0);
  assert.equal(post(env.calls).length, 0);
});

test("old instance status cannot replace a newly selected instance", async () => {
  let resolveStatus;
  let reads = 0;
  const status = {status:"loading", loaded:false,
    operation:{id:"prepare-1", action:"prepare-and-load", status:"pending"}};
  const env = setup({view:"local-models", renderOnReload:true, handler:(url, options) => {
    if (url === "/api/local-models/status") {
      reads++;
      return reads === 2 ? new Promise((resolve) => { resolveStatus = resolve; }) : status;
    }
    return modelHandler(url, options);
  }});
  await env.render();
  const lateRead = env.advanceTimer();
  env.scope("another-device");
  await env.render();
  resolveStatus({...status, status:"loaded", loaded:true,
    operation:{...status.operation, status:"completed"}});
  await lateRead;
  assert.equal(env.reloads, 0);
  assert.match(text(env.livePage), /服务处理中/);
  assert.equal(env.timerCount, 1);
  assert.equal(post(env.calls).length, 0);
});

test("unchanged pending status stops automatic checks at the finite limit", async () => {
  const status = {status:"loading", loaded:false,
    operation:{id:"prepare-1", action:"prepare-and-load", status:"pending"}};
  const env = setup({view:"local-models", handler:(url, options) =>
    url === "/api/local-models/status" ? status : modelHandler(url, options)});
  await env.render();
  for (let index = 0; index < 100; index++) await env.advanceTimer();
  assert.equal(env.timerCount, 0);
  assert.match(text(env.notice), /自动状态检查已暂停/);
  assert.equal(post(env.calls).length, 0);
});

test("a status change on the final allowed read still reports observation paused", async () => {
  let status = {status:"loading", loaded:false,
    operation:{id:"prepare-1", action:"prepare-and-load", status:"pending"}};
  const env = setup({view:"local-models", renderOnReload:true, handler:(url, options) =>
    url === "/api/local-models/status" ? status : modelHandler(url, options)});
  await env.render();
  for (let index = 0; index < 99; index++) await env.advanceTimer();
  status = {...status, error_code:"temporary_diagnostic"};
  await env.advanceTimer();
  assert.equal(env.reloads, 1);
  assert.equal(env.timerCount, 0);
  assert.match(text(env.notice), /自动状态检查已暂停/);
  assert.equal(post(env.calls).length, 0);
});

test("model page owns automatic refresh even after its bounded watcher pauses", async () => {
  const status = {status:"loaded", loaded:true,
    runtime:{run_id:"run-1", lifecycle:"running", mode:"human", controller:"released",
      tainted:false, errors:[]}};
  const env = setup({view:"local-models", query:"&id=audited-cpu", handler:(url, options) =>
    url === "/api/local-models/status" ? status : modelHandler(url, options)});
  await env.render();
  assert.equal(await env.ui.refresh("local-models"), true);
  assert.equal(env.timerCount, 1);
  for (let index = 0; index < 100; index++) await env.advanceTimer();
  assert.equal(env.timerCount, 0);
  assert.equal(await env.ui.refresh("local-models"), true);
  assert.equal(env.timerCount, 0);
  env.navigate("statistics");
  assert.equal(await env.ui.refresh("local-models"), false);
  assert.equal(post(env.calls).length, 0);
});

test("a late timer after the model observation deadline performs no status GET", async () => {
  const status = {status:"loading", loaded:false,
    operation:{id:"prepare-1", action:"prepare-and-load", status:"pending"}};
  const env = setup({view:"local-models", handler:(url, options) =>
    url === "/api/local-models/status" ? status : modelHandler(url, options)});
  await env.render();
  const reads = env.calls.filter(call => call.url === "/api/local-models/status").length;
  env.context.Date = {now:() => Date.now() + 6 * 60 * 1000};
  await env.advanceTimer();
  assert.equal(env.calls.filter(call => call.url === "/api/local-models/status").length, reads);
  assert.equal(env.timerCount, 0);
  assert.match(text(env.notice), /自动状态检查已暂停/);
});

test("failed status reads stop after a bound and manual refresh stays read-only", async () => {
  let fail = false;
  const status = {status:"loading", loaded:false,
    operation:{id:"prepare-1", action:"prepare-and-load", status:"pending"}};
  const env = setup({view:"local-models", renderOnReload:true, handler:(url, options) =>
    url === "/api/local-models/status" ? (fail ? {httpStatus:503} : status)
      : modelHandler(url, options)});
  await env.render();
  fail = true;
  await env.advanceTimer();
  await env.advanceTimer();
  await env.advanceTimer();
  assert.equal(env.timerCount, 0);
  assert.match(text(env.notice), /自动状态检查暂不可用/);
  fail = false;
  await action(env.livePage, "model-refresh-status").onclick();
  assert.equal(env.reloads, 1);
  assert.equal(env.timerCount, 1);
  assert.equal(post(env.calls).length, 0);
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
            runtime: { mode: "human", lifecycle: "running", controller: "released" },
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

test("collection page claims automatic delivery only when its local owner reports it running", async () => {
  for (const [upload, expected, forbidden] of [
    [{ enabled: false, process: "not_configured" }, "本机后台上传未启用", "本机后台投递已启用"],
    [{ enabled: true, process: "not_configured" }, "本机投递服务未配置运行", "本机后台投递已启用"],
    [{ enabled: true, process: "stopped" }, "本机投递服务当前未运行", "本机后台投递已启用"],
    [{ enabled: true, process: "running" }, "本机后台投递已启用且当前运行", "本机后台上传未启用"],
  ]) {
    const env = setup({ view: "campaigns", handler: (url) => url.endsWith("/collection-flow") ? {
      schema: "stpd/local-collection-flow-v1", device_id: "this-pc", enrollment: null,
      default: null, consent_required: false, stage: "unavailable", next_action: "reconnect", upload,
    } : emptyList() });
    const page = await env.render();
    assert.match(text(page), new RegExp(expected));
    assert.doesNotMatch(text(page), new RegExp(forbidden));
    assert.equal(post(env.calls).length, 0);
  }
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


test("runtime environment rejection remains historical until an explicit fresh start", async () => {
  const env = setup({ view: "local-models", handler: (url, options) =>
    url === "/api/local-models/status" ? {
      status: "loaded", loaded: true,
      runtime: { lifecycle: "running", mode: "human", controller: "released",
        errors: ["environment_modset_fingerprint_drift"], last_receipt: null }
    } : modelHandler(url, options) });
  let page = await env.render();
  assert.match(text(page), /游戏环境与模型绑定不一致/);
  assert.match(text(page), /历史诊断/);
  assert.match(text(page), /尚无游戏动作送达记录/);
  assert.equal(action(page, "model-command-auto").disabled, false);
  assert.equal(action(page, "model-command-stop").disabled, false);
  assert.equal(post(env.calls).length, 0);
  page = await env.render();
  assert.equal(post(env.calls).length, 0);
  await action(page, "model-command-auto").onclick();
  assert.equal(post(env.calls).length, 1);
  assert.deepEqual(body(post(env.calls)[0]), { action: "auto" });
});

test("local Runtime autonomy budget is read-only, bounded and separate from game completion", async () => {
  const base = {
    status: "loaded", loaded: true, error_code: null,
    runtime: {
      lifecycle: "running", mode: "human", controller: "released",
      tainted: false, errors: [], last_receipt: null,
    },
  };
  const values = [
    [{ state: "exhausted", max_submissions: 16, submissions_used: 16,
      max_policy_calls: 32, policy_calls_used: 20, deadline_ms: 60000,
      elapsed_ms: 45000, remaining_ms: 15000, exhausted_reason: "submission_attempt_limit", ended_reason: null },
    /自主提交次数已到限额/],
    [{ state: "exhausted", max_submissions: 16, submissions_used: 4,
      max_policy_calls: 32, policy_calls_used: 32, deadline_ms: 60000,
      elapsed_ms: 25000, remaining_ms: 35000, exhausted_reason: "policy_call_limit", ended_reason: null },
    /模型评分次数已到限额/],
    [{ state: "exhausted", max_submissions: 16, submissions_used: 2,
      max_policy_calls: 32, policy_calls_used: 7, deadline_ms: 60000,
      elapsed_ms: 60000, remaining_ms: 0, exhausted_reason: "deadline", ended_reason: null },
    /自主运行时间已到限额/],
    [{ state: "inactive", max_submissions: 16, submissions_used: 2,
      max_policy_calls: 32, policy_calls_used: 4, deadline_ms: 60000,
      elapsed_ms: 8000, remaining_ms: 52000, exhausted_reason: null, ended_reason: "human_recovery" },
    /因人工接管结束/],
    [{ state: "inactive", max_submissions: 16, submissions_used: 2,
      max_policy_calls: 32, policy_calls_used: 4, deadline_ms: 60000,
      elapsed_ms: 8000, remaining_ms: 52000, exhausted_reason: null, ended_reason: "mode_changed" },
    /因运行模式变更结束/],
    [{ state: "inactive", max_submissions: 16, submissions_used: 2,
      max_policy_calls: 32, policy_calls_used: 4, deadline_ms: 60000,
      elapsed_ms: 8000, remaining_ms: 52000, exhausted_reason: null, ended_reason: "stopped" },
    /随 Runtime 停止结束/],
  ];
  for (const [autonomy_budget, expected] of values) {
    const state = structuredClone(base);
    state.runtime.autonomy_budget = autonomy_budget;
    const env = setup({view: "local-models", handler: url =>
      url === "/api/local-models/status" ? state : modelHandler(url)});
    let page = await env.render();
    assert.match(text(page), expected);
    assert.match(text(page), /不代表这一局已经结束或结果已确认/);
    assert.match(text(page), /提交/);
    assert.match(text(page), /模型评分/);
    assert.equal(action(page, "model-command-auto").disabled, false);
    page = await env.render();
    assert.match(text(page), expected);
    assert.equal(post(env.calls).length, 0, "GET and redraw do not send commands");
    await action(page, "model-command-auto").onclick();
    assert.equal(post(env.calls).length, 1, "existing command still needs its explicit click");
    assert.deepEqual(body(post(env.calls)[0]), {action: "auto"});
  }
});

test("active local Runtime budget requires a running autonomous mode without changing controls", async () => {
  const activeBudget = {state: "active", max_submissions: 16, submissions_used: 3,
    max_policy_calls: 32, policy_calls_used: 5, deadline_ms: 60000,
    elapsed_ms: 12000, remaining_ms: 48000, exhausted_reason: null, ended_reason: null};
  for (const [mode, controller] of [["auto", "held"], ["shadow", "released"], ["one_step", "released"]]) {
    const state = {
      status: "loaded", loaded: true,
      runtime: {lifecycle: "running", mode, controller, tainted: false,
        errors: [], last_receipt: null, autonomy_budget: activeBudget},
    };
    const env = setup({view: "local-models", handler: url =>
      url === "/api/local-models/status" ? state : modelHandler(url)});
    const page = await env.render();
    assert.match(text(page), /正在使用/);
    assert.match(text(page), /3 \/ 16/);
    assert.match(text(page), /5 \/ 32/);
    assert.match(text(page), /12 秒 \/ 60 秒（剩余 48 秒）/);
    assert.equal(action(page, "model-command-auto").disabled, true, mode);
    assert.equal(action(page, "model-command-human").disabled, false, mode);
    assert.equal(action(page, "model-command-stop").disabled, false, mode);
    assert.equal(post(env.calls).length, 0, mode);
  }
});

test("active budget with Human mode or stopped lifecycle is unknown without affecting command guards", async () => {
  const activeBudget = {state: "active", max_submissions: 16, submissions_used: 3,
    max_policy_calls: 32, policy_calls_used: 5, deadline_ms: 60000,
    elapsed_ms: 12000, remaining_ms: 48000, exhausted_reason: null, ended_reason: null};
  const cases = [
    {lifecycle: "running", mode: "human", controller: "released", tainted: false},
    {lifecycle: "stopped", mode: "auto", controller: "released", tainted: false},
  ];
  for (const runtime of cases) {
    const render = async (budget) => {
      const state = {status: "loaded", loaded: true,
        runtime: {...runtime, errors: [], last_receipt: null, ...(budget ? {autonomy_budget: budget} : {})}};
      const env = setup({view: "local-models", handler: url =>
        url === "/api/local-models/status" ? state : modelHandler(url)});
      return {page: await env.render(), env};
    };
    const baseline = await render(null);
    const {page, env} = await render(activeBudget);
    assert.match(text(page), /预算状态未提供或格式无法识别/);
    assert.doesNotMatch(text(page), /正在使用|3 \/ 16|5 \/ 32/);
    for (const actionName of ["auto", "human", "stop"])
      assert.equal(action(page, `model-command-${actionName}`).disabled,
        action(baseline.page, `model-command-${actionName}`).disabled, actionName);
    assert.equal(post(env.calls).length, 0);
  }
});

test("old or unrecognized Runtime budget data is not replaced with a default or echoed", async () => {
  const budgetText = page => text(find(page, node => node.tagName === "SECTION" &&
    node.children[0]?.textContent === "本次自主操作预算"));
  const state = {
    status: "loaded", loaded: true,
    runtime: {lifecycle: "running", mode: "human", controller: "released",
      tainted: false, errors: [], last_receipt: null},
  };
  const env = setup({view: "local-models", handler: url =>
    url === "/api/local-models/status" ? state : modelHandler(url)});
  let page = await env.render();
  assert.match(budgetText(page), /此 Runtime 未提供预算信息/);
  assert.doesNotMatch(budgetText(page), /16|32|60 秒/);

  state.runtime.autonomy_budget = {state: "private-path-leak", submissions_used: 999};
  page = await env.render();
  assert.match(text(page), /预算状态未提供或格式无法识别/);
  assert.doesNotMatch(text(page), /private-path-leak|999/);
  assert.equal(action(page, "model-command-auto").disabled, false);
  assert.equal(post(env.calls).length, 0);

  state.runtime.mode = "human";
  state.runtime.autonomy_budget = {state: "inactive", max_submissions: 16, submissions_used: 3,
    max_policy_calls: 32, policy_calls_used: 5, deadline_ms: 60000,
    elapsed_ms: 12000, remaining_ms: 48000, exhausted_reason: "deadline", ended_reason: "human_recovery"};
  page = await env.render();
  assert.match(text(page), /预算状态未提供或格式无法识别/);
  assert.doesNotMatch(text(page), /自主运行时间已到限额|因人工接管结束/);
  assert.equal(post(env.calls).length, 0);
});

test("retained local precondition diagnostics do not block an explicit fresh start", async () => {
  for (const error_code of [
    "connector_identity_unavailable",
    "runtime_recovery_epoch_mismatch",
  ]) {
    const env = setup({ view: "local-models", handler: (url, options) =>
      url === "/api/local-models/status" ? {
        status: "loaded", loaded: true, error_code,
        runtime: {
          lifecycle: "running", mode: "human", controller: "released",
          tainted: false, errors: [error_code], last_receipt: null,
        },
      } : modelHandler(url, options) });
    const page = await env.render();
    assert.equal(action(page, "model-command-auto").disabled, false, error_code);
    assert.equal(post(env.calls).length, 0);
    await action(page, "model-command-auto").onclick();
    assert.equal(post(env.calls).length, 1, error_code);
    assert.deepEqual(body(post(env.calls)[0]), { action: "auto" });
  }
});

test("each explicit decision mode sends exactly one request and redraw never retries", async () => {
  for (const action_name of ["auto", "one_step", "shadow"]) {
    const env = setup({ view: "local-models", handler: (url, options) =>
      url === "/api/local-models/status" ? {
        status: "loaded", loaded: true, error_code: "runtime_recovery_epoch_mismatch",
        runtime: {
          lifecycle: "running", mode: "human", controller: "released",
          tainted: false, errors: ["runtime_recovery_epoch_mismatch"], last_receipt: null,
        },
      } : modelHandler(url, options) });
    let page = await env.render();
    assert.equal(action(page, `model-command-${action_name}`).disabled, false);
    page = await env.render();
    assert.equal(post(env.calls).length, 0);
    await action(page, `model-command-${action_name}`).onclick();
    assert.equal(post(env.calls).length, 1, action_name);
    assert.deepEqual(body(post(env.calls)[0]), { action: action_name });
  }
});

test("current owner blockers disable decisions one at a time while legal recovery remains available", async () => {
  const runtime = (overrides = {}) => ({
    lifecycle: "running", mode: "human", controller: "released",
    tainted: false, errors: ["historical_runtime_failure"], last_receipt: null,
    ...overrides,
  });
  const cases = [
    {
      name: "command_unknown",
      state: { status: "command_unknown", loaded: true, runtime: runtime() },
      recoverable: true,
    },
    {
      name: "recovery_required",
      state: {
        status: "recovery_required", loaded: false,
        previous_session: { run_id: "previous" }, runtime: runtime(),
      },
      recoverable: true,
    },
    {
      name: "observation_error",
      state: {
        status: "loaded", loaded: true,
        observation_error: "runtime_status_unavailable_or_identity_drift",
        runtime: runtime(),
      },
      recoverable: true,
    },
    {
      name: "taint",
      state: { status: "loaded", loaded: true, runtime: runtime({ tainted: true }) },
      recoverable: true,
    },
    {
      name: "pending",
      state: {
        status: "loading", loaded: false,
        operation: { status: "pending", action: "prepare-and-load" }, runtime: runtime(),
      },
      recoverable: true,
    },
    {
      name: "stopped",
      state: {
        status: "stopped", loaded: false,
        runtime: runtime({ lifecycle: "stopped" }),
      },
      recoverable: false,
    },
    {
      name: "non_human",
      state: {
        status: "loaded", loaded: true,
        runtime: runtime({ mode: "shadow", controller: "held" }),
      },
      recoverable: true,
    },
    {
      name: "controller_held",
      state: {
        status: "loaded", loaded: true,
        runtime: runtime({ controller: "held" }),
      },
      recoverable: true,
    },
  ];
  for (const { name, state, recoverable } of cases) {
    const env = setup({
      view: "local-models",
      handler: (url, options) =>
        url === "/api/local-models/status" ? state : modelHandler(url, options),
    });
    const page = await env.render();
    for (const action_name of ["auto", "one_step", "shadow"])
      assert.equal(action(page, `model-command-${action_name}`).disabled, true, name);
    assert.equal(post(env.calls).length, 0, name);
    for (const action_name of ["human", "stop"])
      assert.equal(
        action(page, `model-command-${action_name}`).disabled,
        !recoverable,
        name,
      );
  }
});

test("each owner blocker is independently effective against a complete healthy fixture", async () => {
  const healthy = () => ({
    status: "loaded",
    loaded: true,
    error_code: null,
    observation_error: null,
    operation: { id: "previous-command", action: "auto", status: "completed" },
    runtime: {
      lifecycle: "running",
      mode: "human",
      controller: "released",
      tainted: false,
      taint_reason: null,
      errors: [],
      invalidations: [],
      last_receipt: null,
    },
  });
  const normal = setup({
    view: "local-models",
    handler: (url, options) =>
      url === "/api/local-models/status" ? healthy() : modelHandler(url, options),
  });
  const normalPage = await normal.render();
  for (const action_name of ["auto", "one_step", "shadow"])
    assert.equal(action(normalPage, `model-command-${action_name}`).disabled, false);
  assert.equal(post(normal.calls).length, 0);

  const cases = [
    {
      name: "pending operation",
      change: (state) => {
        state.operation = { id: "current-command", action: "auto", status: "pending" };
      },
    },
    {
      name: "shadow mode with released controller",
      change: (state) => {
        state.runtime.mode = "shadow";
      },
    },
    {
      name: "stopped Runtime while service remains loaded",
      change: (state) => {
        state.runtime.lifecycle = "stopped";
      },
    },
    {
      name: "recovery required while service and Runtime remain observed",
      change: (state) => {
        state.status = "recovery_required";
      },
    },
  ];
  for (const { name, change } of cases) {
    const state = healthy();
    change(state);
    const env = setup({
      view: "local-models",
      handler: (url, options) =>
        url === "/api/local-models/status" ? state : modelHandler(url, options),
    });
    const page = await env.render();
    for (const action_name of ["auto", "one_step", "shadow"]) {
      const button = action(page, `model-command-${action_name}`);
      assert.equal(button.disabled, true, name);
      await button.onclick();
    }
    assert.equal(post(env.calls).length, 0, name);

    for (const recovery_action of ["human", "stop"]) {
      const recoveryEnv = setup({
        view: "local-models",
        handler: (url, options) =>
          url === "/api/local-models/status" ? state : modelHandler(url, options),
      });
      const recoveryPage = await recoveryEnv.render();
      const button = action(recoveryPage, `model-command-${recovery_action}`);
      assert.equal(button.disabled, false, `${name}: ${recovery_action}`);
      await button.onclick();
      assert.equal(post(recoveryEnv.calls).length, 1, `${name}: ${recovery_action}`);
      assert.deepEqual(
        body(post(recoveryEnv.calls)[0]),
        { action: recovery_action },
        `${name}: ${recovery_action}`,
      );
    }
  }
});

test("retained runtime diagnostics are history after explicit recovery", async () => {
  const env = setup({ view: "local-models", handler: (url, options) =>
    url === "/api/local-models/status" ? {
      status: "loaded", loaded: true, error_code: null,
      runtime: {
        lifecycle: "running", mode: "human", controller: "released",
        errors: ["temporary_runtime_failure"],
        last_snapshot_id: "snapshot-recovered",
        last_receipt: { delivery: "delivered" },
      }
    } : modelHandler(url, options) });
  let page = await env.render();
  assert.match(text(page), /历史记录/);
  assert.match(text(page), /temporary_runtime_failure/);
  assert.equal(action(page, "model-command-auto").disabled, false);
  assert.equal(action(page, "model-command-human").disabled, false);
  assert.equal(action(page, "model-command-stop").disabled, false);
  page = await env.render();
  assert.equal(post(env.calls).length, 0);
  await action(page, "model-command-human").onclick();
  assert.deepEqual(body(post(env.calls)[0]), { action: "human" });
});

test("a Runtime already in Auto remains protected from duplicate start despite old errors", async () => {
  const env = setup({ view: "local-models", handler: (url, options) =>
    url === "/api/local-models/status" ? {
      status: "loaded", loaded: true, error_code: null,
      runtime: {
        lifecycle: "running", mode: "auto", controller: "held",
        errors: ["environment_modset_fingerprint_drift"],
        last_snapshot_id: "snapshot-current",
        last_receipt: null,
      }
    } : modelHandler(url, options) });
  const page = await env.render();
  assert.equal(action(page, "model-command-auto").disabled, true);
  assert.equal(action(page, "model-command-human").disabled, false);
  assert.equal(action(page, "model-command-stop").disabled, false);
  assert.equal(post(env.calls).length, 0);
});

test("taint remains a current execution blocker while history does not trigger commands on redraw", async () => {
  const env = setup({ view: "local-models", handler: (url, options) =>
    url === "/api/local-models/status" ? {
      status: "loaded", loaded: true, error_code: null,
      runtime: {
        lifecycle: "running", mode: "human", controller: "released",
        tainted: true, taint_reason: "receipt_correlation_failed",
        errors: ["temporary_runtime_failure"], last_receipt: null,
      }
    } : modelHandler(url, options) });
  await env.render();
  await env.render();
  assert.equal(action(await env.render(), "model-command-auto").disabled, true);
  assert.equal(post(env.calls).length, 0);
});

test("another recording's unresolved publication blocks a new dataset check", async () => {
  for (const recoverable of [true, false]) {
    const artifact = id("a"), previous = id("b");
    const env = localDatasetEnv({artifact, datasetStatus: {
      schema: "stpd/local-dataset-operation-v1", availability: "ready", paired_training: [],
      operation: {status: "failed", artifact_id: previous, purpose: "training",
        paired_training: null, preview_id: "d".repeat(32), can_publish: false,
        recovery_available: recoverable,
        error_code: recoverable ? "publish_failed" : "publication_recovery_required"},
      csrf_token: "dataset-csrf",
    }});
    const page = await env.render();
    const check = action(page, "check-local-dataset");
    assert.equal(check.disabled, true);
    assert.match(text(page), /另一份录制的创建结果尚未确认/);
    assert.equal(walk(page).some(element => element.tag === "a"
      && element.href?.includes(previous)), true);
    assert.equal(walk(page).some(element => element.dataset?.action === "recover-local-dataset-publication"), false);
    await check.onclick();
    await action(page, "refresh-local-dataset-status").onclick();
    assert.equal(post(env.calls).length, 0);
  }
});
