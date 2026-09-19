"use strict";

// Presentation only. Hub owns membership/data; the local service owns files and processes.
window.SpireProject = (() => {
  const local = document.body.dataset.mode === "local";
  const hex = (value, length = 64) =>
    typeof value === "string" &&
    new RegExp(`^[a-f0-9]{${length}}$`).test(value);
  const selectionId = (value) =>
    typeof value === "string" && /^[a-z0-9-]{1,80}$/.test(value);
  const supported = new Set([
    "members",
    "statistics",
    "downloads",
    "research",
    "local-models",
    "campaigns",
    "collection-overview",
    "datasets",
    "games",
    "evaluations",
    "record-quality",
  ]);
  let current = null;
  let account = null;
  let offsets = new Map();
  let drafts = new Map();
  let selected = new Set();
  let artifacts = new Map();
  let exportId = null;
  let readiness = new Map();
  const pending = new Set();
  let activeDataset = null;
  const datasetSnapshots = new WeakMap();
  const datasetReads = new Map();
  let datasetReadEpoch = 0;
  const labels = {
    invited: "待首次登录",
    active: "已启用",
    disabled: "已停用",
    member: "成员",
    admin: "管理员",
    accepted: "游戏接受",
    proved: "因果后继已证明",
    canonical: "已录入决策",
    real_failures: "真实录制失败",
    cancelled: "已取消",
    aborted: "中断",
    diagnostics: "诊断",
    unsupported: "不在记录范围",
    unresolved: "后继未解决",
    invalidations: "显式 invalidation",
    accepted_children: "接受的子决策",
    canonical_children: "已录入子决策",
    native_starts: "观测到原生开局",
    native_ends: "观测到原生终局",
    assigned_run_count: "已分配 run 的数量",
    recorder_pauses: "记录器暂停",
    device: "电脑",
    status: "接收状态",
    campaign: "采集活动",
    format: "记录格式",
    disposition: "记录 disposition",
    action_family: "动作类别",
    family: "动作类别",
    surface: "交互界面",
    decision_kind: "决策类型",
    character: "角色",
    difficulty: "难度",
    game_version: "游戏版本",
    evidence: "证据",
    dataset: "数据集",
    protocol: "固定分配 / 协议",
    model_view: "模型输入",
    feature_set: "冻结特征",
    training_input: "训练输入",
    experiment: "实验配置",
    run: "训练运行",
    run_result: "训练结果",
    checkpoint: "检查点",
    model: "模型",
    offline_evaluation: "离线评估",
    live_evaluation: "游戏评估",
    performance: "性能",
    analysis: "分析",
    idle: "尚未加载",
    loading: "正在加载",
    loaded: "已加载",
    stopped: "已停止",
    failed: "操作失败",
    runtime_exited: "进程已退出",
    recovery_required: "需要确认上一进程",
    command_unknown: "命令结果未知",
    downloading: "正在下载并校验",
    verified: "文件校验完成",
    quarantined: "已接收，隔离待审",
    pending: "服务处理中",
    running: "正在处理",
    queued: "等待处理",
    completed: "操作已完成",
    unknown: "结果未知",
    ready_to_load: "可请求加载",
    blocked: "条件未满足",
    human: "人类控制",
    shadow: "Shadow 只评分",
    one_step: "执行一个决策",
    auto: "自动决策",
    backend: "运行硬件与依赖",
    runtime_package: "模型运行器",
    public_contract: "模型接口",
    policy_identity: "模型组合身份",
    training_ready: "训练产物清单",
    checkpoint_sidecar: "模型文件说明",
  };
  const show = (value) =>
    labels[value] ||
    (value === null || value === undefined ? "未知" : String(value));
  const count = (value) =>
    typeof value === "number" && Number.isFinite(value)
      ? value.toLocaleString("zh-CN")
      : "未知";
  const when = (value) => {
    if (value === null || value === undefined || value === "")
      return "未提供观测时间";
    const date = new Date(typeof value === "number" ? value * 1000 : value);
    return Number.isFinite(date.getTime())
      ? date.toLocaleString("zh-CN")
      : "未提供观测时间";
  };
  const bytes = (value) => {
    if (!Number.isFinite(value) || value < 0) return "大小未知";
    if (value < 1024) return `${value} B`;
    const unit = Math.min(3, Math.floor(Math.log(value) / Math.log(1024)));
    return `${(value / 1024 ** unit).toLocaleString("zh-CN", { maximumFractionDigits: 1 })} ${["B", "KiB", "MiB", "GiB"][unit]}`;
  };
  const el = (tag, text, cls) => {
    const item = document.createElement(tag);
    if (text !== undefined && text !== null) item.textContent = String(text);
    if (cls) item.className = cls;
    return item;
  };
  const panel = (title, description) => {
    const box = el("section", null, "panel project-panel");
    if (title) box.append(el("h2", title));
    if (description) box.append(el("p", description, "muted"));
    return box;
  };
  const empty = (title, description) => {
    const box = el("div", null, "empty-state");
    box.append(el("strong", title), el("p", description));
    return box;
  };
  const badge = (text, kind = "neutral") => el("span", text, `badge ${kind}`);
  const link = (label, target) => {
    const item = el("a", label, "button");
    item.href = target;
    if (target.startsWith("?view=")) {
      item.onclick = (event) => {
        if (
          event.button !== 0 ||
          event.metaKey ||
          event.ctrlKey ||
          event.shiftKey ||
          event.altKey ||
          !window.SpireProject.navigate
        )
          return;
        const query = new URLSearchParams(target);
        event.preventDefault();
        window.SpireProject.navigate(query.get("view"), query.get("id"));
      };
    }
    return item;
  };
  const route = (view, id) =>
    `?view=${encodeURIComponent(view)}${id ? `&id=${encodeURIComponent(id)}` : ""}`;
  const project = (path) => {
    if (window.SpireIdentity?.api && !window.SpireIdentity.isLocal()) {
      const split = path.indexOf("?");
      return window.SpireIdentity.api(
        split < 0 ? path : path.slice(0, split),
        split < 0 ? "" : path.slice(split),
      );
    }
    return (local ? "/api/project/" : "/app/api/") + path;
  };
  const member = (path) => (local ? "/api/member/" : "/app/api/member/") + path;
  const cloudLink = (view) => {
    try {
      const url = new URL(document.body.dataset.cloudUrl);
      if (
        url.protocol !== "https:" ||
        url.username ||
        url.password ||
        !["", "/"].includes(url.pathname) ||
        url.search ||
        url.hash
      )
        return null;
      return url.origin + "/app/" + route(view);
    } catch {
      return null;
    }
  };
  const scope = () => window.SpireIdentity?.context?.() || "";
  const live = (ctx) => current === ctx && scope() === ctx.scope && location.search === ctx.search;
  const signedIn = (ctx) =>
    Boolean(ctx.identity?.principal) &&
    (!local || ctx.identity.status === "signed_in");
  const note = (ctx, text, kind = "good") => {
    if (!live(ctx)) return;
    const target = document.getElementById("notice");
    const message = el("div", text, `banner ${kind}`);
    message.setAttribute("role", "status");
    target.replaceChildren(message);
  };
  const failure = (error) => {
    const known = {
      authentication_required: "当前账号已过期或无权访问，请重新登录项目账号。",
      publication_already_started: "已进入最后保存阶段，请等待结果。完成后可以从列表移除。",
      selection_index_capacity: "所选数据超出本机处理缓存容量，请减少本次选择；已有录制和数据集不会被删除。",
      worker_memory_limit: "处理时达到内存上限。已核验的来源会保留，重试会复用这些结果。",
      worker_timeout: "本次处理超时。已完成的来源会保留，可继续同一任务。",
      worker_cpu_limit: "本次处理达到计算时间上限。已完成的来源会保留。",
      worker_killed: "处理进程被终止。请查看系统状态后继续同一任务。",
      worker_start_failed: "处理进程未能启动，请查看系统状态。",
      worker_exit_failed: "处理进程异常退出。已完成的来源会保留。",
      last_admin_required:
        "必须保留至少一位已激活的管理员。先让另一位管理员完成首次登录。",
      member_already_exists: "此邮箱已在成员列表中；请修改现有成员。",
      collection_not_shared: "这份记录已撤回项目访问，或尚未完成接收。请重新选择可用记录。",
      source_sharing_not_established: "数据集包含已撤回项目访问的来源。请修改选择，保留当前可用的数据。",
      dataset_not_available: "这个数据集已不在当前可用列表中，请回到数据集列表重新选择。",
      explicit_campaign_consent_required:
        "请分别确认真人来源、上传授权和项目成员共享。",
      owned_active_device_required:
        "只能为当前账号拥有且仍有效的电脑登记活动。",
      runtime_command_unknown:
        "命令可能仍在执行。请查看当前状态；不要重复命令，可交还人类或停止。",
      previous_operation_requires_recovery:
        "上一操作结果未确认，请先交还人类或停止。",
      runtime_connector_binding_required: "这份旧运行记录缺少游戏连接绑定。请结束后重新加载模型。",
      connector_identity_unavailable: "暂时无法核对游戏连接，请检查游戏和 Mod 状态。",
      model_readiness_blocked: "模型加载条件未满足，请查看逐项兼容性检查。",
      runtime_upgrade_required_for_model_control: "当前模型运行器需更新后才能开始测试；暂停和结束仍可使用。",
      runtime_game_mismatch: "模型运行器连接的不是当前游戏，请重新加载模型。",
      runtime_recovery_epoch_mismatch: "你已暂停或结束测试，这条旧操作已取消。",
      request_unknown: "请求结果尚未确认，请先刷新状态。不会自动重发操作。",
      request_unavailable: "暂时无法读取服务，请刷新重试。",
      absolute_game_directory_required: "请填写这台电脑上的游戏安装目录完整路径。",
      default_collection_fields_required: "请填写名称、录制说明和授权说明。",
      dataset_sources_required: "请先选择至少一份录制。",
      dataset_selection_limit: "一次最多选择 100 份录制。请缩小日期范围，或清空已有选择后再全选。",
      invalid_source_dates: "请填写有效日期，结束日期不能早于开始日期。",
      environment_identity_conflict: "所选录制的环境身份存在冲突。请修改选择；同一失败任务直接重试不会改变来源。",
      dataset_payload_inventory_missing: "此数据集尚未提供文件清单，请打开详情核对后下载。",
      minimum_two_decision_datasets_required: "请选择至少两个决策数据集后再合并。",
      training_test_overlap: "与对应训练集包含同一局或重复来源，请修改录制选择。",
      gold_reserved_data: "这些数据已封存为 Gold，只能用于受控评估或 Gold 合并。",
      gold_already_in_other_dataset: "这些数据已进入普通数据集，请为 Gold 选择独立数据。",
      gold_requires_gold_merge: "这些数据已属于 Gold，请在数据集列表合并已有 Gold。",
      test_merge_requires_only_test: "测试集只能与测试集合并，并保持测试用途。",
      gold_merge_requires_only_gold: "Gold 只能与 Gold 合并，合并后仍为 Gold。",
      gold_previously_used_for_training: "这些数据已有训练使用记录，不能作为 Gold。",
      gold_source_inventory_pending: "正在建立来源隔离索引。完成后可继续同一任务。",
      source_isolation_index_pending: "这份录制正在核对 Gold 隔离，整理完成后即可检查可用性。",
      quality_annotations_changed: "预览后操作标记发生了变化，请重新预览再确认生成。",
      held_out_data_cannot_train: "测试集和 Gold 不能作为训练输入。",
    };
    return (
      known[error?.message] ||
      (/^[a-z0-9_:.\/-]{1,120}$/.test(error?.message || "")
        ? `服务未完成请求（${error.message}）。`
        : "服务暂时不可用，请刷新状态。")
    );
  };
  async function request(ctx, path, body) {
    if (!live(ctx)) throw new Error("context_changed");
    const mutation = body !== undefined;
    if (mutation) { datasetReads.clear(); datasetReadEpoch++; }
    const payload =
      mutation && !local
        ? { ...body, csrf_token: ctx.identity?.csrf_token || "" }
        : body;
    let response;
    try {
      response = await fetch(path, {
        method: mutation ? "POST" : "GET",
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.timeout(mutation ? 25000 : 15000),
        headers: mutation
          ? {
              "Content-Type": "application/json",
              "X-CSRF-Token": ctx.identity?.csrf_token || "",
            }
          : {},
        body: mutation ? JSON.stringify(payload) : undefined,
      });
    } catch {
      throw new Error(mutation ? "request_unknown" : "request_unavailable");
    }
    let value;
    try {
      value = await response.json();
    } catch {
      throw new Error(mutation ? "request_unknown" : "request_unavailable");
    }
    if (response.status === 401 || response.status === 403) {
      datasetReads.clear(); datasetReadEpoch++;
      throw new Error("authentication_required");
    }
    const flowDiagnostic = !mutation && path === member("collection-flow") && value?.schema === "stpd/local-collection-flow-v1";
    const taskDiagnostic = !mutation && hex(value?.id,32) && path === member(`datasets/${value.id}`) && value.state === "failed";
    if (!response.ok || (value?.error && !flowDiagnostic && !taskDiagnostic))
      throw new Error(value?.error || "request_unavailable");
    if (!value || typeof value !== "object" || Array.isArray(value))
      throw new Error("request_unavailable");
    return value;
  }
  async function datasetRead(ctx, path, fresh = false) {
    const key = `${ctx.account}:${ctx.scope}:${path}`, saved = datasetReads.get(key);
    if (!fresh && saved && Date.now() - saved.at < 15000) return saved.value;
    const epoch = datasetReadEpoch, value = await request(ctx, path);
    if (live(ctx) && epoch === datasetReadEpoch) {
      datasetReads.set(key, {at:Date.now(), value});
      while (datasetReads.size > 32) datasetReads.delete(datasetReads.keys().next().value);
    }
    return value;
  }
  const authNotice = (ctx) => {
    const box = panel(
      "登录后查看项目",
      "登录状态与电脑上传授权分开管理。未登录不会删除本地记录。",
    );
    if (
      ctx.identity?.status === "unavailable" ||
      ctx.identity?.status === "reconnect_required"
    )
      box.append(el("p", "暂未取得可用账号状态，请重新连接。"));
    box.append(link("账号与电脑", local ? route("devices") : "/app/"));
    return box;
  };
  function command(ctx, name, label, operation, options = {}) {
    const button = el(
      "button",
      label,
      `button ${options.primary ? "primary" : ""} ${options.danger ? "danger" : ""}`,
    );
    button.type = "button";
    button.dataset.action = name;
    const key = `${ctx.account}:${name}`;
    button.disabled = Boolean(options.disabled) || pending.has(key);
    if (options.title) button.title = options.title;
    button.onclick = async () => {
      if (!live(ctx) || button.disabled || pending.has(key)) return;
      pending.add(key);
      button.disabled = true;
      try {
        await operation();
      } catch (error) {
        if (error.message !== "context_changed")
          note(ctx, failure(error), "error");
      } finally {
        pending.delete(key);
        button.disabled = Boolean(options.disabled);
      }
    };
    return button;
  }
  async function reload(ctx) {
    if (live(ctx)) await window.SpireProject.reload();
  }
  function fields(rows) {
    const list = el("dl", null, "fact-list");
    for (const [label, value] of rows) {
      const row = el("div", null, "fact-row");
      row.append(el("dt", label), el("dd", value ?? "未知"));
      list.append(row);
    }
    return list;
  }
  function technical(value, title = "查看精确身份与元数据") {
    const detail = el("details", null, "technical");
    detail.append(
      el("summary", title),
      el("pre", JSON.stringify(value, null, 2)),
    );
    return detail;
  }
  function input(form, label, name, value = "", type = "text") {
    const field = el(
      "label",
      null,
      type === "checkbox" ? "project-check" : "form-field",
    );
    const control = el("input");
    control.name = name;
    control.type = type;
    if (type === "checkbox") control.checked = value === true;
    else control.value = String(value);
    field.append(control, el("span", label));
    if (type !== "checkbox") field.replaceChildren(el("span", label), control);
    form.append(field);
    return control;
  }
  function select(form, label, name, options, value) {
    const field = el("label", null, "form-field"),
      control = el("select");
    control.name = name;
    for (const [key, title] of options) {
      const option = el("option", title);
      option.value = key;
      control.append(option);
    }
    control.value = value;
    field.append(el("span", label), control);
    form.append(field);
    return control;
  }
  function table(headers, rows) {
    const wrap = el("div", null, "table-wrap"),
      grid = el("table");
    const head = el("thead"),
      heading = el("tr"),
      body = el("tbody");
    headers.forEach((title) => heading.append(el("th", title)));
    head.append(heading);
    rows.forEach((values) => {
      const row = el("tr");
      values.forEach((value) => {
        const cell = el("td");
        if (value && typeof value === "object" && value.tagName !== undefined)
          cell.append(value);
        else if (value && typeof value === "object" && value.tag)
          cell.append(value);
        else cell.textContent = String(value ?? "未知");
        row.append(cell);
      });
      body.append(row);
    });
    grid.append(head, body);
    wrap.append(grid);
    return wrap;
  }
  function pager(ctx, key, data, limit = 25) {
    const offset = offsets.get(key) || 0,
      box = el("div", null, "pagination");
    box.append(
      el(
        "span",
        `共 ${count(data.total)} 项 · 当前第 ${Math.floor(offset / limit) + 1} 页`,
      ),
    );
    const actions = el("div", null, "pagination-actions");
    for (const [direction, label, disabled] of [
      [-1, "上一页", offset === 0],
      [
        1,
        "下一页",
        !(typeof data.total === "number" && offset + limit < data.total),
      ],
    ]) {
      actions.append(
        command(
          ctx,
          `${key}-page-${direction}`,
          label,
          async () => {
            offsets.set(key, offset + direction * limit);
            await reload(ctx);
          },
          { disabled },
        ),
      );
    }
    box.append(actions);
    return box;
  }
  function metric(label, value, explanation, kind = "") {
    const card = el("section", null, `metric ${kind}`);
    card.append(
      el("div", label, "metric-label"),
      el("div", count(value), "metric-value"),
      el("div", explanation, "metric-note"),
    );
    return card;
  }
  function metricCoverage(value) {
    return value
      ? `已知 ${count(value.known)} 份上传 · 未知 ${count(value.unknown)} 份${value.partial ? " · 部分汇总" : ""}`
      : "尚无此项摘要";
  }
  function facetPanel(title, facet) {
    const box = panel(title);
    if (!facet || facet.availability !== "available")
      box.append(el("p", "当前没有可用分类，未知不会归入其他类别。", "muted"));
    if (facet) {
      box.append(
        el(
          "p",
          `已知 ${count(facet.known)} · 未知 ${count(facet.unknown)}`,
          "small muted",
        ),
      );
      if ((facet.items || []).length)
        box.append(
          table(
            ["类别", "数量"],
            facet.items.map((item) => [show(item.value), count(item.count)]),
          ),
        );
      if (facet.truncated)
        box.append(
          el("p", "仅展示数量最多的 100 类，列表已截断。", "small muted"),
        );
    }
    return box;
  }
  function profilePanel(title, profile, unit) {
    const box = panel(title, unit);
    if (!profile) {
      box.append(
        empty(
          "尚未建立画像",
          "管理员可以显式刷新选定来源；打开此页面不会处理原始数据。",
        ),
      );
      return box;
    }
    box.append(
      fields([
        ["来源总数", count(profile.sources)],
        [
          "可用画像 / 缺失画像",
          `${count(profile.profiles_available)} / ${count(profile.profiles_missing)}`,
        ],
        ["画像中的记录次数", count(profile.records)],
      ]),
    );
    if (profile.partial || profile.availability !== "available")
      box.append(
        el(
          "p",
          "画像覆盖不完整。以下已知分类只代表已建立画像的来源。",
          "banner",
        ),
      );
    const facets = el("div", null, "project-facets");
    for (const [key, value] of Object.entries(profile.facets || {}))
      facets.append(facetPanel(show(key), value));
    box.append(facets);
    return box;
  }
  async function statistics(ctx) {
    const data = await request(ctx, project("statistics"));
    if (data.schema !== "stpd/project-statistics-v1")
      throw new Error("unsupported_statistics_schema");
    const box = el("div", null, "project-page");
    box.dataset.observedAt = data.observed_at || "";
    const metrics = data.metrics || {},
      cards = el("div", null, "metrics");
    cards.append(
      metric("接收记录次数", data.uploads, "单位为上传记录，不是完整游戏局数"),
      metric(
        "不同内容身份",
        data.unique_content_ids,
        `重复内容上传 ${count(data.duplicate_content_uploads)} 次`,
      ),
      metric(
        "已录入决策",
        metrics.canonical?.value,
        metricCoverage(metrics.canonical),
        "good",
      ),
      metric(
        "真实录制失败",
        metrics.real_failures?.value,
        metricCoverage(metrics.real_failures),
        "danger",
      ),
    );
    box.append(cards);
    const quality = panel(
      "记录质量与原生边界",
      "各项来自 Platform 摘要。不同上传中的计数相加，不代表已跨来源去重。",
    );
    quality.append(
      table(
        ["指标", "已知数量", "摘要覆盖"],
        Object.entries(metrics).map(([key, value]) => [
          show(key),
          count(value.value),
          metricCoverage(value),
        ]),
      ),
      el(
        "p",
        "原生开局与终局数量不能单独证明 uninterrupted Full Run；已分配 run 的数量也不是完整局数。",
        "small muted",
      ),
    );
    box.append(quality);
    const uploads = panel("上传记录分类", "这里每一项以上传记录次数为单位。"),
      facets = el("div", null, "project-facets");
    for (const key of ["device", "status", "campaign", "format", "disposition"])
      if (data.facets?.[key])
        facets.append(facetPanel(show(key), data.facets[key]));
    uploads.append(facets);
    box.append(
      uploads,
      profilePanel(
        "已接收数据的决策画像",
        data.collection_profiles,
        "单位为投影后的 canonical 记录出现次数；缺失画像不会算零，不声称 Dataset 已准入。",
      ),
      profilePanel(
        "数据集画像",
        data.dataset_profiles,
        "单位为已索引数据集中的记录出现次数；同一记录进入多个数据集可重复计数。",
      ),
    );
    const catalog = panel(
      "研究产物目录",
      "这是已发布或显式刷新的索引，不是云存储桶的完整盘点。",
    );
    const entries = Object.entries(data.artifacts?.counts || {});
    if (entries.length)
      catalog.append(
        table(
          ["类型", "已索引身份数"],
          entries.map(([key, value]) => [show(key), count(value)]),
        ),
      );
    else
      catalog.append(
        empty(
          "尚无已索引研究产物",
          "数据接收、Dataset 构建、训练与模型发布是不同步骤。",
        ),
      );
    catalog.append(link("查看训练、评估和分析", route("research")));
    box.append(catalog);
    box.append(
      el(
        "p",
        `服务观测：${when(data.observed_at)}。模型质量与训练许可需各自证据。`,
        "small muted",
      ),
    );
    return box;
  }
  function memberEditor(ctx, item) {
    const key = item?.member_id || "invite",
      original = item || {
        email: "",
        role: "member",
        device_quota: 3,
        enroll_devices: true,
      };
    const draft = drafts.get(`member:${key}`) || original;
    const form = el("div", null, "project-form");
    form.dataset.projectEditor = "member";
    const email = item
      ? null
      : input(form, "邀请邮箱", "email", draft.email, "email");
    const role = select(
      form,
      "角色",
      "role",
      [
        ["member", "成员"],
        ["admin", "管理员"],
      ],
      draft.role,
    );
    const quota = input(
      form,
      "有效电脑额度",
      "device_quota",
      draft.device_quota,
      "number",
    );
    quota.min = "0";
    quota.max = "128";
    quota.step = "1";
    const enroll = input(
      form,
      "允许自行绑定新电脑",
      "enroll_devices",
      draft.enroll_devices,
      "checkbox",
    );
    const read = () => ({
      ...(email ? { email: email.value.trim() } : {}),
      role: role.value,
      device_quota: Number(quota.value),
      enroll_devices: enroll.checked,
    });
    for (const control of [email, role, quota, enroll].filter(Boolean))
      control.oninput = () => drafts.set(`member:${key}`, read());
    form.append(
      command(
        ctx,
        `member-save-${key}`,
        item ? "保存权限设置" : "添加成员",
        async () => {
          const value = read();
          if (email && !/^[^\s@]+@[^\s@]+$/.test(value.email))
            throw new Error("invalid_member_email");
          if (
            !quota.value ||
            !Number.isInteger(value.device_quota) ||
            value.device_quota < 0 ||
            value.device_quota > 128
          )
            throw new Error("invalid_member_quota");
          if (
            value.role === "admin" &&
            original.role !== "admin" &&
            !window.confirm(
              "此账号将能管理成员、电脑与采集活动。确认授予管理员权限？",
            )
          )
            return;
          await request(
            ctx,
            "/app/api/admin/members" + (item ? `/${key}` : ""),
            value,
          );
          if (!live(ctx)) return;
          drafts.delete(`member:${key}`);
          note(
            ctx,
            item
              ? "成员权限已保存。"
              : "成员已添加。请把项目入口发给对方；系统未自动发送邀请邮件。",
          );
          await reload(ctx);
        },
        { primary: !item },
      ),
    );
    return form;
  }
  async function admin(ctx) {
    const box = panel(
      "成员与访问管理",
      "停用会撤销个人会话与该成员拥有的电脑授权，历史数据保留。至少留一位已激活管理员。",
    );
    if (ctx.identity.principal.role !== "admin") {
      box.append(
        empty(
          "当前是项目成员",
          "成员可以查看共享数据、登记自己的电脑和使用研究产物。管理账号由管理员负责。",
        ),
      );
      return box;
    }
    if (local) {
      box.append(
        el(
          "p",
          "管理操作需要云端浏览器的有效登录。工作台个人会话只用于项目读取。",
        ),
      );
      const target = cloudLink("members");
      if (target) box.append(link("打开云端成员管理 ↗", target));
      return box;
    }
    const data = await request(
      ctx,
      `/app/api/admin/members?limit=25&offset=${offsets.get("members") || 0}`,
    );
    const invitation = panel(
      "邀请成员",
      "首次登录时激活邀请，默认可绑定 3 台有效电脑。两种角色均可读取共享项目数据。",
    );
    invitation.append(memberEditor(ctx));
    box.append(invitation);
    for (const item of data.items || []) {
      if (!hex(item.member_id, 32)) continue;
      const row = panel(
        item.email,
        `${show(item.role)} · ${show(item.status)}`,
      );
      row.append(
        fields([
          [
            "当前有效 / 历史绑定电脑",
            `${count(item.active_device_count)} / ${count(item.owned_device_count)}`,
          ],
          ["最近权限变化", when(item.updated_at)],
        ]),
        memberEditor(ctx, item),
      );
      const actions = el("div", null, "project-actions");
      actions.append(
        command(
          ctx,
          `member-status-${item.member_id}`,
          item.status === "disabled" ? "恢复成员访问" : "停用成员及电脑",
          async () => {
            const disable = item.status !== "disabled";
            if (
              !window.confirm(
                disable
                  ? `停用 ${item.email}，撤销其工作台会话与电脑上传授权？历史数据保留。`
                  : `恢复 ${item.email} 的成员访问？已撤销的电脑凭据不会自动恢复。`,
              )
            )
              return;
            await request(ctx, `/app/api/admin/members/${item.member_id}`, {
              status: disable ? "disabled" : "active",
            });
            note(
              ctx,
              disable
                ? "成员已停用；历史数据保留。"
                : "成员访问已恢复。电脑需另行恢复授权。",
            );
            await reload(ctx);
          },
          { danger: item.status !== "disabled" },
        ),
      );
      actions.append(
        command(
          ctx,
          `member-sessions-${item.member_id}`,
          "撤销工作台个人会话",
          async () => {
            if (
              !window.confirm(
                `撤销 ${item.email} 的工作台个人会话？电脑上传授权与浏览器登录独立管理。`,
              )
            )
              return;
            await request(
              ctx,
              `/app/api/admin/members/${item.member_id}/revoke-sessions`,
              {},
            );
            note(ctx, "工作台个人会话已撤销。");
          },
        ),
      );
      row.append(actions);
      box.append(row);
    }
    box.append(pager(ctx, "members", data));
    return box;
  }
  async function research(ctx) {
    const box = el("div", null, "project-page");
    const archived = drafts.get("research-archived") === true;
    box.append(command(ctx, "research-archive-view", archived ? "返回当前记录" : "查看已归档", async () => {
      drafts.set("research-archived", !archived);
      for (const kind of ["training", "evaluations", "analyses"]) offsets.set(kind, 0);
      await reload(ctx);
    }));
    box.append(
      el(
        "p",
        "查看已索引的不可变产物与来源关系。这里不会启动 GPU、重建 Dataset 或把评估结果自动当作模型已合格。",
        "banner good",
      ),
    );
    for (const [kind, title, description] of [
      [
        "training",
        "训练记录",
        "固定分配、模型输入、特征、实验、运行和检查点。归档只收起记录，保留来源和使用关系。",
      ],
      [
        "evaluations",
        "评估记录",
        "离线、游戏和性能评估的已发布目录。封存评估内容不开放。",
      ],
      ["analyses", "分析记录", "明确发布的分析产物。没有记录时保持空白。"],
    ]) {
      const section = panel(title, description),
        offset = offsets.get(kind) || 0;
      try {
        const data = await request(
          ctx,
          project(`${kind}?limit=25&offset=${offset}${archived ? "&archived=true" : ""}`),
        );
        if (data.availability !== "available")
          section.append(
            empty("目录暂不可用", "当前没有可读取的目录权限或索引。"),
          );
        else if (!(data.items || []).length)
          section.append(
            empty(
              "暂无已索引记录",
              "这不等于云存储中没有文件，也不代表该阶段已完成。",
            ),
          );
        else
          for (const item of data.items) {
            const record = panel(show(item.kind), item.artifact_id);
            record.append(
              fields([
                [
                  "记录 / 局数",
                  `${count(item.metadata?.records)} / ${count(item.metadata?.runs)}`,
                ],
                ["文件总大小", bytes(item.payload_bytes)],
                ["索引时间", when(item.indexed_at)],
              ]),
            );
            if (hex(item.artifact_id))
              record.append(
                command(ctx, `research-visibility-${item.artifact_id}`, archived ? "恢复显示" : "归档", async () => {
                  await request(ctx, member("artifacts/visibility"), {ids:[item.artifact_id], archived:!archived});
                  await reload(ctx);
                }),
                command(
                  ctx,
                  `research-export-${item.artifact_id}`,
                  "加入导出清单（manifest）",
                  async () => {
                    artifacts.set(item.artifact_id, []);
                    note(
                      ctx,
                      "已选中该产物的 manifest。到“数据下载”生成清单；来源与数据文件不会自动下载。",
                    );
                  },
                ),
              );
            record.append(
              technical(
                {
                  artifact_id: item.artifact_id,
                  producer: item.producer,
                  metadata: item.metadata,
                  parents: item.parents,
                  lineage_partial: item.lineage_partial,
                },
                "精确来源与父产物",
              ),
            );
            section.append(record);
          }
        section.append(pager(ctx, kind, data));
      } catch (error) {
        section.append(empty("暂时无法读取此目录", failure(error)));
      }
      box.append(section);
    }
    box.append(
      link("查看作业状态", route("jobs")),
      link("打开数据下载", route("downloads")),
    );
    return box;
  }
  function exportFacts(ctx, data) {
    const box = panel(
      "固定导出清单",
      "清单记录准确的文件身份。生成清单不等于下载成功，也不会递归抓取父数据或绕过当前共享授权。",
    );
    if (data.schema !== "stpd/project-export-v1" || !hex(data.export_id))
      throw new Error("invalid_export_identity");
    box.append(
      fields([
        ["导出身份", data.export_id],
        [
          "文件数 / 总大小",
          `${count(data.files_count)} / ${bytes(data.total_bytes)}`,
        ],
        ["清单创建时间", when(data.created_at)],
      ]),
    );
    const rows = [];
    for (const file of data.files || []) {
      if (!hex(file.file_id) || !hex(file.sha256))
        throw new Error("invalid_export_file_identity");
      const kind = file.upload_id
        ? "原始录制包"
        : file.type === "manifest"
          ? "产物 manifest"
          : `数据文件 · ${file.role}`;
      const title = el("div");
      title.append(
        el("strong", kind),
        el("span", file.filename, "subtext mono"),
      );
      const cell = local
        ? el("span", "随完整清单校验下载")
        : link(
            "下载此文件",
            member(`exports/${data.export_id}/files/${file.file_id}`),
          );
      if (!local) cell.download = file.filename;
      rows.push([
        title,
        bytes(file.size),
        el("span", file.sha256, "mono"),
        cell,
      ]);
    }
    box.append(table(["文件", "大小", "SHA-256", "下载"], rows));
    const actions = el("div", null, "project-actions");
    const manifestLink = link(
      "保存清单 JSON",
      member(`exports/${data.export_id}`),
    );
    manifestLink.download = data.export_id + ".json";
    actions.append(
      manifestLink,
      link("此清单的固定页面", route("downloads", data.export_id)),
    );
    if (local)
      actions.append(
        command(
          ctx,
          `export-download-${data.export_id}`,
          "下载到本机并逐文件校验",
          async () => {
            await request(
              ctx,
              member(`exports/${data.export_id}/download`),
              {},
            );
            note(
              ctx,
              "本机服务已接收下载请求。下方状态中的文件数与校验结果决定是否完成。",
            );
            await reload(ctx);
          },
          { primary: true },
        ),
      );
    else
      box.append(
        el(
          "p",
          "在云页面可以保存清单与逐文件下载；需要自动逐文件校验和固定目录保存时，请在本机工作台打开同一清单。",
          "muted small",
        ),
      );
    box.append(actions);
    return box;
  }
  function downloadState(value) {
    const box = panel(
      "本机下载状态",
      "仅显示服务已报告的进展。没有百分比或预计完成时间的推算。",
    );
    box.append(
      fields([
        ["状态", show(value.status)],
        ["导出身份", value.export_id || "尚无下载"],
        [
          "已校验 / 总文件",
          `${count(value.verified_files)} / ${count(value.total_files)}`,
        ],
        [
          "已校验 / 总字节",
          `${bytes(value.verified_bytes)} / ${bytes(value.total_bytes)}`,
        ],
      ]),
    );
    if (value.error_code || value.error)
      box.append(
        el(
          "p",
          failure({ message: value.error_code || value.error }),
          "banner error",
        ),
      );
    if (["pending", "downloading"].includes(value.status))
      box.append(
        el("p", "下载仍由本机后台服务处理。关闭网页不表示下载完成。", "muted"),
      );
    if (value.status === "verified")
      box.append(
        el(
          "p",
          "所选文件已通过本机完整性校验。这不表示训练准入或研究质量已通过。",
          "banner good",
        ),
      );
    if (value.directory)
      box.append(fields([["服务管理的本机保存目录", value.directory]]));
    box.append(technical(value, "实际下载回执与观测"));
    return box;
  }
  function boundaryLabel(coverage) {
    const labels = {complete:"完整开局至终局", missing_start:"缺少开局", missing_end:"缺少终局", missing_both:"局中片段", ambiguous:"边界有歧义", out_of_order:"边界顺序异常", overlapping_exports_differ:"来源覆盖不同", unknown:"边界未知"};
    return coverage ? labels[coverage.boundary_status] || "未知" : "等待边界整理";
  }
  function continuityLabel(coverage) {
    return coverage?.recording_continuity === "complete" ? "无已知间断" : coverage?.recording_continuity === "gap" ? "有暂停或恢复间断" : "未确认";
  }
  async function gamesPage(ctx) {
    const data = await request(ctx, member("games"));
    const box = panel("对局与片段", `已整理 ${data.profiled_recordings ?? "未知"} / ${data.shared_recordings ?? "未知"} 份共享录制。待整理 ${data.pending_profiles ?? "未知"}，整理失败 ${data.failed_profiles ?? "未知"}。上传次数不等于独立局数；跨录制只按已证明身份归并。`);
    box.append(el("p", "完整局：原生开局到胜利或失败终局都有记录，且中途没有已知录制间断。胜负、技术失败与严格训练序列条件分别判断。Close 只封存录制，不是游戏终局。", "banner"));
    box.append(table(["局身份", "对局边界", "录制连续性", "游戏结果", "严格序列条件", "录入 / 接受", "来源数"], (data.items || []).map(r => [r.run_id, boundaryLabel(r.coverage), continuityLabel(r.coverage), r.outcome === "win" ? "胜利" : r.outcome === "loss" ? "失败" : r.outcome === "abandoned" ? "放弃" : "未知", r.complete ? "满足" : "未满足", `${r.canonical} / ${r.accepted}`, r.uploads.length])));
    for (const failed of data.failures || []) {
      box.append(el("p", `整理失败：${failed.error}`), command(ctx, `retry-profile-${failed.id}`, "重试此整理任务", async () => {
        await request(ctx, member(`datasets/${failed.id}/retry`), {}); await reload(ctx);
      }));
    }
    box.append(link("筛选并建立数据集", route("datasets")), technical(data));
    return box;
  }
  const datasetTab = () => drafts.get("dataset-tab") || "library";
  const splitLabel = value => value === "assigned" ? "已按局分组切分" :
    value === "purpose_assigned" ? "独立评估用途" :
    value === "insufficient_independent_run_components" ? "独立局数不足，尚未切分" : "查看切分条件";
  const datasetName = item => item.display_name || item.parameters?.name || `数据集 ${item.artifact_id.slice(0, 12)}`;
  function datasetTabs(ctx) {
    const nav = el("nav", null, "dataset-tabs");
    nav.setAttribute("aria-label", "数据集工作区");
    for (const [key, label] of [["library", "已生成的数据集"], ["create", "新建数据集"], ["previews", "预览与任务"], ["archived", "已移除"]]) {
      const button = el("button", label, `button${datasetTab() === key ? " primary" : ""}`);
      button.type = "button";
      button.dataset.action = `dataset-tab-${key}`;
      if (["previews", "archived"].includes(key)) button.className += " dataset-secondary";
      button.onclick = async () => {
        if (!live(ctx)) return;
        drafts.set("dataset-tab", key); drafts.delete("dataset-task-id"); await reload(ctx);
      };
      button.setAttribute("aria-current", datasetTab() === key ? "page" : "false");
      nav.append(button);
    }
    return nav;
  }
  async function decisionDatasets(ctx, mount) {
    const box = el("div", null, "project-page dataset-workspace");
    const tab = datasetTab();
    const nav = datasetTabs(ctx);
    box.append(nav, panel("正在读取…", "可以继续切换工作区；录制选择会保留。"));
    if (mount) mount(box);
    try {
      const content = tab === "create" ? await datasetCreate(ctx) :
        ["previews", "archived"].includes(tab) ? await datasetTasks(ctx) : await datasetLibrary(ctx);
      if (live(ctx) && tab === datasetTab()) {
        box.replaceChildren(nav, content);
        activeDataset = {ctx, tab, box, content};
      }
    } catch (error) {
      if (live(ctx) && tab === datasetTab()) box.replaceChildren(nav, panel("暂未读取到内容", failure(error)));
    }
    return box;
  }
  async function refreshDataset() {
    const page = activeDataset;
    if (!page || !live(page.ctx) || page.tab !== datasetTab()) return false;
    // A form is the user's draft. Polling never reconstructs its inputs or selection.
    if (page.tab === "create") return true;
    try {
      const next = page.tab === "library" ? await datasetLibrary(page.ctx, true) : await datasetTasks(page.ctx, true);
      if (!live(page.ctx) || activeDataset !== page || page.tab !== datasetTab()) return true;
      if (datasetSnapshots.get(next) === datasetSnapshots.get(page.content)) return true;
      const previous = new Map([...page.content.children].filter(n => n.dataset?.refreshKey)
        .map(n => [n.dataset.refreshKey, n]));
      const children = [...next.children].map(node => {
        const old = previous.get(node.dataset?.refreshKey);
        if (old && datasetSnapshots.get(old) === datasetSnapshots.get(node)) return old;
        if (old) {
          const opened = new Set([...old.querySelectorAll("details[open]")].map(n => n.dataset.preserve));
          node.querySelectorAll("details[data-preserve]").forEach(n => { n.open = opened.has(n.dataset.preserve); });
        }
        return node;
      });
      // Keep the mounted panel and unchanged task cards. No loading shell or empty frame.
      page.content.replaceChildren(...children);
      datasetSnapshots.set(page.content, datasetSnapshots.get(next));
    } catch (error) {
      if (!live(page.ctx) || activeDataset !== page) return true;
      if (error.message === "authentication_required") {
        page.box.replaceChildren(panel("需要重新登录", failure(error)));
        activeDataset = null;
      } else note(page.ctx, "状态暂未更新，保留上次结果。" + failure(error), "error");
    }
    return true;
  }
  async function datasetLibrary(ctx, fresh = false) {
    const box = panel("已生成的数据集", "这里是已固定版本、可查看和下载的数据集。预览在单独的工作区；生成数据集不等于已经通过训练或模型效果验收。");
    const filters = el("div", null, "project-form"); filters.dataset.projectEditor = "dataset-search";
    const search = input(filters, "搜索名称或数据集 ID", "dataset-search", drafts.get("dataset-search") || "");
    search.oninput = () => drafts.set("dataset-search", search.value);
    filters.append(command(ctx, "search-datasets", "搜索", async () => {
      drafts.set("dataset-search", search.value.trim()); offsets.set("dataset-catalog", 0); await reload(ctx);
    }));
    box.append(filters);
    const q = encodeURIComponent(drafts.get("dataset-search") || "");
    const catalog = await datasetRead(ctx, project(`datasets?limit=25&offset=${offsets.get("dataset-catalog") || 0}${q ? `&q=${q}` : ""}`), fresh);
    if (catalog.availability === "not_authorized") throw new Error("authentication_required");
    datasetSnapshots.set(box, JSON.stringify({...catalog, observed_at:undefined}));
    box.append(el("p", `共 ${count(catalog.total)} 个${q ? "匹配的" : "已生成的"}数据集`, "muted"));
    const mergeSet = new Set(drafts.get("merge-datasets") || []);
    const rows = [];
    for (const item of catalog.items || []) {
      if (!hex(item.artifact_id)) continue;
      const metadata = item.metadata || {};
      const title = el("div"); title.append(metadata.purpose === "gold" ? el("strong", datasetName(item)) : link(datasetName(item), route("datasets", item.artifact_id)), el("span", item.artifact_id.slice(0,12), "subtext mono"));
      const actions = el("div", null, "project-actions");
      actions.append(command(ctx, `download-dataset-${item.artifact_id}`, "下载", async () => {
        const downloadPage = location.search;
        if (metadata.materialization === "on_demand") {
          const job = await request(ctx, member("datasets/materialize"), {dataset_id:item.artifact_id});
          if (!live(ctx)) return;
          if (hex(job.id,32)) drafts.set("dataset-task-id", job.id);
          drafts.set("dataset-tab", "previews"); await reload(ctx); return;
        }
        if (!item.payloads?.length) throw new Error("dataset_payload_inventory_missing");
        const result = await request(ctx, member("exports"), {schema:"stpd/project-export-request-v1", collections:[], artifacts:[{artifact_id:item.artifact_id, roles:item.payloads.map(p => p.role)}]});
        if (!live(ctx) || location.search !== downloadPage) return;
        if (!hex(result.export_id)) throw new Error("invalid_export_identity");
        exportId = result.export_id;
        if (window.SpireProject.navigate) window.SpireProject.navigate("downloads", exportId);
      }, {disabled:metadata.purpose === "gold", title:metadata.purpose === "gold" ? "Gold 保持封存，内容只供受控评估" : "准备完整数据文件"}));
      const choice = el("div");
      const supported = ["stpd/decision-dataset-v1", "stpd/decision-union-v1", "stpd/curated-decision-dataset-v1"].includes(metadata.schema);
      const checkbox = input(choice, "合并", `merge-${item.artifact_id}`, mergeSet.has(item.artifact_id), "checkbox");
      checkbox.disabled = !supported;
      checkbox.title = supported ? "合并已选决策，保留原数据集" : "此数据契约不支持决策合并";
      checkbox.onchange = () => {
        if (checkbox.checked) mergeSet.add(item.artifact_id); else mergeSet.delete(item.artifact_id);
        drafts.set("merge-datasets", [...mergeSet].sort());
        const purposes = {...(drafts.get("merge-purposes") || {}), [item.artifact_id]:metadata.purpose || "training"};
        drafts.set("merge-purposes", purposes);
      };
      rows.push([title, count(metadata.records), {training:"训练",test:"测试",gold:"Gold · 已封存"}[metadata.purpose] || "历史数据集", splitLabel(metadata.split_status), when(item.indexed_at), actions, choice]);
    }
    if (rows.length) box.append(table(["名称 / 版本", "决策数", "用途", "分组", "生成时间", "操作", "合并选择"], rows));
    else box.append(empty(q ? "没有匹配的数据集" : "还没有已生成的数据集", q ? "更换关键词，或使用完整数据集 ID。" : "进入新建数据集，选择录制，预览后确认生成。"));
    box.append(pager(ctx, "dataset-catalog", catalog));
    const merge = el("details"); merge.dataset.preserve = "dataset-merge"; merge.append(el("summary", "合并已有数据集"));
    merge.append(el("p", "勾选列表中的两个或更多可靠决策数据集。只合并已选中的决策，去重后重新按真实局分组；原版本保留。", "muted"));
    merge.append(command(ctx, "preview-dataset-merge", "预览合并结果", async () => {
      if (mergeSet.size < 2) throw new Error("minimum_two_decision_datasets_required");
      const purposes = new Set([...mergeSet].map(id => drafts.get("merge-purposes")?.[id] || "training"));
      if (purposes.has("gold") && purposes.size !== 1) throw new Error("gold_merge_requires_only_gold");
      if (purposes.has("test") && purposes.size !== 1) throw new Error("test_merge_requires_only_test");
      const created = await request(ctx, member("datasets"), {name: "合并决策数据集", datasets: [...mergeSet].sort(),
        curation:{purpose:purposes.has("gold") ? "gold" : purposes.size === 1 && purposes.has("test") ? "test" : "training", paired_training:null},
        rules: {schema: "stpd/decision-selection-v1", complete_only: false, wins_only: false,
          no_failures_only: false, filters: {}, seed: 0}, preview_id: null});
      if (!live(ctx)) return;
      if (hex(created.id,32)) drafts.set("dataset-task-id",created.id);
      drafts.set("dataset-tab", "previews"); await reload(ctx);
    }));
    box.append(merge);
    return box;
  }
  async function datasetCreate(ctx) {
    const box = panel("1. 选择录制与筛选条件", "选择来源 → 查看预览 → 确认生成。默认保留可靠决策，包括失败局和不完整片段。");
    const form = el("div", null, "project-form");
    form.dataset.projectEditor = "decision-dataset";
    box.append(form);
    const draft = drafts.get("decision-dataset") || {name: "人类决策数据集", uploads: [], complete: false, wins: false, filters: {}};
    const name = input(form, "数据集名称", "dataset-name", draft.name);
    const purpose = select(form, "用途", "dataset-purpose", [["training","训练集"],["test","测试集"],["gold","Gold 封存集"]], draft.purpose || "training");
    const paired = select(form, "对应训练集（测试 / Gold 可选）", "paired-training", [["","稍后在使用前检查"]], draft.paired || "");
    if (draft.paired) { const old = el("option", `先前选择 · ${draft.paired.slice(0,12)}`); old.value = draft.paired; paired.append(old); paired.value = draft.paired; }
    const purposeHelp = el("p", "", "muted"); form.append(purposeHelp);
    function purposeChanged() {
      draft.purpose = purpose.value; draft.paired = paired.value.trim();
      paired.disabled = purpose.value === "training";
      purposeHelp.textContent = purpose.value === "gold" ? "封存后只能用于受控评估或与 Gold 合并。已下载或已被人看过的内容无法远程收回；历史使用记录会保留。" : purpose.value === "test" ? "与对应训练集按整局和重复来源检查重叠。未指定时，在使用前仍需检查训练来源。" : "保留训练与验证分组；独立测试集单独建立。";
      drafts.set("decision-dataset", draft);
    }
    purpose.onchange = purposeChanged; paired.oninput = purposeChanged; purposeChanged();
    const dates = el("div", null, "project-form");
    dates.dataset.projectEditor = "dataset-dates";
    const from = input(dates, "录制开始日期（本地时间）", "source-from", draft.sourceFrom || "", "date");
    const to = input(dates, "录制结束日期（包含当天）", "source-to", draft.sourceTo || "", "date");
    const saveDates = () => { draft.sourceFrom = from.value; draft.sourceTo = to.value; drafts.set("decision-dataset", draft); };
    from.oninput = saveDates; to.oninput = saveDates;
    from.onchange = saveDates; to.onchange = saveDates;
    function sourceQuery(limit, offset) {
      const query = new URLSearchParams({limit: String(limit), offset: String(offset), selectable: "true"});
      function day(value, exclusive) {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new Error("invalid_source_dates");
        const [y,m,d] = value.split("-").map(Number), date = new Date(y,m-1,d);
        if (date.getFullYear() !== y || date.getMonth() !== m-1 || date.getDate() !== d) throw new Error("invalid_source_dates");
        if (exclusive) date.setDate(date.getDate() + 1);
        return date.getTime() / 1000;
      }
      if (draft.sourceFrom) query.set("from", day(draft.sourceFrom, false));
      if (draft.sourceTo) query.set("to", day(draft.sourceTo, true));
      if (query.has("from") && query.has("to") && Number(query.get("from")) >= Number(query.get("to"))) throw new Error("invalid_source_dates");
      return query.toString();
    }
    dates.append(command(ctx, "filter-dataset-sources", "按日期筛选", async () => {
      saveDates(); sourceQuery(25,0); offsets.set("dataset-sources",0); await reload(ctx);
    }), command(ctx, "clear-dataset-dates", "不限日期", async () => {
      from.value = ""; to.value = ""; saveDates(); offsets.set("dataset-sources",0); await reload(ctx);
    }));
    box.append(el("h3", "选择录制来源"), dates);
    const advanced = el("details");
    advanced.append(el("summary", "筛选条件（默认保留所有合格决策）"));
    advanced.dataset.preserve = "decision-dataset-filters";
    const complete = input(advanced, "仅满足严格连续序列条件的完整局（包括失败局）", "complete-only", draft.complete, "checkbox");
    const noFailures = input(advanced, "排除有录制技术失败的局（与游戏失败无关）", "no-failures", draft.noFailures, "checkbox");
    const wins = input(advanced, "仅已确认胜利", "wins-only", draft.wins, "checkbox");
    const filterFields = {};
    for (const [key, title] of [["game_version", "游戏版本"], ["connector_version", "连接器版本"], ["annotator_version", "采集器版本"], ["character", "角色"], ["difficulty", "难度"], ["family", "动作类别"], ["surface", "界面"], ["decision_kind", "决策类型"], ["environment_identity", "精确环境身份"]]) {
      filterFields[key] = input(advanced, `${title}（留空不限，多个值用逗号分隔）`, key, (draft.filters[key] || []).join(","));
    }
    form.append(advanced);
    function save() {
      draft.name = name.value;
      draft.complete = complete.checked;
      draft.wins = wins.checked;
      draft.noFailures = noFailures.checked;
      draft.filters = {};
      for (const [key, field] of Object.entries(filterFields)) {
        const values = field.value.split(",").map(v => v.trim()).filter(Boolean);
        if (values.length) draft.filters[key] = key === "difficulty" ? values.map(Number) : values;
      }
      drafts.set("decision-dataset", draft);
    }
    for (const field of [name, complete, wins, noFailures, ...Object.values(filterFields)]) field.onchange = save;
    for (const field of [name, ...Object.values(filterFields)]) field.oninput = save;
    const savedPair = draft.paired;
    const [listing, trainingCatalog] = await Promise.all([
      datasetRead(ctx, project(`collections?${sourceQuery(25, offsets.get("dataset-sources") || 0)}`)),
      datasetRead(ctx, project("datasets?limit=100&offset=0")),
    ]);
    for (const item of trainingCatalog.items || []) {
      if (["test","gold"].includes(item.metadata?.purpose)) continue;
      const prior = [...paired.children].find(option => option.value === item.artifact_id);
      if (prior) prior.textContent = datasetName(item);
      else { const option = el("option", datasetName(item)); option.value = item.artifact_id; paired.append(option); }
    }
    if (savedPair && ![...paired.children].some(option => option.value === savedPair)) {
      const option = el("option", `先前选择 · ${savedPair.slice(0,12)}`); option.value = savedPair; paired.append(option);
    }
    paired.value = savedPair || ""; purposeChanged();
    const selection = new Set(draft.uploads);
    const sourceRows = [];
    const selectedCount = el("p", `已选择 ${selection.size} 份录制（跨日期和分页保留，最多 100 份）`, "project-selection");
    box.append(selectedCount);
    const sourceChoices = [];
    let selectionEpoch = 0;
    function selectionChanged() {
      selectionEpoch++;
      draft.uploads = [...selection].sort(); save();
      selectedCount.textContent = `已选择 ${selection.size} 份录制（跨日期和分页保留，最多 100 份）`;
      for (const [id, choice] of sourceChoices) choice.checked = selection.has(id);
    }
    box.append(command(ctx, "select-all-dataset-sources", "全选符合日期的录制", async () => {
      const query = sourceQuery(100, 0), epoch = selectionEpoch;
      const matching = await request(ctx, project(`collections?${query}`));
      if (!live(ctx) || query !== sourceQuery(100,0) || epoch !== selectionEpoch) return;
      const next = new Set([...selection, ...(matching.items || []).filter(item => item.status === "verified" && item.dataset_selectable !== false).map(item => item.upload_id || item.id)]);
      if (matching.total > 100 || matching.next_offset != null || next.size > 100) throw new Error("dataset_selection_limit");
      for (const id of next) if (hex(id,32)) selection.add(id);
      selectionChanged();
    }), command(ctx, "clear-dataset-selection", "清空全部选择", async () => {
      selection.clear(); selectionChanged();
    }));
    box.append(el("p", `当前日期范围共 ${count(listing.total)} 份可用录制；只读取列表，不会启动数据集任务。`, "muted"));
    const previewActions = el("div", null, "project-actions");
    box.append(previewActions);
    for (const item of listing.items || []) {
      const id = item.upload_id || item.id;
      if (!hex(id, 32)) continue;
      const choiceCell = el("div");
      const choice = input(choiceCell, "选择", `source-${id}`, selection.has(id), "checkbox");
      choice.disabled = item.status !== "verified" || item.dataset_selectable === false;
      choice.onchange = () => {
        if (choice.checked && selection.size >= 100 && !selection.has(id)) {
          choice.checked = false; note(ctx, failure({message:"dataset_selection_limit"}), "error"); return;
        }
        if (choice.checked) selection.add(id); else selection.delete(id);
        selectionChanged();
      };
      sourceChoices.push([id, choice]);
      sourceRows.push([choiceCell, when(item.summary?.created_at || item.created_at),
        count(item.summary?.counts?.canonical), count(item.summary?.assigned_run_count), show(item.status),
        link((item.summary?.session_id || id).slice(0, 28), route("collections", id))]);
    }
    if (sourceRows.length) box.append(table(["选择", "录制时间", "已录入决策", "局 / 片段数", "状态", "记录详情"], sourceRows));
    else box.append(empty("没有可选择的录制", "先完成录制 Close 和上传，再回到这里选择来源。"));
    box.append(pager(ctx, "dataset-sources", listing));
    previewActions.append(command(ctx, "preview-dataset", "下一步：预览选定记录", async () => {
      save();
      if (!draft.uploads.length) throw new Error("dataset_sources_required");
      const created = await request(ctx, member("datasets"), {name: draft.name, uploads: draft.uploads,
        curation:{purpose:draft.purpose || "training", paired_training:draft.purpose === "training" ? null : draft.paired || null},
        rules: {schema: "stpd/decision-selection-v1", complete_only: draft.complete,
          wins_only: draft.wins, no_failures_only: draft.noFailures, filters: draft.filters, seed: 0}, preview_id: null});
      if (!live(ctx)) return;
      if (hex(created.id,32)) drafts.set("dataset-task-id", created.id);
      drafts.set("dataset-tab", "previews");
      await reload(ctx);
    }, {primary: true}));
    return box;
  }

  async function datasetTasks(ctx, fresh = false) {
    const archived = datasetTab() === "archived";
    const key = archived ? "dataset-archived" : "dataset-tasks";
    const focused = !archived && drafts.get("dataset-task-id");
    const jobs = focused ? {items: [await datasetRead(ctx, member(`datasets/${focused}`), fresh)],total:1} :
      await datasetRead(ctx, member(`datasets${archived ? "/archived" : ""}?limit=25&offset=${offsets.get(key) || 0}`), fresh);
    const box = panel(archived ? "已移除的预览与任务" : "预览与任务", archived ? "可以恢复到任务列表。原始录制、固定数据集和失败记录始终保留。" : "预览成功后，确认生成才会进入数据集列表。处理中可离开页面，回来查看同一任务。");
    datasetSnapshots.set(box, JSON.stringify(jobs));
    if (focused) box.append(command(ctx, "show-all-dataset-tasks", "查看所有预览与任务", async () => {
      drafts.delete("dataset-task-id"); await reload(ctx);
    }));
    const disposable = (jobs.items || []).filter(j => j.state === "completed" && !j.request.preview_id);
    async function visibility(ids, value) {
      await request(ctx, member("datasets/visibility"), {ids, archived:value});
      if (!live(ctx)) return;
      drafts.delete("dataset-task-id"); await reload(ctx);
    }
    if (!archived && disposable.length) box.append(command(ctx, "remove-finished-previews", "移除本页已完成预览", async () => {
      await visibility(disposable.map(j => j.id), true);
    }));
    for (const job of jobs.items || []) {
      const row = panel(job.request.name, `${job.request.materialize ? "准备下载文件" : job.request.preview_id ? "生成数据集" : "预览"} · ${show(job.state)} · ${when(job.created_at)}`);
      row.dataset.refreshKey = job.id;
      datasetSnapshots.set(row, JSON.stringify(job));
      if (job.progress && ["pending", "running"].includes(job.state)) {
        const phases = {checking_access:"核对来源", preparing_sources:"分批准备来源", reading_sources:"读取来源", verifying_sources:"校验并整理", loading_selected_datasets:"读取已选数据", union_selected_decisions:"合并与去重", publishing_dataset:"保存固定版本", preparing_isolation:"建立隔离索引", preparing_download:"准备完整下载文件"};
        row.append(el("p", `${phases[job.progress.phase] || show(job.progress.phase)} · ${count(job.progress.completed)} / ${count(job.progress.total)} 个来源 · ${Number(job.progress.elapsed_seconds || 0).toFixed(1)} 秒`));
      }
      if (job.error) row.append(el("p", failure({message:job.error}), "banner error"));
      if (!archived && job.request.uploads) row.append(command(ctx, `edit-dataset-${job.id}`, "修改录制选择", async () => {
        const rules = job.request.rules || {};
        drafts.set("decision-dataset", {name:job.request.name, uploads:[...job.request.uploads],
          purpose:job.request.curation?.purpose || "training", paired:job.request.curation?.paired_training || "",
          complete:!!rules.complete_only, wins:!!rules.wins_only, noFailures:!!rules.no_failures_only, filters:rules.filters || {}});
        drafts.delete("dataset-task-id"); drafts.set("dataset-tab","create"); await reload(ctx);
      }));
      if (job.result) {
        row.append(fields([["保留决策", job.result.selected ?? job.result.records], ["训练分组", splitLabel(job.result.split_status)], ["去除重复", job.result.exact_duplicate_decisions ?? "见详情"]]));
        if (job.result.artifact_id && !job.request.materialize) row.append(link("打开已生成的数据集", route("datasets", job.result.artifact_id)));
        if (job.result.artifact_id && job.request.materialize) row.append(command(ctx, `download-materialized-${job.id}`, "下载完整数据文件", async () => {
          const result = await request(ctx, member("exports"), {schema:"stpd/project-export-request-v1", collections:[], artifacts:[{artifact_id:job.result.artifact_id, roles:["records","selection"]}]});
          if (!live(ctx)) return;
          exportId = result.export_id;
          if (window.SpireProject.navigate) window.SpireProject.navigate("downloads", exportId);
        }, {primary:true}));
        const report = el("details"); report.dataset.preserve = `dataset-report-${job.id}`; report.append(el("summary", "查看分类、对局和排除原因"));
        const facets = job.result.selected_facets || {};
        const categories = [];
        for (const key of ["character", "difficulty", "game_version", "family", "surface", "decision_kind"]) {
          const facet = facets[key]; if (!facet) continue;
          categories.push([labels[key] || key, facet.items.map(x => `${x.value} · ${count(x.count)}`).join("；") || "无已知值", count(facet.unknown)]);
        }
        if (categories.length) report.append(table(["分类", "内容与决策数", "未知"], categories));
        if (Array.isArray(job.result.runs)) report.append(table(["对局 / 片段", "对局边界", "录制连续性", "游戏结果", "严格序列条件", "录入 / 接受"], job.result.runs.map(r => {
          const coverage = (job.result.run_coverage || []).find(c => c.run_id === r.run_id);
          return [r.run_id, boundaryLabel(coverage), continuityLabel(coverage), r.outcome === "win" ? "胜利" : r.outcome === "loss" ? "失败" : r.outcome === "abandoned" ? "放弃" : "未知", r.complete ? "满足" : "未满足", `${r.canonical} / ${r.accepted}`];
        })));

        if (job.result.exclusion_counts) report.append(table(["排除原因", "数量"], Object.entries(job.result.exclusion_counts)));
        report.append(technical(job.result, "精确身份与完整报告")); row.append(report);
      }
      if (!archived && job.state === "completed" && !job.request.preview_id && !job.request.materialize) row.append(command(ctx, `build-${job.id}`, job.request.curation ? "确认生成数据集" : "按新规则重新预览", async () => {
        // Historical previews predate immutable purpose/quality rules. Preserve
        // them, and make a new preview instead of confirming a different identity.
        const created = await request(ctx, member("datasets"), {name:job.request.name, ...(job.request.datasets ? {datasets:job.request.datasets} : {uploads:job.request.uploads}), ...(job.request.curation ? {curation:job.request.curation} : {}), rules:job.request.rules, preview_id:job.request.curation ? job.id : null});
        if (!live(ctx)) return;
        if (hex(created.id,32)) drafts.set("dataset-task-id",created.id);
        await reload(ctx);
      }, {primary:true, disabled: !job.result?.selected}));
      if (!archived && ["failed", "cancelled"].includes(job.state)) row.append(command(ctx, `retry-dataset-${job.id}`, "重试此任务", async () => {
        const created = await request(ctx, member(`datasets/${job.id}/retry`), {});
        if (!live(ctx)) return;
        if (hex(created.id,32)) drafts.set("dataset-task-id",created.id);
        await reload(ctx);
      }));
      if (!archived && ["pending", "running"].includes(job.state)) row.append(command(ctx, `cancel-dataset-${job.id}`, "取消任务", async () => {
        await request(ctx, member(`datasets/${job.id}/cancel`), {});
        await reload(ctx);
      }, {disabled:job.progress?.phase === "publishing_dataset" && job.state === "running"}));
      if (["completed", "failed", "cancelled"].includes(job.state)) row.append(command(ctx, `visibility-${job.id}`, archived ? "恢复到任务列表" : job.request.preview_id ? "移除任务记录" : "移除预览", () => visibility([job.id], !archived)));
      box.append(row);
    }
    if (!jobs.items?.length) box.append(empty(archived ? "没有已移除的任务" : "没有待确认的预览或任务", "已生成的数据集在第一个页签中。"));
    box.append(pager(ctx, key, jobs));
    return box;
  }

  async function recordQuality(ctx) {
    const id = new URLSearchParams(location.search).get("id");
    const box = panel("操作标记", "标记误操作或从新数据集中排除。原始录制保留，已生成的数据集保持原版本；可随时撤销标记。");
    if (!hex(id,32)) return box;
    const page = await request(ctx, member(`collections/${id}/decisions?limit=25&offset=${offsets.get("quality") || 0}`));
    if (page.availability === "index_pending") { box.append(el("p", "正在后台整理操作列表。")); return box; }
    const rows = [];
    for (const item of page.items || []) {
      const history = item.annotations || [], latest = history[history.length - 1];
      const description = el("div"); description.append(el("strong", `${item.sequence} · ${item.action.kind}`), el("span", `${item.run} · ${show(item.family)}`, "subtext"), technical(item.action, "动作与目标"));
      const editor = el("div", null, "project-form"); editor.dataset.projectEditor = "quality";
      const reason = input(editor, "标记说明", `reason-${item.id}`, "");
      const choice = select(editor, "处理", `quality-${item.id}`, [["flag","仅标记问题"],["exclude","从新数据集排除"],["restore","撤销标记 / 恢复"]], "flag");
      editor.append(command(ctx, `annotate-${item.id}`, "保存标记", async () => {
        await request(ctx, member("quality-annotations"), {upload_id:id, occurrence:item.id, action:choice.value, reason:reason.value});
        await reload(ctx);
      }));
      rows.push([description, latest ? `${{flag:"已标记",exclude:"已排除",restore:"已恢复"}[latest.action]} · ${latest.reason}` : "无标记", editor]);
    }
    box.append(table(["已记录操作", "当前标记", "修改"], rows), pager(ctx, "quality", page));
    return box;
  }

  async function exportsPage(ctx) {
    const box = el("div", null, "project-page");
    const requested = new URLSearchParams(location.search).get("id");
    if (requested && !hex(requested))
      throw new Error("invalid_export_identity");
    const chosen = requested || exportId;
    if (requested) box.append(link("建立新的导出清单", route("downloads")));
    if (!requested) {
      const chooser = panel(
        "选择要下载的数据",
        "最多选择 100 份项目记录或产物。已验收录制可直接使用；产物仅包含你勾选的自身文件。",
      );
      const offset = offsets.get("export-collections") || 0;
      const listing = await request(
        ctx,
        project(`collections?limit=25&offset=${offset}`),
      );
      const rows = [];
      for (const item of listing.items || []) {
        const id = item.upload_id || item.id;
        if (!hex(id, 32)) continue;
        const available = ["verified", "quarantined"].includes(item.status);
        const checkbox = el("input");
        checkbox.type = "checkbox";
        checkbox.checked = selected.has(id);
        checkbox.disabled = !available;
        checkbox.name = `collection-${id}`;
        checkbox.setAttribute(
          "aria-label",
          `选择录制 ${item.summary?.session_id || id}`,
        );
        checkbox.onchange = () => {
          if (checkbox.checked) selected.add(id);
          else selected.delete(id);
          selectionText.textContent = selectionLabel();
        };
        const description = el("div");
        description.append(
          el("span", item.summary?.session_id || id, "row-title break"),
          el(
            "span",
            `${item.device_id || "电脑未知"} · ${show(item.status)}`,
            "subtext",
          ),
        );
        rows.push([
          checkbox,
          description,
          bytes(item.archive_bytes),
          available ? "生成清单时重新验证共享授权" : "文件尚未接收",
        ]);
      }
      chooser.dataset.projectEditor = "export";
      if (rows.length)
        chooser.append(table(["选择", "录制会话", "包大小", "下载条件"], rows));
      else
        chooser.append(
          empty(
            "当前没有可选择的录制",
            "记录器 Close 后的包被云端接收后会出现在这里。失败记录也可能具有维护价值。",
          ),
        );
      chooser.append(pager(ctx, "export-collections", listing));
      const artifactBox = panel(
          "加入数据集或研究产物",
          "先选择 manifest，再明确勾选数据文件。来源图不会递归下载。",
        ),
        artifactForm = el("div", null, "project-form");
      artifactForm.dataset.projectEditor = "export";
      const artifactDraft = drafts.get("artifact-picker") || {
        kind: "datasets",
        id: "",
      };
      const kind = select(
        artifactForm,
        "产物目录",
        "artifact-kind",
        [
          ["datasets", "数据集"],
          ["models", "模型与结果"],
          ["training", "训练产物"],
          ["evaluations", "评估"],
          ["analyses", "分析"],
        ],
        artifactDraft.kind,
      );
      const artifactInput = input(
        artifactForm,
        "精确产物 ID（可从目录复制）",
        "artifact-id",
        artifactDraft.id,
      );
      artifactInput.maxLength = 64;
      for (const control of [kind, artifactInput])
        control.oninput = () =>
          drafts.set("artifact-picker", {
            kind: kind.value,
            id: artifactInput.value,
          });
      artifactForm.append(
        command(ctx, "inspect-export-artifact", "查看可选文件", async () => {
          const id = artifactInput.value.trim();
          if (!hex(id)) throw new Error("invalid_artifact_identity");
          const response = await request(ctx, project(`${kind.value}/${id}`));
          if (!live(ctx)) return;
          const item = response.item;
          if (!item || item.artifact_id !== id)
            throw new Error("invalid_artifact_identity");
          drafts.set("inspected-artifact", item);
          await reload(ctx);
        }),
      );
      artifactBox.append(
        artifactForm,
        link("浏览数据集目录", route("datasets")),
        link("浏览研究记录", route("research")),
      );
      const inspected = drafts.get("inspected-artifact");
      if (inspected && hex(inspected.artifact_id)) {
        const item = panel(show(inspected.kind), inspected.artifact_id),
          selection = input(
            item,
            "包含该产物的 manifest",
            `artifact-${inspected.artifact_id}`,
            artifacts.has(inspected.artifact_id),
            "checkbox",
          );
        const payloadChoices = [];
        for (const payload of inspected.payloads || []) {
          if (typeof payload.role !== "string" || !hex(payload.sha256))
            continue;
          const choice = input(
            item,
            `${payload.role} · ${bytes(payload.size)}`,
            `role-${payload.role}`,
            (artifacts.get(inspected.artifact_id) || []).includes(payload.role),
            "checkbox",
          );
          choice.disabled = !selection.checked;
          payloadChoices.push([payload.role, choice]);
        }
        const changed = () => {
          for (const [, choice] of payloadChoices)
            choice.disabled = !selection.checked;
          if (selection.checked)
            artifacts.set(
              inspected.artifact_id,
              payloadChoices
                .filter(([, choice]) => choice.checked)
                .map(([role]) => role),
            );
          else artifacts.delete(inspected.artifact_id);
          selectionText.textContent = selectionLabel();
        };
        selection.onchange = changed;
        payloadChoices.forEach(([, choice]) => {
          choice.onchange = changed;
        });
        if (!Array.isArray(inspected.payloads))
          item.append(
            el(
              "p",
              "这条历史索引尚未提供文件角色，只能选择 manifest。管理员显式刷新索引后才可选择数据文件。",
              "banner",
            ),
          );
        artifactBox.append(item);
      }
      chooser.append(artifactBox);
      function selectionLabel() {
        return `已选择 ${selected.size} 份录制、${artifacts.size} 个产物。选择跨页面保留，切换账号会清空。`;
      }
      const selectionText = el("p", selectionLabel(), "project-selection");
      chooser.append(selectionText);
      if (artifacts.size)
        chooser.append(
          technical(
            [...artifacts].map(([artifact_id, roles]) => ({
              artifact_id,
              roles,
            })),
            "已选产物与文件角色",
          ),
        );
      const actions = el("div", null, "project-actions");
      actions.append(
        command(
          ctx,
          "create-export",
          "生成固定导出清单",
          async () => {
            if (
              (!selected.size && !artifacts.size) ||
              selected.size + artifacts.size > 100
            )
              throw new Error("select_between_1_and_100_items");
            const data = await request(ctx, member("exports"), {
              schema: "stpd/project-export-request-v1",
              collections: [...selected].sort(),
              artifacts: [...artifacts].map(([artifact_id, roles]) => ({
                artifact_id,
                roles,
              })),
            });
            if (!hex(data.export_id))
              throw new Error("invalid_export_identity");
            if (!live(ctx)) return;
            exportId = data.export_id;
            note(ctx, "固定清单已生成，文件尚未下载。请核对文件列表再下载。");
            await reload(ctx);
          },
          { primary: true },
        ),
        command(ctx, "clear-export", "清空选择", async () => {
          selected.clear();
          artifacts.clear();
          exportId = null;
          await reload(ctx);
        }),
      );
      chooser.append(actions);
      box.append(chooser);
    }
    if (chosen) {
      try {
        box.append(
          exportFacts(ctx, await request(ctx, member(`exports/${chosen}`))),
        );
      } catch (error) {
        box.append(empty("无法读取这份清单", failure(error)));
      }
    }
    if (local) {
      try {
        box.append(
          downloadState(await request(ctx, member("download-status"))),
        );
      } catch (error) {
        box.append(empty("本机下载状态暂不可用", failure(error)));
      }
    }
    return box;
  }
  const nativeLabels = {
    not_checked: "尚未检查游戏连接",
    game_not_running: "请启动游戏后刷新",
    configured: "目录已绑定，等待游戏连接",
    mismatch: "游戏或录制目录不匹配",
    bound: "游戏连接与录制目录已核对",
    blocked: "游戏连接需要处理",
  };
  const nextSteps = {
    register_collection_tool: "先按安装说明注册项目提供的采集工具，再刷新。",
    prepare_configuration: "确认授权后，准备这台电脑的录制配置。",
    prepare: "准备这台电脑的录制配置。",
    launch_game: "启动游戏，再刷新检查连接。",
    close_game: "先关闭游戏，再绑定录制目录。",
    bind_recording_root: "填写这台电脑的游戏目录，绑定本次录制目录。",
    review_runtime: "游戏身份或录制目录尚未通过检查，请查看连接详情。",
    review_setup: "本机设置需要处理，请查看设置详情中的检查结果。",
    activate_delivery: "游戏连接已核对，可以启用这台电脑的上传。",
    activate: "游戏连接已核对，可以启用这台电脑的上传。",
    none: "真人录制后按 Recorder Close，在采集记录中查看上传与云端收据。",
  };
  function preparationResult(prepared) {
    const result = panel("这台电脑的设置状态");
    const binding = prepared.native_binding || {};
    result.append(
      fields([
        ["本机配置", prepared.configuration_saved === true ? "已保存" : prepared.configuration_saved === false ? "尚未准备" : "状态未取得"],
        ["游戏连接", nativeLabels[binding.status] || "尚未取得连接检查"],
        ["投递配置", prepared.delivery_selected === true ? "当前正在使用" : prepared.delivery_selected === false ? "尚未启用" : "状态未取得"],
        ["后台上传", prepared.delivery_status === "running" ?
          prepared.delivery_selected === true ? "正在运行" : "后台运行中，未选择此配置" :
          prepared.delivery_status === "stopped" ? "已停止" : show(prepared.delivery_status)],
      ]),
      el("p", nextSteps[prepared.next_action] || "请核对本机配置与游戏连接后继续。", "banner"),
    );
    if (prepared.error || binding.reason)
      result.append(el("p", `检查结果：${prepared.error || binding.reason}`, "small muted"));
    result.append(technical(prepared, "设置与连接检查详情"));
    return result;
  }
  function prepareButton(ctx, enrollment) {
    return command(ctx, `prepare-${enrollment.enrollment_id}`, "准备本机录制配置", async () => {
      await request(ctx, member(`campaigns/${enrollment.enrollment_id}/prepare`), {});
      note(ctx, "本机准备已返回。正在重新读取已保存配置与游戏连接状态。");
      await reload(ctx);
    });
  }
  function preparationActions(ctx, enrollment, prepared) {
    const box = el("div", null, "project-actions");
    if (prepared.configuration_saved !== true) {
      if (prepared.configuration_saved === false) box.append(prepareButton(ctx, enrollment));
      else box.append(el("p", "尚未取得本机配置状态，请刷新后再继续。", "muted"));
      return box;
    }
    const binding = prepared.native_binding || {};
    if (binding.status !== "bound") {
      const form = el("div", null, "project-form collection-binding");
      form.dataset.projectEditor = "collection-binding";
      const name = `game-directory:${enrollment.enrollment_id}`;
      const directory = input(form, "这台电脑的游戏安装目录", "game_directory", drafts.get(name) || "");
      directory.placeholder = "选择游戏安装所在的完整路径";
      directory.oninput = () => drafts.set(name, directory.value);
      form.append(command(ctx, `bind-${enrollment.enrollment_id}`, "绑定本机录制目录", async () => {
        const path = directory.value.trim();
        if (!path || !(/^(?:\/|[A-Za-z]:[\\/]|\\\\)/.test(path)))
          throw new Error("absolute_game_directory_required");
        await request(ctx, member(`campaigns/${enrollment.enrollment_id}/bind`), { game_directory: path });
        note(ctx, "目录绑定已返回。请按检查结果启动游戏并刷新。");
        await reload(ctx);
      }, { disabled: binding.game_running === true, title: binding.game_running === true ? "先关闭游戏后绑定目录" : "" }));
      box.append(form);
    }
    box.append(command(ctx, `activate-${enrollment.enrollment_id}`, "启用本机上传", async () => {
      await request(ctx, member(`campaigns/${enrollment.enrollment_id}/activate`), {});
      note(ctx, "已请求启用本机上传。状态以重新读取的后台结果为准。");
      await reload(ctx);
    }, { primary: true, disabled: binding.status !== "bound" ||
      (prepared.delivery_selected === true && prepared.delivery_status === "running") }));
    return box;
  }
  function consentForm(ctx, record) {
    const form = el("div", null, "project-form");
    form.append(el("p", "采集游戏中可见的局面、你的选择及实际后继，上传到项目 Hub 的存储，供获授权的项目成员查看和研究。只涉及此后新录制；不会上传历史文件。", "project-consent"));
    form.append(command(ctx, `enroll-${record.template_id}`, "同意并开启采集", async () => {
      const result = await request(ctx, member("collection-flow/consent"), { template_id: record.template_id, accepted: true });
      if (result.schema !== "stpd/local-collection-flow-v1" || result.enrollment?.device_id !== ctx.identity.device_id ||
          result.enrollment?.template_id !== record.template_id) throw new Error("enrollment_identity_mismatch");
      note(ctx, "授权已保存，正在准备这台电脑。后续无需重复确认。");
      await request(ctx, member("collection-flow/prepare"), {});
      await reload(ctx);
    }, { primary: true }));
    return form;
  }
  function defaultAdministration(ctx, settings) {
    const box = panel("日常录制默认设置", "新成员使用此设置确认授权。保存后，已有授权与录制身份保留；采集用途或共享范围改变才需要重新确认；普通软件升级不会要求重新授权。");
    if (local) {
      const target = cloudLink("campaigns");
      if (target) box.append(link("打开云端默认设置 ↗", target));
      return box;
    }
    const form = el("div", null, "project-form collection-settings");
    form.dataset.projectEditor = "collection-settings";
    const previous = settings.default?.template || {}, draft = drafts.get("collection-settings") || previous;
    const name = input(form, "显示名称", "default_name", draft.name || "日常录制");
    const description = input(form, "录制说明", "default_description", draft.description || "记录日常真人游戏，供项目成员查看与研究。");
    const label = el("label", "授权说明", "form-field"), consent = el("textarea");
    consent.name = "default_consent_text"; consent.rows = 4; consent.value = draft.consent_text || "";
    label.append(consent); form.append(label);
    const read = () => ({ name: name.value.trim(), description: description.value.trim(), consent_text: consent.value.trim() });
    for (const control of [name, description, consent]) control.oninput = () => drafts.set("collection-settings", read());
    form.append(command(ctx, "save-default-collection", "发布日常默认设置", async () => {
      const value = read();
      if (!value.name || !value.description || !value.consent_text) throw new Error("default_collection_fields_required");
      await request(ctx, "/app/api/admin/collection-settings", value);
      if (!live(ctx)) return;
      drafts.delete("collection-settings"); note(ctx, "日常默认设置已保存。成员仍须亲自确认授权。");
      await reload(ctx);
    }, { primary: true }));
    box.append(form, el("p", "此设置只定义统一采集的用途和共享范围。角色、胜负与游戏版本在数据集内筛选。", "small muted"));
    return box;
  }
  const collectionSteps = {
    consent: "确认采集说明", prepare_collection: "准备这台电脑", prepare: "准备这台电脑",
    bind_recording_root: "绑定游戏目录", close_game: "请先退出游戏", restart_game: "请重新打开游戏",
    launch_game: "请打开游戏", stop_workbench: "请停止工作台以完成上传暂停", review_runtime: "请查看游戏连接异常",
    cold_launch: "请重新打开游戏", verify_loaded: "请重新打开游戏并启用 Mod",
    activate_collection: "准备上传", activate_delivery: "准备上传", resume_upload: "自动上传已暂停",
    review_upload_preference: "上传已停止，请检查本机设置",
    ready: "可以开始采集", none: "可以开始采集", reconnect: "请登录并连接这台电脑",
    contact_admin: "等待管理员设置采集说明",
  };
  async function collectionFlow(ctx, compact = false) {
    if (!local) {
      const box = panel("真人采集在游戏电脑进行", "云端可以查看已上传数据；不需要绑定电脑就能使用数据集和研究功能。");
      box.append(link("查看采集数据", route("collections")));
      if (ctx.identity.principal?.role === "admin") {
        const settings = await request(ctx, member("collection-settings"));
        const details = el("details"); details.dataset.preserve = "default-collection";
        details.append(el("summary", "采集说明设置"), defaultAdministration(ctx, settings)); box.append(details);
      }
      return box;
    }
    const status = await request(ctx, member("collection-flow"));
    if (status.schema !== "stpd/local-collection-flow-v1") throw new Error("unsupported_collection_flow_schema");
    const box = panel("真人采集", "在游戏里开始录制，本机保存原始记录，后台自动封包并上传。");
    if (status.device_id !== ctx.identity.device_id || (status.enrollment && status.enrollment.device_id !== ctx.identity.device_id)) throw new Error("collection_status_identity_mismatch");
    const enrollment = status.enrollment, preparation = enrollment?.preparation || {}, native = preparation.native_binding || {};
    const ready = native.bound === true && status.upload?.enabled === true && status.upload?.process === "running";
    box.append(badge(ready ? "已准备好" : collectionSteps[status.next_action] || "需要完成本机准备", ready ? "good" : "wait"));
    if (enrollment) {
      box.append(el("p", "授权已保存 · " + (enrollment.template?.name || "真人采集"), "small muted"));
      if (status.default && enrollment.template_id !== status.default.template_id)
        box.append(el("p", "继续使用当前已绑定设置；新的默认说明不会改写这份授权和已有记录。", "small muted"));
    }
    if (ready) box.append(el("p", "打开游戏 → 真人采集 → 开始录制。支持连续录制时，每局结束会自动保存并上传，退出游戏会保存未完部分；“结束录制”用于停止录制。"));
    if (status.error) box.append(el("p", failure({message: status.error}), "banner error"));
    if (status.consent_required && status.default) {
      box.append(el("p", status.default.template?.consent_text, "project-consent"));
      if (!compact) box.append(consentForm(ctx, status.default));
    } else if (enrollment && !compact) {
      if (!ready && status.next_action !== "resume_upload" && status.stage !== "unavailable" && preparation.status !== "unavailable") {
        const form = el("div", null, "project-form"); form.dataset.projectEditor = "collection-prepare";
        const needsDirectory = !native.game_directory && native.bound !== true;
        const directory = needsDirectory ? input(form, "游戏安装目录（未自动找到时填写）", "game_directory", drafts.get("game_directory") || "") : null;
        if (directory) directory.oninput = () => drafts.set("game_directory", directory.value);
        form.append(command(ctx, "prepare-collection", "准备 / 继续检查", async () => {
          const path = directory?.value.trim();
          if (path && !/^(?:\/|[A-Za-z]:[\\/])/.test(path)) throw new Error("absolute_game_directory_required");
          await request(ctx, member("collection-flow/prepare"), directory?.value.trim() ? {game_directory: directory.value.trim()} : {});
          await reload(ctx);
        }, {primary: true})); box.append(form);
      }
      const details = el("details"); details.dataset.preserve = "collection-details";
      details.append(el("summary", "授权、版本与准备详情"), el("p", enrollment.template?.consent_text || "授权说明保存在该电脑的登记记录中。"), preparationResult(preparation), technical(status)); box.append(details);
    } else if (!status.consent_required) box.append(link("登录并连接电脑", route("devices")));
    if (!compact && (enrollment || status.upload?.enabled === true || status.upload?.process === "running")) {
      const enabled = status.upload?.enabled === true || status.upload?.process === "running";
      box.append(command(ctx, "toggle-collection-upload", enabled ? "暂停自动上传" : "恢复自动上传", async () => {
        await request(ctx, member("collection-flow/upload"), {enabled: !enabled});
        note(ctx, enabled ? "已请求暂停。正在上传的文件以最终回执为准；本地文件保留。" : "正在检查并恢复上传。");
        await reload(ctx);
      }));
      box.append(el("p", enabled ? "自动上传已开启。关闭网页或退出个人登录不会暂停后台；暂停请使用上面的按钮。" : "自动上传已暂停，重新打开工作台也保持暂停。已有云端数据不会被删除。", "small muted"));
    }
    if (!signedIn(ctx)) box.append(el("p", "个人登录已退出或云端暂不可用。本机暂停上传仍可操作；登录后可恢复和查看项目数据。", "small muted"));
    if (compact) box.append(link("打开真人采集", route("campaigns")));
    else box.append(link("查看录制和上传进度", route("collections")));
    return box;
  }
  async function campaigns(ctx) {
    const box = el("div", null, "project-page");
    box.append(await collectionFlow(ctx));
    // Existing records and enrollment IDs remain readable; daily users do not
    // publish/select activities or create another collection configuration.
    return box;
  }
  function readinessPanel(value) {
    const reasons = {
      s1_requires_cuda_bf16_backend: "此模型需要支持 BF16 的 NVIDIA 显卡；当前电脑暂不支持。",
      install_locked_ml_and_l2_dependencies: "尚未安装模型运行依赖，请按该模型的安装说明准备。",
      backend_probe_unavailable: "暂时无法检查运行硬件，请查看诊断。",
      runtime_package_missing_or_drifted: "需要准备固定模型运行器；“准备并加载”会处理。",
      cuda_bf16_available: "显卡满足此模型要求。",
    };
    const box = panel(
      "加载前检查",
      "文件、代码和软件包检查与游戏实时兼容性是不同关卡。",
    );
    box.append(
      badge(
        show(value.status),
        value.status === "ready_to_load" ? "good" : "wait",
      ),
    );
    box.append(
      table(
        ["检查", "结果", "原因"],
        Object.entries(value.checks || {}).map(([key, item]) => [
          show(key),
          item.status === "pass" ? "通过" : show(item.status),
          reasons[item.code] || item.code || "—",
        ]),
      ),
    );
    box.append(
      el(
        "p",
        "游戏环境会在 Runtime 首次决策前核对；权重由适配器加载时检查。检查通过不代表模型已经加载或评估通过。",
        "small muted",
      ),
    );
    return box;
  }
  function localStatus(ctx, data) {
    const box = panel(
      "本机运行状态",
      "状态来自本机服务与其管理的唯一 Runtime。操作请求已接收与执行成功分开显示。",
    );
    const runtime = data.runtime,
      operation = data.operation;
    box.append(
      fields([
        ["本机服务", show(data.status)],
        ["模型加载", data.loaded === true ? "服务报告已加载" : "尚未确认加载"],
        ["Runtime 模式", runtime ? show(runtime.mode) : "尚无 Runtime 观测"],
        [
          "控制器",
          runtime?.controller === "held"
            ? "Runtime 持有"
            : runtime?.controller === "released"
              ? "已释放"
              : "未知",
        ],
        [
          "最近操作",
          operation
            ? `${show(operation.action)} · ${show(operation.status)}`
            : "无",
        ],
      ]),
    );
    if (data.error_code)
      box.append(
        el("p", failure({ message: data.error_code }), "banner error"),
      );
    if (data.observation_error)
      box.append(
        el(
          "p",
          "当前无法取得新的 Runtime 状态。以下保留的是此前观测，不能据此继续执行模型决策。",
          "banner error",
        ),
      );
    if (
      data.status === "command_unknown" ||
      data.status === "recovery_required" ||
      runtime?.tainted
    )
      box.append(
        el(
          "p",
          "结果未确认或 Runtime 已 tainted。禁止重复决策，请明确交还人类或停止并审查证据。",
          "banner error",
        ),
      );
    const runtimeFailure = runtime?.errors?.at(-1);
    if (runtimeFailure) {
      const explanation = runtimeFailure === "environment_modset_fingerprint_drift"
        ? "游戏环境与模型绑定不一致，模型尚未获准执行。请结束测试，核对游戏启动配置后重新加载；重复点击开始不会修复此问题。"
        : `模型运行已报告阻塞：${runtimeFailure}。请先查看原因，再恢复测试。`;
      box.append(el("p", explanation, "banner error"));
    }
    box.append(el("p", runtime?.last_receipt
      ? "已收到动作回执，具体送达结果见下方记录。"
      : "尚无游戏动作送达记录。模型已加载不代表正在操作游戏。", "small"));
    const actions = el("div", null, "project-actions");
    const recoverable = data.loaded === true || Boolean(data.previous_session) ||
      (operation?.status === "pending" && ["start", "prepare-and-load"].includes(operation.action));
    const changing = operation?.status === "pending";
    const safe =
      data.loaded === true &&
      runtime?.lifecycle === "running" &&
      !changing &&
      !data.observation_error &&
      !runtime?.tainted &&
      !runtimeFailure &&
      ![
        "command_unknown",
        "recovery_required",
        "runtime_exited",
        "stopped",
      ].includes(data.status);
    const advanced = el("details"); advanced.dataset.preserve = "model-advanced"; advanced.append(el("summary", "高级测试方式"));
    for (const [action, label] of [
      ["shadow", "只评分（不操作）"],
      ["one_step", "执行一个决策"],
      ["auto", "开始测试"],
      ["human", "暂停并接管"],
      ["stop", "结束测试"],
    ]) {
      const recovery = ["human", "stop"].includes(action);
      (["shadow", "one_step"].includes(action) ? advanced : actions).append(
        command(
          ctx,
          `model-command-${action}`,
          label,
          async () => {
            await request(ctx, "/api/local-models/command", { action });
            note(
              ctx,
              "本机服务已接收操作；请查看服务状态与 Runtime 回执。未知结果不会自动重发。",
            );
            await reload(ctx);
          },
          {
            disabled: recovery ? !recoverable : !safe,
            danger: action === "stop",
          },
        ),
      );
    }
    box.append(el("p", "开始测试会先结束真人录制，再由模型操作游戏。随时点“暂停并接管”；结束后生成独立实战记录。", "small muted"), actions, advanced);
    if (runtime)
      box.append(
        technical(
          {
            run_id: runtime.run_id,
            lifecycle: runtime.lifecycle,
            tainted: runtime.tainted,
            taint_reason: runtime.taint_reason,
            last_decision: runtime.last_decision,
            last_receipt: runtime.last_receipt,
            environment: runtime.environment,
          },
          "决策、回执与精确运行身份",
        ),
      );
    if (data.evaluation) box.append(evaluationPanel(data.evaluation));
    return box;
  }
  function evaluationPanel(value) {
    const box = panel(
      "本机已封装的评估记录",
      "这是有界 Runtime 操作与证据检查结果，不是游戏胜率或训练准入。",
    );
    box.append(
      fields([
        ["模型选择", value.selection_id],
        ["run ID", value.run_id],
        ["证据验证", value.evidence_verification || "未提供"],
        ["事件数", count(value.event_count)],
        [
          "游戏结果",
          value.game_outcome === "not_measured"
            ? "未测量"
            : value.game_outcome || "未知",
        ],
      ]),
      technical(value),
    );
    return box;
  }
  async function localModels(ctx) {
    const box = el("div", null, "project-page");
    if (!local) {
      box.append(
        empty(
          "请在游戏所在电脑打开工作台",
          "云页面不会远程启动或控制游戏。模型下载、加载和真实游戏评估由本机服务负责。",
        ),
      );
      return box;
    }
    let catalog, state;
    const replies = await Promise.allSettled([
      request(ctx, "/api/local-models"),
      request(ctx, "/api/local-models/status"),
    ]);
    if (replies[0].status === "fulfilled") catalog = replies[0].value;
    if (replies[1].status === "fulfilled") state = replies[1].value;
    if (state) box.append(localStatus(ctx, state));
    else
      box.append(
        empty(
          "本机 Runtime 状态暂不可用",
          "执行控件保持关闭。请刷新状态，不要重复之前的命令。",
        ),
      );
    const preparations = panel(
      "选择本机模型",
      "准备并加载会检查兼容性和固定运行环境。加载完成后由你开始测试，也可在游戏内操作。",
    );
    if (!catalog)
      preparations.append(
        empty("模型选择目录暂不可用", "不会根据任意下载文件或路径启动代码。"),
      );
    else if (!(catalog.policies || []).length)
      preparations.append(
        empty(
          "暂无审核过的模型选择",
          "普通训练模型与可在游戏内运行的适配器组合不是同一个东西。",
        ),
      );
    for (const item of catalog?.policies || []) {
      if (!selectionId(item.selection_id)) continue;
      const row = panel(
        item.label || item.selection_id,
      "模型、适配器、输入表示与环境契约作为一个审核过的选择。",
      );
      row.append(
        technical({
          selection_id: item.selection_id,
          artifact_sha256: item.artifact_sha256,
          support: item.support,
          claims: item.claims,
        }),
      );
      const report = readiness.get(item.selection_id);
      const actions = el("div", null, "project-actions");
      actions.append(
        command(
          ctx,
          `model-readiness-${item.selection_id}`,
          "检查本机加载条件",
          async () => {
            const result = await request(
              ctx,
              "/api/local-models/readiness?selection_id=" +
                encodeURIComponent(item.selection_id),
            );
            if (result.selection_id !== item.selection_id)
              throw new Error("model_identity_mismatch");
            if (!live(ctx)) return;
            readiness.set(item.selection_id, result);
            await reload(ctx);
          },
        ),
        command(
          ctx,
          `model-start-${item.selection_id}`,
          "准备并加载",
          async () => {
            await request(ctx, "/api/local-models/prepare", {
              selection_id: item.selection_id,
            });
            note(
              ctx,
              "本机服务已接收加载请求。模型尚需完成实际加载与身份核对。",
            );
            await reload(ctx);
          },
          {
            primary: true,
            disabled:
              !state ||
              state.loaded === true ||
              state.operation?.status === "pending" ||
              ["command_unknown", "recovery_required"].includes(state.status),
          },
        ),
      );
      const checks = el("details"); checks.dataset.preserve = `model-checks-${item.selection_id}`; checks.append(el("summary", "查看加载条件"), actions.firstChild);
      row.append(actions, checks);
      if (report) checks.append(readinessPanel(report));
      preparations.append(row);
    }
    box.append(preparations);
    const downloads = panel(
      "下载模型产物",
      "下载只保存并验证文件，不会自动安装适配器或激活模型。",
    );
    if (!signedIn(ctx))
      downloads.append(
        el("p", "登录项目账号后可查看模型目录。已有本机模型仍按本机权限管理。"),
      );
    else {
      try {
        const models = await request(ctx, project("models?limit=25&offset=0"));
        const entries = (models.items || []).filter(
          (item) => item.kind === "model" && hex(item.artifact_id),
        );
        if (!entries.length)
          downloads.append(
            empty(
              "当前页没有已发布模型",
              "训练作业完成后需要发布有效模型产物，才会进入目录。",
            ),
          );
        for (const item of entries)
          downloads.append(
            command(
              ctx,
              `model-download-${item.artifact_id}`,
              `下载模型 ${item.artifact_id.slice(0, 12)}… · ${bytes(item.payload_bytes)}`,
              async () => {
                await request(ctx, "/api/local-models/download", {
                  artifact_id: item.artifact_id,
                });
                note(ctx, "模型下载请求已接收。以本机下载回执为准，尚未加载。");
                await reload(ctx);
              },
              {
                disabled:
                  !state ||
                  state.operation?.status === "pending" ||
                  ["command_unknown", "recovery_required"].includes(
                    state.status,
                  ),
              },
            ),
          );
        if (models.total > 25)
          downloads.append(
            el(
              "p",
              "这里仅显示目录前 25 项；完整模型目录可查看其余身份。",
              "small muted",
            ),
          );
      } catch (error) {
        downloads.append(empty("云端模型目录暂不可用", failure(error)));
      }
      downloads.append(link("查看完整模型目录", route("models")));
    }
    for (const item of catalog?.downloaded_models || []) {
      const downloaded = panel("已下载的模型", item.artifact_id);
      downloaded.append(
        badge(item.local_download ? "存在本机下载记录" : "下载尚未确认"),
        el(
          "p",
          item.support_status === "unsupported"
            ? "当前没有匹配的游戏适配器与输入表示校验，不能直接在游戏中运行。"
            : show(item.support_status),
          "muted",
        ),
      );
      downloads.append(downloaded);
    }
    box.append(downloads);
    if (catalog?.evaluations?.length) {
      const evaluations = panel(
        "本机评估历史",
        "最多保留展示 100 条已校验的本地评估记录；不会自动上传。",
      );
      for (const value of catalog.evaluations)
        evaluations.append(evaluationPanel(value));
      evaluations.append(link("查看与分享实战记录", route("evaluations")));
      box.append(evaluations);
    }
    return box;
  }
  async function evaluationWithSharing(ctx, value) {
    const box = evaluationPanel(value);
    if (!signedIn(ctx) || !hex(value.evaluation_id)) return box;
    box.append(el("p", "分享会把这次模型测试的状态、动作和模型身份上传到项目，供已授权成员查看；不会作为真人采集数据。", "small muted"));
    box.append(command(ctx, `share-evaluation-${value.evaluation_id}`, "分享本次实战记录", async () => {
      await request(ctx, "/api/local-models/share", {evaluation_id: value.evaluation_id, authorized: true});
      note(ctx, "分享请求已接收。以项目回执为准；未知结果不会自动重发。");
      await reload(ctx);
    }, {disabled: value.evidence_verification !== "pass"}));
    return box;
  }
  async function evaluations(ctx) {
    const box = el("div", null, "project-page");
    if (local) {
      if (signedIn(ctx)) {
        const sharing = await request(ctx, "/api/local-models/share-status");
        if (sharing.status !== "idle") box.append(panel("实战记录分享", `${show(sharing.status)}${sharing.error ? " · " + failure({message: sharing.error}) : ""}`), technical(sharing));
      }
      const catalog = await request(ctx, "/api/local-models");
      const history = panel("本机实战记录", "结束测试后生成记录。是否完整、胜负和技术失败分别显示。");
      for (const value of catalog.evaluations || []) history.append(await evaluationWithSharing(ctx, value));
      if (!catalog.evaluations?.length) history.append(empty("还没有实战记录", "在模型实战中选择已支持模型，开始并结束一次测试。"));
      history.append(link("打开模型实战", route("local-models"))); box.append(history);
    }
    if (signedIn(ctx)) {
      const data = await request(ctx, project("models?limit=100&offset=0"));
      const records = (data.items || []).filter(item => ["live_evaluation", "offline_evaluation"].includes(item.kind));
      const shared = panel("项目评估结果", "离线拟合与真实游戏能力分开查看。这里只统计当前目录页，不把未测量结果计为成功。");
      for (const item of records) shared.append(link(`${item.kind === "live_evaluation" ? "游戏实战" : "离线评估"} · ${item.artifact_id.slice(0, 12)}`, route("models", item.artifact_id)));
      if (!records.length) shared.append(empty("当前页没有已发布评估", "未完成或未发布的评估不会显示为通过。"));
      box.append(shared);
    }
    return box;
  }
  return {
    reload: async () => {},
    refresh: async view => view === "datasets" ? refreshDataset() : false,
    openDatasetLibrary() {
      drafts.set("dataset-tab", "library");
      if (window.SpireProject.navigate) window.SpireProject.navigate("datasets");
    },
    datasetContext: () => `${datasetTab()}:${drafts.get("dataset-task-id") || ""}`,
    async render(view, identity, mount) {
      if (!supported.has(view)) throw new Error("unsupported_project_view");
      const nextAccount = `${identity?.principal?.subject || "anonymous"}:${identity?.principal?.role || ""}:${identity?.status || ""}`;
      if (account !== nextAccount) {
        account = nextAccount;
        activeDataset = null;
        offsets = new Map();
        drafts = new Map();
        selected = new Set();
        artifacts = new Map();
        exportId = null;
        readiness = new Map();
        datasetReads.clear(); datasetReadEpoch++;
      }
      const ctx = {
        account,
        key: `${account}:${view}:${location.search}`,
        identity,
        search: location.search,
        scope: scope(),
      };
      current = ctx;
      if (!(["local-models", "evaluations"].includes(view) || (local && ["campaigns", "collection-overview"].includes(view))) && !signedIn(ctx)) return authNotice(ctx);
      try {
        return await {
          members: admin,
          statistics,
          datasets: ctx => decisionDatasets(ctx, mount),
          games: gamesPage,
          downloads: exportsPage,
          research,
          evaluations,
          "local-models": localModels,
          campaigns,
          "collection-overview": (ctx) => collectionFlow(ctx, true),
          "record-quality": recordQuality,
        }[view](ctx);
      } catch (error) {
        const box = panel("当前页面暂不可用", failure(error));
        box.append(
          link("账号与电脑", route("devices")),
          command(ctx, "retry-read", "重新读取状态", () => reload(ctx)),
        );
        return box;
      }
    },
  };
})();
