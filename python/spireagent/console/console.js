"use strict";

// Presentation only. All statuses and counts originate in the owning APIs.
const config = document.body.dataset;
const localShell = config.mode === "local";
let local = localShell;
const views = {
  "local-home": ["本机工作台", "本机模型、本机资料与游戏内录制入口。云端登录可选。"],
  devices: ["账号与电脑", "一个项目账号，管理获授权的电脑。"],
  connect: ["确认电脑接入", "核对名称与配对码，批准刚刚发起的请求。"],
  overview: ["概览", "采集、上传与研究进展，一处查看。"],
  collections: ["采集记录", "每份录制的质量、投递和研究使用，分别查看。"],
  games: ["对局与片段", "按原生边界整理；完整性、胜负与记录质量分别统计。"],
  datasets: ["数据集", "固定来源与版本；新上传的数据不会自动改变已有训练集。"],
  jobs: ["作业", "查看真实执行状态与产物。此页面不会启动计算。"],
  models: ["模型目录", "沿着数据与作业来源查看产物；下载不等于加载或运行。"],
  system: ["系统", "查看连接、版本与运维证据，区分已确认事实和未观测状态。"],
  members: ["成员管理", "管理员管理成员；所有成员共享项目数据与研究能力。"],
  statistics: ["数据统计", "总量、类别和摘要覆盖，按明确的数据范围统计。"],
  downloads: ["数据下载", "固定下载清单和校验值；封存与未获共享授权的数据保持受限。"],
  research: ["训练与模型", "查看真实训练产物、benchmark 和数据分析，区分尚未执行的计划。"],
  "local-models": ["模型实战", "检查兼容性、加载模型，再明确开始真实游戏评估。"],
  "local-workspace": ["本机资料", "直接浏览已登记的本机研究资料与来源。无需项目账号或云端连接。"],
  campaigns: ["真人采集", "在游戏内由你控制录制；本机后台投递状态按实际配置显示。"],
  evaluations: ["评估结果", "模型游戏实战与离线评价分别展示；片段和未知结果不会计作胜局。"],
};
const labels = {
  verified: ["云端已验收", "good"],
  auth_blocked: ["上传授权需恢复", "wait"],
  pending: ["等待投递", "wait"],
  awaiting_upload: ["等待上传", "wait"],
  verification_pending: ["等待云端校验", "wait"],
  quarantined: ["云端已隔离", "bad"],
  transfer_failed: ["传输失败 · 需处理", "bad"],
  incident: ["本地异常 · 需处理", "bad"],
  unavailable: ["暂不可用", "wait"],
  not_configured: ["尚未配置", "neutral"],
  not_assessed: ["尚未研究检查", "neutral"],
  not_authorized: ["当前权限不可查看", "neutral"],
  not_indexed: ["摘要尚未建立", "neutral"],
  admitted: ["已准入", "good"],
  rejected: ["未准入", "wait"],
  queued: ["排队中", "wait"],
  running: ["运行中", "good"],
  completed: ["已完成", "good"],
  complete: ["已完成", "good"],
  cancelled: ["已取消", "neutral"],
  failed: ["执行失败", "bad"],
  unknown: ["状态未知 · 待核对", "wait"],
  submission_unknown: ["提交结果未知 · 待核对", "wait"],
  uncertain: ["执行状态未知", "wait"],
  cancel_requested: ["已请求取消 · 等待确认", "wait"],
  paused: ["调度已暂停", "neutral"],
  available: ["可用", "good"],
  stale: ["最近已知状态", "wait"],
};
Object.assign(labels, {
  packing: ["封装阶段 · 最近观测", "wait"],
  locally_verified: ["本机核验完成", "wait"],
  retry_wait: ["等待再次处理", "wait"],
  dataset_references_present: ["已有数据集引用", "good"],
});
const events = {
  upload_created: "远端上传记录已创建",
  upload_receipt: "接收端已写入收据",
  verification_retry: "等待下一次云端校验",
  upload_retry: "运维已授权重试",
  upload_verification_requested: "云端校验已请求",
  verification_requested: "云端校验已请求",
  closed: "Recorder 已封口",
  observed: "最近状态观测",
};
const $ = (id) => document.getElementById(id);
const node = (tag, text, cls) => {
  const element = document.createElement(tag);
  if (text !== undefined && text !== null) element.textContent = String(text);
  if (cls) element.className = cls;
  return element;
};
const number = (value) =>
  Number.isFinite(value) ? value.toLocaleString("zh-CN") : "—";
const short = (value) => (typeof value === "string" ? value.slice(0, 12) : "—");
const date = (value) => {
  if (!value) return "未观测";
  const parsed = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isFinite(parsed.getTime())
    ? parsed.toLocaleString("zh-CN", { hour12: false })
    : "未观测";
};
const bytes = (value) =>
  Number.isFinite(value)
    ? value >= 1e6
      ? `${(value / 1e6).toFixed(2)} MB`
      : `${(value / 1e3).toFixed(1)} KB`
    : "未观测";
const badge = (value) => {
  const [text, tone] = labels[value] || [value || "未观测", "neutral"];
  return node("span", text, `badge ${tone}`);
};
const button = (text, fn, cls = "secondary") => {
  const element = node("button", text, `button ${cls}`);
  element.type = "button";
  element.addEventListener("click", fn);
  return element;
};
const link = (text, url, external = false) => {
  const element = node("a", text, "button secondary small");
  element.href = url;
  if (external) {
    element.target = "_blank";
    element.rel = "noreferrer";
  }
  return element;
};
function panel(title, subtitle) {
  const section = node("section", null, "panel");
  const header = node("div", null, "panel-header");
  const heading = node("div");
  heading.append(node("h2", title));
  if (subtitle) heading.append(node("p", subtitle));
  header.append(heading);
  section.append(header);
  return section;
}
function facts(entries) {
  const list = node("dl", null, "fact-list");
  for (const [label, value] of entries) {
    const row = node("div", null, "fact-row");
    const detail = node("dd");
    detail.append(
      value instanceof Node ? value : node("span", value ?? "未观测"),
    );
    row.append(node("dt", label), detail);
    list.append(row);
  }
  return list;
}
function empty(title, description) {
  const element = node("div", null, "empty-state");
  element.append(
    node("div", "◇", "empty-icon"),
    node("strong", title),
    node("p", description),
  );
  return element;
}
function localHome() {
  const page = node("div", null, "project-page");
  const intro = panel(
    "先从本机开始",
    "本机模型与资料查看不需要云端登录。游戏内操作由你在 Mod 中明确开始；采集授权和本机准备按现有流程完成。",
  );
  intro.append(node("p", config.cloudUrl
    ? "云端共享和账号功能可选；需要时可从右上角打开云端。"
    : "此工作台尚未配置云端入口；本机功能仍可使用。"));
  page.append(intro);

  const grid = node("div", null, "grid-two");
  const models = panel(
    "本机模型",
    "查看本机审核过的模型选择与 Runtime 状态。准备并加载、开始测试都需要你分别明确操作。",
  );
  models.append(link("打开模型实战 →", "?view=local-models"));
  const workspace = panel(
    "本机资料",
    "浏览本机研究资料。没有已配置资料库时，可在资料页明确新建空工作空间；打开页面只读取状态，不会自动创建或扫描资料库。",
  );
  workspace.append(link("打开本机资料 →", "?view=local-workspace"));
  grid.append(models, workspace);
  page.append(grid);

  const recording = panel(
    "游戏内真人录制",
    "录制由你在 STS2 游戏 Mod 中开始和结束；打开工作台不会启动游戏或触发录制。",
  );
  recording.append(
    node("p", "录制保存在本机。上传另需设备授权、开启上传设置且本机投递服务实际运行；没有投递配置时，新录制不会自动上传。查看下方真人采集状态可核对。"),
    link("查看真人采集配置与状态 →", "?view=campaigns"),
  );
  page.append(recording);
  return page;
}
function technical(value) {
  const element = node("details", null, "technical");
  element.dataset.preserve = `technical:${value.id || value.artifact_id || value.source_revision || "system"}`;
  element.append(
    node("summary", "技术证据 · 精确身份与原始状态"),
    node("pre", JSON.stringify(value, null, 2)),
    button("下载诊断摘要", () => {
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(value, null, 2)], {
          type: "application/json",
        }),
      );
      const download = document.createElement("a");
      download.href = url;
      download.download = "spireagent-console-summary.json";
      download.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }),
    node(
      "p",
      "包含记录身份与状态；仅用于私下排查，公开前请审阅。",
      "small muted",
    ),
  );
  return element;
}
function metric(title, value, note, tone = "") {
  const element = node("div", null, `metric ${tone}`);
  element.append(
    node("div", title, "metric-label"),
    node("div", number(value), "metric-value"),
    node("div", note, "metric-note"),
  );
  return element;
}
function collectionId(row) {
  return row.id || row.upload_id;
}
function collectionContext(row) {
  const context = row.collection_context;
  if (context?.kind === "unlinked") return "未关联专题活动";
  if (["default", "activity"].includes(context?.kind) && typeof context.name === "string" && context.name)
    return `${context.kind === "default" ? "日常录制" : "专题活动"} · ${context.name}`;
  return row.local_delivery ? "本机记录 · 云端归属见详情" : "录制用途尚未关联";
}
function summary(row) {
  return row.summary || {};
}
function counts(row) {
  return summary(row).counts || {};
}
function completeness(row) {
  const runs = summary(row).runs;
  if (!Array.isArray(runs)) return "局边界未观测";
  const assigned = runs.filter((run) => run.assigned === true);
  if (!assigned.length) return "局边界未观测";
  const complete = assigned.filter(
    (run) => run.start_observed === true && run.terminal_observed === true,
  ).length;
  return `${complete} / ${assigned.length} 局原生边界齐全`;
}
function collectionTable(rows) {
  const wrap = node("div", null, "table-wrap");
  const table = node("table");
  const head = node("thead"),
    tr = node("tr");
  for (const title of [
    "采集记录",
    "完整性",
    "已录入",
    "真实失败",
    "投递",
    "研究检查",
  ])
    tr.append(node("th", title));
  head.append(tr);
  table.append(head);
  const body = node("tbody");
  for (const row of rows) {
    const record = summary(row),
      tally = counts(row),
      line = node("tr");
    const first = node("td");
    const action = button(
      date(record.created_at || row.created_at),
      () => navigate("collections", collectionId(row)),
      "link row-title",
    );
    first.append(
      action,
      node("span", collectionContext(row), "subtext"),
      node(
        "span",
        `${row.device_id || record.worker_id || row.worker_id || "本机"} · ${short(record.session_id || row.session_id || collectionId(row))}`,
        "subtext",
      ),
    );
    line.append(
      first,
      node("td", completeness(row)),
      node("td", number(tally.canonical)),
    );
    const failure = node("td");
    failure.append(
      node(
        "span",
        Number.isFinite(tally.real_failures)
          ? number(tally.real_failures)
          : "未核验",
        `badge ${tally.real_failures > 0 ? "bad" : tally.real_failures === 0 ? "good" : "neutral"}`,
      ),
    );
    const delivery = node("td"),
      research = node("td");
    delivery.append(
      badge(row.status === "pending" ? row.stage || row.status : row.status),
    );
    research.append(badge(row.research?.status || "not_assessed"));
    line.append(failure, delivery, research);
    body.append(line);
  }
  table.append(body);
  wrap.append(table);
  return wrap;
}
function overview(data) {
  if (["unavailable", "not_configured"].includes(data.status))
    return empty(
      "采集概览暂不可用",
      "尚未取得本机状态，不能判断记录数量。请查看系统页。",
    );
  const fragment = document.createDocumentFragment();
  const statuses = data.counts?.statuses;
  const tally = statuses
    ? {
        verified: statuses.verified ?? 0,
        pending:
          (statuses.awaiting_upload ?? 0) +
          (statuses.verification_pending ?? 0),
      }
    : data.counts || {};
  const quality = data.quality || data.counts?.quality || {};
  const metrics = node("div", null, "metrics");
  metrics.append(
    metric(
      local ? "本机待处理" : "等待接收 / 校验",
      Number.isFinite(tally.pending) ? tally.pending + (tally.auth_blocked || 0) : undefined,
      local ? "保持后台运行，可离线排队" : "仅统计有权限的远端记录",
    ),
    metric("云端已验收", tally.verified, "完整性与合同检查通过", "good"),
    metric("已录入决策", quality.canonical, "来自已核验采集摘要"),
    metric(
      "真实录制失败",
      quality.real_failures,
      "取消与诊断不计入",
      quality.real_failures > 0 ? "danger" : "good",
    ),
  );
  fragment.append(metrics);
  const attention =
    (data.counts?.auth_blocked || 0) +
    (data.counts?.incident || 0) +
    (data.counts?.quarantined || 0) +
    (statuses?.quarantined || 0) +
    (statuses?.transfer_failed || 0);
  if (attention > 0)
    fragment.append(
      node(
        "div",
        `${number(attention)} 份投递需要处理。传输异常与录制失败分别统计，请查看采集记录。`,
        "banner error",
      ),
    );
  if (
    quality.partial ||
    quality.summaries_missing > 0 ||
    (Number.isFinite(quality.real_failures_known) &&
      quality.real_failures_known < quality.summaries_available)
  )
    fragment.append(
      node(
        "div",
        "部分记录缺少可统计的可信摘要。上方只展示已知计数，不能据此认定全部记录零失败。",
        "banner",
      ),
    );
  const recent = panel("最近采集", "从一份记录，追溯它的云端去向");
  recent
    .querySelector(".panel-header")
    .append(button("查看全部 →", () => navigate("collections"), "link"));
  const rows = data.recent || data.latest || data.items || [];
  recent.append(
    rows.length
      ? collectionTable(rows.slice(0, 3))
      : empty(
          "还没有可展示的采集",
          local
            ? "确认后台投递运行，然后由真人完成录制并按 Recorder Close。"
            : "这里只展示云端已经收到且你有权查看的数据。",
        ),
  );
  fragment.append(recent);
  const grid = node("div", null, "grid-two");
  const flow = panel("采集到云端", "各阶段来自真实状态；不估算虚构进度");
  const flowBody = node("div", null, "panel-body");
  const steps = node("div", null, "steps");
  for (const text of [
    "Recorder Close",
    "本机封装",
    "上传 / 等待校验",
    "云端已验收",
  ])
    steps.append(node("span", text, "step"));
  flowBody.append(
    steps,
    node(
      "p",
      local
        ? "本机用已配置的设备身份连接云端，无需每局重新登录。关闭网页不会停止后台；关闭电脑后暂停处理。"
        : "云端不读取离线电脑的队列。最近收到数据的时间，不代表设备当前在线。",
      "muted small",
    ),
  );
  flow.append(flowBody);
  const next = panel("研究进展", "上传成功，与进入训练集是两件事");
  const nextBody = node("div", null, "panel-body");
  nextBody.append(
    node(
      "p",
      "研究检查绑定明确的输入集合与代码版本。新采集不会自动改变 Dataset，也不会自动启动训练。",
      "muted small",
    ),
    button("查看数据集 →", () => navigate("datasets"), "link"),
  );
  if (data.quality_coverage)
    nextBody.append(node("p", data.quality_coverage, "small muted"));
  next.append(nextBody);
  grid.append(flow, next);
  fragment.append(grid);
  return fragment;
}
function paginate(section, data) {
  const bar = node("div", null, "pagination");
  bar.append(
    node(
      "span",
      `共 ${number(data.total)} 条 · 第 ${Math.floor((data.offset || 0) / (data.limit || 25)) + 1} 页`,
    ),
  );
  const actions = node("div", null, "pagination-actions");
  const previous = button(
    "上一页",
    () => changePage(Math.max(0, state.offset - state.limit)),
    "secondary small",
  );
  previous.disabled = !state.offset;
  const next = button(
    "下一页",
    () => changePage(data.next_offset),
    "secondary small",
  );
  next.disabled = !Number.isInteger(data.next_offset);
  actions.append(previous, next);
  bar.append(actions);
  section.append(bar);
}
function collections(data) {
  if (["unavailable", "not_configured"].includes(data.status))
    return empty(
      "采集状态暂不可用",
      "查看系统页的配置和后台状态；无法读取不代表没有数据。",
    );
  const fragment = document.createDocumentFragment();
  const toolbar = node("div", null, "toolbar");
  toolbar.append(
    node(
      "span",
      local ? "本机采集 · 点击一条查看云端关联" : "授权设备的采集记录",
      "muted small",
    ),
  );
  const group = node("div", null, "toolbar-group");
  const search = node("input");
  search.type = "search";
  search.placeholder = "筛选本页：记录 / 电脑 / 活动名称";
  search.value = state.search;
  search.setAttribute("aria-label", "筛选当前页采集");
  search.addEventListener("input", () => {
    state.search = search.value;
    renderCollectionRows(data);
  });
  const size = node("select");
  size.setAttribute("aria-label", "每页条数");
  for (const value of [25, 50]) {
    const option = node("option", `${value} 条 / 页`);
    option.value = value;
    option.selected = state.limit === value;
    size.append(option);
  }
  size.addEventListener("change", () => {
    state.limit = Number(size.value);
    changePage(0);
  });
  group.append(search, size);
  toolbar.append(group);
  fragment.append(toolbar);
  const section = panel(
    "采集记录",
    "一份 Recorder session 可包含多局或部分局；局边界在详情中展开。",
  );
  const rows = node("div");
  rows.id = "collection-rows";
  section.append(rows);
  paginate(section, data);
  fragment.append(section);
  queueMicrotask(() => renderCollectionRows(data));
  return fragment;
}
function renderCollectionRows(data) {
  const target = $("collection-rows");
  if (!target) return;
  const query = state.search.toLowerCase();
  const rows = (data.items || []).filter(
    (row) =>
      !query ||
      [
        row.id,
        row.upload_id,
        row.content_id,
        row.device_id,
        row.worker_id,
        row.campaign_id,
        row.collection_context?.name,
        row.collection_context?.activity_id,
        row.session_id,
        summary(row).session_id,
        summary(row).campaign_id,
        summary(row).worker_id,
      ]
        .join(" ")
        .toLowerCase()
        .includes(query),
  );
  target.replaceChildren(
    rows.length
      ? collectionTable(rows)
      : empty(
          query ? "本页没有匹配的记录" : "还没有采集记录",
          query
            ? "筛选只作用于当前页。清空筛选或翻页查看。"
            : "完成一份录制并 Close 后，记录会进入对应投递流程。",
        ),
  );
}
function detail(data) {
  const row = data.item || data,
    record = summary(row),
    tally = counts(row);
  const fragment = document.createDocumentFragment(),
    heading = node("div", null, "detail-heading");
  heading.append(
    button("← 返回列表", () => navigate("collections"), "secondary small"),
    node("h2", date(record.created_at || row.created_at)),
    badge(row.status),
  );
  fragment.append(heading);
  if (row.summary_status && row.summary_status !== "available")
    fragment.append(
      node(
        "div",
        "采集摘要尚未核验或建立。投递收据独立保留，不将未知质量显示为零失败。",
        "banner",
      ),
    );
  const metrics = node("div", null, "metrics");
  metrics.append(
    metric("已录入决策", tally.canonical, "durable canonical", "good"),
    metric(
      "真实失败",
      tally.real_failures,
      "authoritative disposition",
      tally.real_failures > 0 ? "danger" : "good",
    ),
    metric("正常取消", tally.cancelled, "取消不制造成功 successor"),
    metric("诊断记录", tally.diagnostics, "不计入真实失败"),
  );
  fragment.append(metrics);
  const grid = node("div", null, "grid-two");
  const capture = panel(
    "本次采集",
    "展开后的局边界与决策统计来自 Platform 摘要",
  );
  const captureBody = node("div", null, "panel-body");
  captureBody.append(
    facts([
      ["采集 ID", record.session_id || row.session_id],
      ["设备", row.device_id || record.worker_id || row.worker_id],
      ["录制用途", collectionContext(row)],
      ["录制配置 ID", record.campaign_id || row.campaign_id],
      ["被游戏接收", number(tally.accepted)],
      [
        "子决策 / canonical",
        `${number(tally.accepted_children)} / ${number(tally.canonical_children)}`,
      ],
      ["未解决", number(tally.unresolved)],
      ["Recorder Close", date(record.closed_at)],
    ]),
  );
  capture.append(captureBody);
  grid.append(capture);
  const delivery = panel("投递与验收", "历史验收与当前网络连接分别显示");
  const deliveryBody = node("div", null, "panel-body");
  deliveryBody.append(
    facts([
      ["状态", badge(row.status)],
      ["归档大小", bytes(row.archive_bytes)],
      ["最近投递阶段", badge(row.stage || row.status)],
      ["远端记录", row.upload_id || "尚未建立"],
      ["验收收据", row.receipt?.receipt_id || "尚未收到"],
      ["错误代码", row.last_error || row.error || "无已报告错误"],
      ["本机投递尝试", row.attempts ?? "不在此端观测"],
      ["云端验证处理异常（本轮）", row.verify_attempts ?? "未观测"],
    ]),
  );
  if (local && row.remote)
    deliveryBody.append(
      facts([
        [
          "云端最近状态",
          badge(row.remote.delivery_status || row.remote.status),
        ],
        ["云端观测时间", date(row.remote.observed_at)],
        ["云端收据", row.remote.receipt?.receipt_id || "未观测"],
      ]),
    );
  deliveryBody.append(
    node(
      "p",
      "阶段是最近观测，不证明后台仍在运行。本机尝试含正常状态查询；云端仅累计本轮处理异常，成功验证不会增加，人工重新投递时归零。",
      "muted small",
    ),
  );
  if (local && config.cloudUrl && /^[a-f0-9]{32}$/.test(row.upload_id || ""))
    deliveryBody.append(
      link(
        "查看同一份云端记录 ↗",
        `${config.cloudUrl}/app/?view=collections&id=${row.upload_id}`,
        true,
      ),
    );
  if (row.remote?.status === "unavailable" || row.remote?.status === "stale")
    deliveryBody.append(
      node("p", "云连接暂不可用，展示最近确认的本地收据。", "muted small"),
    );
  delivery.append(deliveryBody);
  grid.append(delivery);
  fragment.append(grid);
  const runs = panel("局边界", "session 与 run 分开；缺少边界不猜测完整局");
  const runsBody = node("div", null, "panel-body");
  if (!record.runs?.length)
    runsBody.append(
      empty("未观测到可展示的局边界", "这不会覆写已有上传收据或决策证据。"),
    );
  for (const run of record.runs || [])
    runsBody.append(
      facts([
        ["Run", run.run_id],
        ["原生开始", run.start_observed ? date(run.started_at) : "未观测"],
        ["原生结束", run.terminal_observed ? date(run.ended_at) : "未观测"],
        ["原生恢复次数", number(run.native_resumes)],
        [
          "结果",
          { victory: "胜利", defeat: "失败", natural_defeat: "自然败北", abandoned: "放弃" }[
            run.outcome
          ] ||
            run.outcome ||
            "未观测",
        ],
      ]),
    );
  runsBody.append(
    node(
      "p",
      `Recorder 暂停次数：${number(record.recorder_pauses)}。边界齐全不等于通过 uninterrupted Full-Run 审计。`,
      "muted small",
    ),
  );
  runs.append(runsBody);
  fragment.append(runs);
  const lower = node("div", null, "grid-two");
  const timeline = panel("已观测里程碑", "不回填未记录的历史阶段或精确耗时");
  const timeBody = node("div", null, "panel-body"),
    list = node("ol", null, "timeline");
  const milestones = [
    ...(record.closed_at
      ? [{ operation: "closed", at: record.closed_at }]
      : []),
    ...(row.timeline || []),
  ];
  for (const item of milestones) {
    const line = node("li");
    line.append(
      node("strong", events[item.operation] || item.operation || "状态事件"),
      node("time", date(item.at)),
    );
    list.append(line);
  }
  timeBody.append(
    milestones.length
      ? list
      : empty("阶段时间未观测", "当前收据仍是验收依据。"),
  );
  timeline.append(timeBody);
  lower.append(timeline);
  const research = panel(
    "研究检查与使用",
    "准入结论绑定输入集合，不给一份采集永久贴标签",
  );
  const researchBody = node("div", null, "panel-body");
  researchBody.append(
    badge(row.research?.status || "not_assessed"),
    node(
      "p",
      row.research?.reason ||
        "尚无当前已索引的研究检查。云端验收不会自动产生 Dataset 或训练。",
      "muted small",
    ),
  );
  for (const identity of row.research?.dataset_ids || [])
    researchBody.append(
      button(
        `Dataset ${short(identity)} →`,
        () => navigate("datasets", identity),
        "link",
      ),
    );
  research.append(researchBody);
  lower.append(research);
  fragment.append(lower);
  fragment.append(technical(row));
  return fragment;
}
function catalog(data, kind) {
  const fragment = document.createDocumentFragment();
  if (
    data.status === "not_authorized" ||
    data.availability === "not_authorized"
  )
    return empty(
      "当前身份没有此目录的查看权限",
      "采集和上传不受影响。研究元数据及原始数据权限分别管理。",
    );
  if (["unavailable", "not_configured"].includes(data.status))
    return empty(
      "暂时无法读取目录",
      "检查系统页的云连接。尚未取得的远端状态不代表目录为空。",
    );
  const rows = data.item ? [data.item] : data.items || [],
    section = panel(
      views[kind][0],
      kind === "models"
        ? "已存在的模型、检查点与评估；不展示未取得的性能结论。"
        : "版本与来源由不可变 artifact manifest 标识。",
    );
  if (!rows.length)
    section.append(
      empty(
        kind === "datasets" ? "尚无可展示的数据集" : "尚无可展示的模型或评估",
        kind === "datasets"
          ? "先按固定输入集合运行 STPD 准入。需要足够独立 run 分组；上传成功不会自动建集。"
          : "完成受控作业并索引产物后会出现在这里。目前不启动 GPU，也不自动加载模型。",
      ),
    );
  for (const item of rows) {
    const body = node("div", null, "panel-body");
    if (!state.id)
      body.append(
        button(
          `查看 ${short(item.artifact_id)} →`,
          () => navigate(kind, item.artifact_id),
          "link",
        ),
      );
    else body.append(button("← 返回目录", () => kind === "datasets" ? window.SpireProject.openDatasetLibrary() : navigate(kind), "link"));
    if (kind === "datasets") {
      body.append(node("h2", item.display_name || `数据集 ${short(item.artifact_id)}`));
      body.append(facts([["收录决策", item.metadata?.records ?? "未报告"],
        ["数据契约", item.metadata?.schema ?? "未报告"],
        ["分组切分", item.metadata?.split_status === "assigned" ? "已按真实局分组" : "尚未确认，查看报告"],
        ["使用条件", "已生成固定版本；训练准入与模型效果需单独验证"]]));
      body.append(button("返回数据集列表下载", () => window.SpireProject.openDatasetLibrary(), "link"));
    }
    body.append(
      facts([
        ["类型", item.kind],
        ["Artifact ID", item.artifact_id],
        ["源码", item.producer?.source_revision || item.source_revision],
        ["收录时间", date(item.indexed_at)],
        [
          "本机下载",
          local
            ? item.local_download
              ? "存在下载收据；实际加载尚未验证"
              : "未发现本机下载收据"
            : "在本机查看",
        ],
      ]),
    );
    for (const parent of item.parents || [])
      body.append(
        node("p", `${parent.role}: ${parent.artifact_id}`, "mono muted"),
      );
    if (
      local &&
      kind === "models" &&
      /^[a-f0-9]{64}$/.test(item.artifact_id || "")
    )
      body.append(
        node(
          "p",
          `uv run --locked python -m stpd.workbench project download --config /ABS/project.json --artifact ${item.artifact_id}`,
          "command mono",
        ),
      );
    body.append(technical(item));
    section.append(body);
  }
  if (!state.id) paginate(section, data);
  fragment.append(section);
  return fragment;
}
function jobs(data) {
  if (["unavailable", "not_configured"].includes(data.status))
    return empty(
      "暂时无法读取作业",
      "连接状态未知不代表没有作业。请查看系统页。",
    );
  const fragment = document.createDocumentFragment();
  fragment.append(
    node(
      "div",
      "只读作业视图。预算为 0 禁止新计算启动，不代表已经运行的外部作业自动停止。",
      "banner",
    ),
  );
  const section = panel(
    "作业与执行尝试",
    "unknown 必须核对拥有者，不自动重新提交",
  );
  if (!data.items?.length)
    section.append(
      empty(
        "当前没有可展示的作业",
        "数据采集与上传可以继续。只有明确配置与预算授权的计算才会启动。",
      ),
    );
  for (const item of data.items || []) {
    const body = node("div", null, "panel-body");
    body.append(
      facts([
        ["Job", item.id || item.job_id],
        ["类型", item.kind],
        ["状态", badge(item.status)],
        ["输入", item.input_id],
        ["执行尝试", item.attempt_id || "尚未分配"],
        ["预算预留单位", item.reserved_units ?? "未观测"],
        ["时长上限 / 秒", item.max_seconds ?? "未观测"],
        [
          "结果",
          typeof item.result === "object" && item.result !== null
            ? JSON.stringify(item.result)
            : item.result || "尚无结果",
        ],
      ]),
      technical(item),
    );
    section.append(body);
  }
  paginate(section, data);
  fragment.append(section);
  return fragment;
}
function system(data) {
  const fragment = document.createDocumentFragment(),
    grid = node("div", null, "grid-two");
  const connection = panel(
    local ? "本机与云端连接" : "浏览器身份与范围",
    "设备凭据与网页登录分别管理",
  );
  const body = node("div", null, "panel-body");
  body.append(
    facts([
      ["当前模式", local ? "本机" : "云端"],
      ["后台投递", data.delivery_status || "在本机查看"],
      ["云连接", data.cloud_status || (local ? "未观测" : "当前请求已认证")],
      [
        "查看权限",
        data.access?.role || data.role || (local ? "本设备" : "当前授权范围"),
      ],
      ["设备最近联络", "在账号与电脑查看；最近联络不代表当前在线"],
    ]),
  );
  if (data.access?.role === "admin" || data.role === "admin") body.append(link("采集说明设置", "?view=campaigns"));
  if (local) {
    body.append(
      node(
        "p",
        "在“账号与电脑”登录后，两端查看相同授权数据；上方切换项目范围或本机队列。",
        "small muted",
      ),
    );
    if (data.platform_endpoint?.url)
      body.append(
        link(
          data.platform_endpoint.label || "Platform 本地端点 ↗",
          data.platform_endpoint.url,
          true,
        ),
      );
  }
  connection.append(body);
  grid.append(connection);
  const evidence = panel("版本与运行证据", "源码、已安装、已加载是不同层次");
  const evidenceBody = node("div", null, "panel-body"),
    identity = data.identity || data.producer || {};
  const compute = data.compute || data.cloud?.compute || {},
    backup = data.backup || data.cloud?.backup || {};
  const storage = data.storage || data.cloud?.storage || {},
    capacity = storage.capacity || {};
  evidenceBody.append(
    facts([
      [local ? "本机工作台来源" : "Hub 来源", identity.source_revision],
      ...(local ? [["Hub 来源", data.cloud?.producer?.source_revision]] : []),
      ["更新规则", "来源不同不等于不兼容；按实际接口和所需功能决定更新"],
      ["推荐版本", "查看正式发布说明；不会随 main 自动安装"],
      ["依赖锁", identity.uv_lock_sha256],
      ["查询时间", date(data.observed_at)],
      ["计算预算", number(compute.budget_units)],
      [
        "新计算预留",
        compute.budget_allows_reservations === false
          ? "预算不允许"
          : "以拥有者实际配置为准",
      ],
      [
        "调度",
        compute.paused === true
          ? "已暂停"
          : compute.paused === false
            ? "未暂停"
            : "未观测",
      ],
    ]),
  );
  evidenceBody.append(link(
    "正式发布、支持范围与升级说明 ↗",
    "https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/releases",
    true,
  ));
  evidence.append(evidenceBody);
  grid.append(evidence);
  fragment.append(grid);
  const ops = panel("维护与恢复", "问题保留证据，修复产生新版本");
  const opsBody = node("div", null, "panel-body");
  opsBody.append(
    facts([
      [
        "备份状态",
        backup.last_status ||
          backup.availability ||
          "此接口尚未观测；查看运维收据",
      ],
      ["最近成功备份", date(backup.last_success_at)],
      ["备份新鲜度", {ok: "在有效期内", attention: "需要检查", unknown: "未观测"}[backup.freshness] || "未观测"],
      ["距成功备份", Number.isFinite(backup.age_seconds) ? `${(backup.age_seconds / 3600).toFixed(1)} 小时` : "未观测"],
      ["外部告警", "未在此界面验证"],
      ["完整主机恢复", "不由数据库备份或页面可用推断"],
    ]),
  );
  if (storage.capacity) {
    opsBody.append(
      node("h3", "Hub 数据所在文件系统"),
      facts([
        ["容量状态", {ok: "运行余量充足", attention: "容量不足 · 暂缓新部署和批量处理", unknown: "容量未完整观测"}[capacity.status] || "未观测"],
        ["可用 / 总容量", `${bytes(storage.free_bytes)} / ${bytes(storage.total_bytes)}`],
        ["运行保留空间", bytes(capacity.reserve_bytes)],
        ["可用 inode / 保留", `${number(capacity.free_inodes)} / ${number(capacity.reserve_inodes)}`],
        ["观测时间", date(capacity.observed_at)],
      ]),
      node("p", "此处仅观察 Hub 数据所在文件系统。Docker 镜像若在其他盘，需运行主机容量检查；未统计可回收缓存，不会自动删除数据或镜像。", "small muted"),
    );
  }
  const list = node("ul", null, "help-list");
  for (const text of [
    "关闭浏览器不停止本机后台；project stop 才停止该项目服务。",
    "录制缺陷交给 Platform，投递/服务/研究问题按所属层处理。",
    "保留原始录制、bundle 和失败收据；公开问题报告只包含经脱敏的摘要。",
  ])
    list.append(node("li", text));
  opsBody.append(list);
  ops.append(opsBody);
  fragment.append(ops, technical(data));
  return fragment;
}
const state = {
  view: "overview",
  id: null,
  offset: 0,
  limit: 25,
  search: "",
  busy: false,
  serial: 0,
};
let renderedContext = null;
function readLocation() {
  const params = new URLSearchParams(location.search),
    requested = params.get("view");
  state.view = Object.hasOwn(views, requested) &&
    (!["local-workspace", "local-home"].includes(requested) || localShell)
    ? requested
    : (localShell ? "local-home" : "collections");
  const id = params.get("id");
  state.id =
    ["collections", "datasets", "models", "local-workspace"].includes(state.view) &&
    /^(?:[a-f0-9]{32}|[a-f0-9]{64})$/.test(id || "")
      ? id
      : null;
  state.offset = Math.max(
    0,
    Number.parseInt(params.get("offset") || "0", 10) || 0,
  );
}
function navigate(view, id = null) {
  state.search = "";
  state.offset = 0;
  const query = new URLSearchParams({ view });
  if (id) query.set("id", id);
  history.pushState({}, "", "?" + query);
  readLocation();
  load(true, false);
}
function changePage(offset) {
  if (!Number.isInteger(offset)) return;
  const query = new URLSearchParams({
    view: state.view,
    offset: String(offset),
  });
  history.pushState({}, "", "?" + query);
  readLocation();
  load(true, false);
}
function navigationGroup(view) {
  if (["collections", "statistics", "games", "downloads", "overview"].includes(view)) return "collections";
  if (["research", "models", "jobs"].includes(view)) return "research";
  return view;
}
function renderTaskTabs(view) {
  const target = $("task-tabs");
  if (!target) return;
  if (localShell && !config.cloudUrl && ["research", "models", "jobs"].includes(view)) {
    target.replaceChildren();
    return;
  }
  const groups = {
    collections: [["collections", "记录"], ["statistics", "统计"], ["games", "对局与片段"], ["downloads", "下载"]],
    research: [["research", "训练与分析"], ["models", "模型目录"], ["jobs", "作业"]],
  };
  target.replaceChildren();
  for (const [key, label] of groups[navigationGroup(view)] || []) {
    const item = link(label, `?view=${key}`);
    item.className = `task-tab${key === view ? " active" : ""}`;
    item.addEventListener("click", event => { if (event.button === 0 && !event.metaKey && !event.ctrlKey) { event.preventDefault(); navigate(key); } });
    target.append(item);
  }
}
function localTeamEntryUnavailable() {
  const page = node("div", null, "project-page");
  const explanation = panel(
    "此入口当前用于团队资料",
    "连接团队服务并登录后，可查看团队数据集、训练与模型记录。",
  );
  explanation.append(node("p", "本机资料、模型实战和游戏内录制可在本机工作台继续使用。"));
  page.append(explanation);
  const localLinks = panel("本机入口");
  localLinks.append(
    link("本机资料 →", "?view=local-workspace"),
    link("模型实战 →", "?view=local-models"),
    link("真人采集 →", "?view=campaigns"),
  );
  page.append(localLinks);
  return page;
}
async function load(manual = false, forceIdentity = manual) {
  if (!manual && (document.activeElement?.id === "device-name" || document.activeElement?.closest("[data-editor], [data-project-editor]"))) return;
  if (state.busy && !manual) return;
  state.busy = true;
  const serial = ++state.serial;
  const view = state.view,
    id = state.id;
  if (localShell && view === "local-home") window.SpireIdentity.localOnly();
  const record = view === "connect" ? new URLSearchParams(location.search).get("flow") : id;
  const pageContext = `${view}:${record || ""}:${state.offset}:${state.limit}:${view === "datasets" && !id ? window.SpireProject.datasetContext() : ""}`;
  let context = `${pageContext}:${window.SpireIdentity.context()}`;
  if (renderedContext !== context) {
    $("content").replaceChildren(
      empty("正在读取…", "等待当前页面的拥有者状态。"),
    );
    $("updated").textContent = "当前页面尚未取得状态";
  }
  $("title").textContent = views[view][0];
  $("subtitle").textContent = views[view][1];
  document
    .querySelectorAll("[data-view]")
    .forEach((item) =>
      item.classList.toggle("active", item.dataset.view === navigationGroup(view)),
    );
  renderTaskTabs(view);
  $("content").setAttribute("aria-busy", "true");
  $("refresh").disabled = true;
  if (localShell && view === "local-home") {
    if (serial !== state.serial) return;
    local = true;
    $("content").replaceChildren(localHome());
    $("notice").replaceChildren();
    $("updated").textContent = "本机入口 · 尚未读取云端状态";
    $("connection").textContent = "本机工作台";
    renderedContext = `${pageContext}:${window.SpireIdentity.context()}`;
    $("content").setAttribute("aria-busy", "false");
    state.busy = false;
    $("refresh").disabled = false;
    return;
  }
  if (
    localShell &&
    !config.cloudUrl &&
    ["datasets", "research", "models", "jobs"].includes(view)
  ) {
    if (serial !== state.serial) return;
    local = true;
    $("content").replaceChildren(localTeamEntryUnavailable());
    $("notice").replaceChildren();
    $("updated").textContent = "团队入口暂不可用";
    $("connection").textContent = "本机工作台";
    renderedContext = `${pageContext}:${window.SpireIdentity.context()}`;
    $("content").setAttribute("aria-busy", "false");
    state.busy = false;
    $("refresh").disabled = false;
    return;
  }
  const route = id ? `${view}/${id}` : view;
  const query =
    ["collections", "datasets", "jobs", "models"].includes(view) && !id
      ? `?limit=${state.limit}&offset=${state.offset}`
      : "";
  try {
    const localWorkspaceOnly = localShell && view === "local-workspace";
    const identity = localWorkspaceOnly
      ? window.SpireIdentity.localOnly()
      : await window.SpireIdentity.refresh(forceIdentity);
    if (serial !== state.serial) return;
    document.querySelectorAll("[data-admin-only]").forEach(item => { item.hidden = identity?.principal?.role !== "admin"; });
    if (["members", "statistics", "downloads", "research", "campaigns"].includes(view) || (["datasets", "games"].includes(view) && !id)) {
      window.SpireIdentity.ensureProjectScope();
    }
    const identityContext = window.SpireIdentity.context();
    context = `${pageContext}:${identityContext}`;
    if (renderedContext !== context) $("content").replaceChildren(empty("正在读取…", "当前账号与电脑范围"));
    local = window.SpireIdentity.isLocal();
    if (!manual && (view === "local-models" || !id) && renderedContext === context && window.SpireProject.refresh &&
        await window.SpireProject.refresh(view)) {
      if (view !== "local-models" && serial === state.serial && identityContext === window.SpireIdentity.context())
        $("updated").textContent = "当前页面状态已检查";
      return;
    }
    if (["members", "statistics", "downloads", "research", "local-models", "local-workspace", "campaigns", "evaluations"].includes(view) || (["datasets", "games"].includes(view) && !id)) {
      const opened = [...document.querySelectorAll("details[open]")].map(item => item.dataset.preserve);
      const content = await window.SpireProject.render(view, identity, shell => {
        if (serial === state.serial && identityContext === window.SpireIdentity.context()) $("content").replaceChildren(shell);
      });
      if (serial !== state.serial || identityContext !== window.SpireIdentity.context()) return;
      $("content").replaceChildren(content);
      document.querySelectorAll("details[data-preserve]").forEach(item => {
        item.open = opened.includes(item.dataset.preserve);
      });
      renderedContext = context;
      $("updated").textContent = localWorkspaceOnly ? "本机资料读取完成" : "当前账号下的服务观测";
      $("connection").textContent = identity?.status === "signed_in" ? "已通过身份验证" : "本机工作台";
      return;
    }
    if (view === "devices" || view === "connect") {
      const content = view === "devices" ? window.SpireIdentity.renderDevices() :
        await window.SpireIdentity.renderConnect();
      if (serial !== state.serial || identityContext !== window.SpireIdentity.context()) return;
      $("content").replaceChildren(content);
      renderedContext = context;
      $("updated").textContent = identity?.observed_at
        ? `账号更新于 ${date(identity.observed_at)}`
        : "账号观测时间未提供";
      $("connection").textContent = identity?.status === "signed_in"
        ? "已通过身份验证"
        : identity?.status === "signed_out"
          ? "未登录项目账号"
          : identity?.status === "reconnect_required"
            ? "需要重新登录"
            : "账号状态暂不可用";
      return;
    }
    const response = await fetch(window.SpireIdentity.api(route, query), {
      cache: "no-store",
      credentials: "same-origin",
      redirect: "error",
      signal: AbortSignal.timeout(12000),
    });
    if (!response.ok)
      throw new Error(
        response.status === 401 || response.status === 403
          ? "authentication_required"
          : "unavailable",
      );
    const data = await response.json();
    if (serial !== state.serial || identityContext !== window.SpireIdentity.context()) return;
    if (data.error) throw new Error(data.error);
    const opened = [...document.querySelectorAll("details[open]")].map(
      (item) => item.dataset.preserve,
    );
    const focusedSearch = document.activeElement?.type === "search";
    const selection = focusedSearch
      ? document.activeElement.selectionStart
      : null;
    let content =
      view === "overview"
        ? overview(data)
        : view === "collections"
          ? id
            ? detail(data)
            : collections(data)
          : view === "jobs"
            ? jobs(data)
            : view === "system"
              ? system(data)
              : catalog(data, view);
    if (view === "overview") {
      const setup = await window.SpireProject.render("collection-overview", identity);
      if (serial !== state.serial || identityContext !== window.SpireIdentity.context()) return;
      const page = document.createDocumentFragment();
      page.append(setup, content);
      content = page;
    }
    if (view === "collections" && id && /^[a-f0-9]{32}$/.test(id)) {
      const quality = await window.SpireProject.render("record-quality", identity);
      if (serial !== state.serial || identityContext !== window.SpireIdentity.context()) return;
      content.append(quality);
    }
    $("content").replaceChildren(content);
    renderedContext = context;
    document.querySelectorAll("details[data-preserve]").forEach((item) => {
      item.open = opened.includes(item.dataset.preserve);
    });
    if (focusedSearch) {
      const input = document.querySelector("input[type=search]");
      input?.focus();
      input?.setSelectionRange(selection, selection);
    }
    $("notice").replaceChildren();
    const stale =
      ["stale", "unavailable", "not_configured"].includes(data.status) ||
      ["stale", "unavailable", "not_configured"].includes(data.cloud?.status);
    if (stale)
      $("notice").append(
        node(
          "div",
          "部分状态暂不可用或配置尚未完成。已知数据保留，请留意最后更新时间并查看系统页。",
          "banner",
        ),
      );
    $("updated").textContent = `更新于 ${date(data.observed_at)}`;
    $("connection").textContent = stale
      ? "显示最近已知状态"
      : local
        ? "本机状态已更新"
        : "已通过身份验证";
  } catch (error) {
    if (serial !== state.serial) return;
    if (!window.SpireIdentity.isLocal()) $("content").replaceChildren();
    const auth = error.message === "authentication_required";
    $("notice").replaceChildren(
      node(
        "div",
        auth
          ? localShell
            ? "当前账号或设备授权不可用。请到“账号与电脑”重新登录；原始数据保留在本机。"
            : "登录已过期或没有访问权限。请重新打开云端入口登录。"
          : "暂时无法取得新状态。已有画面是此前观测，不代表新的成功或失败。",
        "banner error",
      ),
    );
    $("connection").textContent = auth ? "需要登录 / 授权" : "连接暂不可用";
    $("notice").append(link(localShell ? "账号与电脑 →" : "重新登录云端 →",
      localShell ? "?view=devices" : "/app/"));
  } finally {
    if (serial === state.serial) {
      state.busy = false;
      $("refresh").disabled = false;
      $("content").setAttribute("aria-busy", "false");
    }
  }
}
document.querySelectorAll("[data-view]").forEach((item) =>
  item.addEventListener("click", (event) => {
    event.preventDefault();
    navigate(item.dataset.view);
  }),
);
$("refresh").addEventListener("click", () => load(true));
$("lifecycle-note").textContent = localShell
  ? "本机上传状态以真人采集页的实际配置与观测为准"
  : "邀请制项目 · 共享数据按权限访问";
window.SpireProject.reload = () => load(true, false);
window.SpireProject.navigate = (view, id = null) => navigate(view, id);
window.addEventListener("popstate", () => {
  readLocation();
  load(true, false);
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) load();
});
setInterval(() => {
  if (!document.hidden) load();
}, 10000);
window.SpireIdentity.connect((reset = false) => {
  ++state.serial;
  $("content").replaceChildren();
  renderedContext = null;
  if (reset) history.replaceState({}, "", "?view=" + state.view);
  readLocation(); load(true);
});
readLocation();
load();
