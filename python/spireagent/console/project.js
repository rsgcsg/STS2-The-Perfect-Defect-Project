"use strict";

// Presentation only. Hub owns membership/data; the local service owns files and processes.
window.SpireProject = (() => {
  const local = document.body.dataset.mode === "local";
  const hex = (value, length = 64) =>
    typeof value === "string" &&
    new RegExp(`^[a-f0-9]{${length}}$`).test(value);
  const selectionId = (value) =>
    typeof value === "string" && /^[a-z0-9-]{1,80}$/.test(value);
  const runProfileIds = new Set(["short", "extended"]);
  function runProfiles(item) {
    if (!Array.isArray(item?.run_profiles)) return [];
    return item.run_profiles.filter(profile =>
      profile && runProfileIds.has(profile.id) &&
      typeof profile.label === "string" && profile.label.length <= 80 &&
      profile.label.length > 0 && !/[\/\\\x00-\x1f]/.test(profile.label) &&
      (profile.limits === null && profile.id === "short" ||
        profile.limits && typeof profile.limits === "object" &&
        ["max_submissions", "max_policy_calls", "deadline_ms"].every(key =>
          Number.isSafeInteger(profile.limits[key]) && profile.limits[key] > 0)));
  }
  function runProfileTitle(profile) {
    const limits = profile.limits;
    return limits ? `${profile.label} · ${count(limits.max_submissions)} 次提交 / ${count(limits.max_policy_calls)} 次评分 / ${count(Math.ceil(limits.deadline_ms / 1000))} 秒`
      : `${profile.label} · 限额以运行器状态为准`;
  }
  const supported = new Set([
    "members",
    "statistics",
    "downloads",
    "research",
    "local-models",
    "local-environment",
    "local-workspace",
    "campaigns",
    "collection-overview",
    "datasets",
    "games",
    "evaluations",
    "record-quality",
  ]);
  const supportedOfflineEvaluationSchemas = new Set([
    "stpd/offline-ranking-evaluation-v1",
    "stpd/stage1a-ranking-evaluation-v1",
    "stpd/experimental-m2-offline-evaluation-v1",
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
  const commandControls = new Map();
  let activeDataset = null;
  let localRecordingSnapshot = null;
  let localMemberArchiveSnapshot = null;
  const datasetSnapshots = new WeakMap();
  const datasetReads = new Map();
  let datasetReadEpoch = 0;
  let modelWatch = null;
  let environmentSnapshot = null;
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
    feature_job: "特征任务",
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
    run_event: "运行事件",
    analysis: "分析",
    gold_tasks: "Gold 任务",
    gold_labels: "Gold 标注",
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
      download_checksum_mismatch: "本机归档与已保存清单的大小或校验值不符，未导入。请刷新已下载归档后再明确重试。",
      download_file_unavailable: "本机归档文件当前不可用，未导入。请确认下载完整后刷新归档目录。",
      download_inventory_unavailable: "本机下载清单无法读取，未导入。请重新检查已下载归档状态。",
      download_receipt_unavailable: "没有可用的完整下载回执，未导入。请确认成员归档已经下载并通过校验。",
      invalid_export_inventory: "本机导出清单未通过身份校验，未导入。请刷新归档目录并核对来源。",
      collection_archive_not_selected: "所选文件不是该导出中明确选取的 collection archive，未导入。",
      member_download_unavailable: "本机成员下载服务不可用，未导入。请重新读取本机状态。",
      workspace_required: "请先建立或选择本机资料空间，再导入成员归档。",
      curation_owner_recovery_required: "本机来源用途状态需要先恢复，归档尚未导入。",
      previous_import_interrupted: "工作台上次关闭时导入仍在处理中，结果未知。先刷新并核对状态；不会自动重试。",
      local_model_export_failed: "本机模型导出与校验未完成。请刷新状态后再按需明确重试。",
      local_model_registration_invalid: "本机模型登记状态格式未知；未发起模型操作。请刷新状态。",
      local_models_extra_required: "本机模型计算依赖尚未按发行包准备；请先用已验证的开发者工具包初始化模型环境。未启动训练或登记。",
      text_runtime_profile_required: "本机文本菜单运行环境尚未准备；请先完成本机运行环境设置。",
      text_runtime_local_install_required: "本机文本菜单运行组件尚未准备；请检查运行环境状态。",
      trusted_text_runtime_kit_unavailable: "当前工作台没有已选定且可验证的开发者工具包；请先使用已批准的发行包准备本机环境。",
      trusted_text_runtime_kit_invalid: "当前开发者工具包校验失败；请检查发行包和源码状态，不能使用本地散文件替代。",
      trusted_text_runtime_kit_changed: "开发者工具包文件在校验后发生变化；未安装运行组件，请核对发行包后明确重试。",
      trusted_text_runtime_asset_not_bundled: "这份已验证发行包没有附带该文本运行组件；当前没有可用的固定下载资产。",
      private_profile_collision: "现有本机 Runtime pin 与发行包不同；不会自动改绑，请核对后处理。",
      text_menu_capabilities_unavailable: "暂时无法核对当前游戏的文本菜单能力。请打开游戏后刷新，再明确重试。",
      managed_requires_confirmed_interaction_model: "独立游戏环境需要支持已确认操作历史的 v2 模型；原模型和权重保持不变。",
      managed_contract_unavailable: "独立游戏环境尚未提供兼容的文字接口；请先核对环境状态。",
      managed_environment_unavailable: "所选独立游戏环境不可用；请到环境与场景核对，不会自动新开一局。",
      text_menu_capabilities_incompatible: "当前游戏环境不符合此模型的文本菜单要求；尚未登记。",
      registration_metadata_invalid: "本机模型登记资料无法安全确认；请检查恢复状态。",
      verified_export_required: "此模型当前没有可用的已校验导出；请先完成导出校验。",
      verified_export_receipt_required: "这份较早的记忆模型导出缺少校验回执；请点击“重新核验导出”，完成后再明确登记。",
      registration_timeout: "本次登记校验已超时；请先刷新状态核对结果，再按需明确重试。不会自动加载模型。",
      workspace_changed: "导出来自其他资料空间；请切回原资料空间再登记。",
      source_binding_changed: "先前登记绑定的运行源码已变化；旧选择保留。可明确重新登记并生成新选择，不会改写旧登记。",
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
  async function request(ctx, path, body, localCsrfToken) {
    if (!live(ctx)) throw new Error("context_changed");
    const mutation = body !== undefined;
    if (mutation && modelWatch?.ctx === ctx) stopModelWatch();
    const csrfToken = local && typeof localCsrfToken === "string" && localCsrfToken
      ? localCsrfToken
      : ctx.identity?.csrf_token || "";
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
              "X-CSRF-Token": csrfToken,
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
    const controlKey = key;
    const disabledState = () => Boolean(options.disabled);
    button.disabled = disabledState() || pending.has(key);
    if (current === ctx) {
      let controls = commandControls.get(controlKey);
      if (!controls || controls.ctx !== ctx) controls = {ctx, buttons: []};
      controls.buttons.push({button, disabled:disabledState});
      commandControls.set(controlKey, controls);
    }
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
        button.disabled = disabledState();
        const controls = commandControls.get(controlKey);
        if (controls && live(controls.ctx)) {
          for (const control of controls.buttons)
            control.button.disabled = control.disabled() || pending.has(key);
        }
      }
    };
    return button;
  }
  async function reload(ctx) {
    if (live(ctx)) await window.SpireProject.reload();
  }
  function stopModelWatch() {
    if (modelWatch?.timer !== null && modelWatch?.timer !== undefined)
      clearTimeout(modelWatch.timer);
    modelWatch = null;
  }
  function modelWatchKey(ctx) {
    return `${ctx.key}:${ctx.scope}`;
  }
  function modelWatchSignature(state) {
    const runtime = state.runtime || {};
    const budget = runtime.autonomy_budget || {};
    return JSON.stringify({status:state.status, loaded:state.loaded,
      operation:state.operation && {id:state.operation.id, action:state.operation.action,
        status:state.operation.status},
      run_id:runtime.run_id, lifecycle:runtime.lifecycle, mode:runtime.mode,
      controller:runtime.controller, tainted:runtime.tainted,
      error_code:state.error_code, observation_error:state.observation_error,
      runtime_error:runtime.errors?.at(-1), budget_state:budget.state,
      budget_exhausted:budget.exhausted_reason, budget_ended:budget.ended_reason,
      evaluation_id:state.evaluation?.evaluation_id});
  }
  const modelWatchNeeded = (state) => state?.operation?.status === "pending" ||
    (state?.loaded === true && state.runtime?.lifecycle === "running");
  function scheduleModelWatch(watch) {
    if (modelWatch !== watch || !live(watch.ctx)) return;
    if (watch.remaining === 0 || Date.now() >= watch.deadlineAt) {
      note(watch.ctx, "自动状态检查已暂停。需要时请点击“刷新运行状态”。", "warning");
      return;
    }
    watch.timer = setTimeout(() => pollModelWatch(watch), 3000);
  }
  async function pollModelWatch(watch) {
    watch.timer = null;
    const ctx = watch.ctx;
    if (modelWatch !== watch || !live(ctx)) return;
    if (watch.remaining === 0 || Date.now() >= watch.deadlineAt) {
      scheduleModelWatch(watch);
      return;
    }
    watch.remaining--;
    let next;
    try {
      next = await request(ctx, "/api/local-models/status");
      if (!next || typeof next !== "object") throw new Error("status_unavailable");
    } catch (error) {
      if (modelWatch !== watch || watch.ctx !== ctx || !live(ctx)) return;
      watch.failures++;
      if (watch.failures >= 3) {
        stopModelWatch();
        note(ctx, "自动状态检查暂不可用。请点击“刷新运行状态”重试；不会重新发送模型命令。", "warning");
      } else scheduleModelWatch(watch);
      return;
    }
    if (modelWatch !== watch || watch.ctx !== ctx || !live(ctx)) return;
    watch.failures = 0;
    if (modelWatchSignature(next) !== watch.signature) {
      watch.signature = modelWatchSignature(next);
      await reload(ctx);
      if (modelWatch === watch && watch.ctx === ctx) scheduleModelWatch(watch);
      return;
    }
    watch.budgetHost?.replaceChildren(localAutonomyBudget(next.runtime));
    scheduleModelWatch(watch);
  }
  function watchLocalModel(ctx, state, budgetHost) {
    if (!modelWatchNeeded(state)) {
      stopModelWatch();
      return;
    }
    const key = modelWatchKey(ctx);
    const watch = modelWatch?.key === key ? modelWatch :
      {key, remaining:100, deadlineAt:Date.now() + 5 * 60 * 1000,
        failures:0, timer:null};
    if (modelWatch && modelWatch !== watch) stopModelWatch();
    if (watch.timer !== null) clearTimeout(watch.timer);
    watch.ctx = ctx;
    watch.signature = modelWatchSignature(state);
    watch.budgetHost = budgetHost;
    watch.timer = null;
    modelWatch = watch;
    scheduleModelWatch(watch);
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
  function localDataFacts(value, artifact) {
    if (value?.schema !== "stpd/local-data-facts-v1") return null;
    const recording = value.recording || {};
    const dataset = value.dataset || {};
    const ledger = value.ledger || {};
    const allocationRoles = value.purpose_and_allocation?.allocation_roles || [];
    const countValue = fact => fact?.known === true && Number.isSafeInteger(fact.value)
      ? count(fact.value) : "未知";
    const roleSummary = allocationRoles.length
      ? allocationRoles.map(role => `训练 ${count(role.train_count)} / 开发 ${count(role.dev_count)}`).join("；")
      : "未记录固定分配";
    const schemaVersions = Array.isArray(dataset.schema_versions) ? dataset.schema_versions : [];
    const sourceVersion = schemaVersions.find(item => item.role === "source");
    const curatedVersion = schemaVersions.find(item => item.role === "curated");
    const descendants = value.descendants || {};
    const runResults = Array.isArray(descendants.run_results) ? descendants.run_results : [];
    const card = panel("资料来源与使用概况",
      "显示来源、样本数量和已知使用记录。");
    card.append(fields([
      ["样本数（已选）", dataset.samples?.selected == null ? "未知" : count(dataset.samples.selected)],
      ["排除数", dataset.samples?.excluded_known ? count(dataset.samples.excluded) : "未知"],
      ["来源包数 / 录制会话数 / 已登记运行实例数",
        `${count(recording.bundle_count)} / ${count(recording.session_count)} / ${count(recording.qualified_run_occurrence_count)}`],
      ["原生开局 / 终局", `${countValue(recording.native_starts)} / ${countValue(recording.native_ends)}`],
      ["是否来自不同实体对局", "未知，尚未证明独立"],
      ["用途 / 固定分配", `${dataset.purpose || "未知用途"} · ${roleSummary}`],
      ["原始 / 整理数据版本", `${sourceVersion?.schema || "未知"} → ${curatedVersion?.schema || dataset.schema || "未知"}`],
      ["生产源码版本", `${sourceVersion?.producer_source_revision?.slice(0, 12) || "未知"} → ${curatedVersion?.producer_source_revision?.slice(0, 12) || dataset.producer_source_revision?.slice(0, 12) || "未知"}`],
      ["用户授权记录", value.user_declaration?.status === "registered" ? "已持久记录" : "尚未持久登记"],
      ["已知模型 / 评测后代", `${count(descendants.models?.length)} / ${count(descendants.evaluations?.length)}`],
      ["生产者记录的完成训练", count(runResults.filter(item => item.state === "completed").length)],
      ["用途账本", ledger.label || "历史使用记录不完整"],
      ["来源索引", ledger.source_index_status === "complete" ? "已核对" : "不完整或尚未核对"],
      ["当前清单索引", artifact?.registry_indexed === false ? "未对齐当前清单" : "已核对"],
      ["来源关系扫描", value.descendants?.truncated || value.lineage?.truncated
        ? "已截断，结果不完整" : "未触及扫描上限"],
      ["人工查看或调参历史未知", "未知"],
      ["适用范围", "训练前须核对用途与授权；开发评测不代表独立测试；此页不授予 Gold 资格"],
    ]));
    const related = el("div", null, "project-actions");
    for (const item of [...(descendants.models || []), ...(descendants.evaluations || [])]) {
      if (!hex(item?.artifact_id)) continue;
      const kind = item.kind === "model" || (descendants.models || []).includes(item) ? "模型" : "评测";
      related.append(link(`打开${kind} · ${item.artifact_id.slice(0, 12)} · ${item.producer_completion_status || "完成状态未知"}`,
        route("local-workspace", item.artifact_id)));
    }
    if ((descendants.models || []).length || (descendants.evaluations || []).length)
      card.append(related);
    card.append(technical({
      数据集: dataset,
      来源与运行片段: value.lineage,
      固定分配: allocationRoles,
      已记录使用: ledger.uses || [],
      已知模型: value.descendants?.models || [],
      已知评测: value.descendants?.evaluations || [],
      训练运行结果: value.descendants?.run_results || [],
      不确定项: {
        historical_manual_exposure: ledger.historical_manual_exposure || "unknown",
        physical_game_independence: recording.physical_game_independence || "unresolved",
        ledger_coverage: ledger.coverage || "incomplete",
      },
    }, "查看来源、历史使用引用与关联对象"));
    return card;
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
    const safeValue = Object.fromEntries(
      Object.entries(value).filter(([key]) => key !== "directory"),
    );
    const box = panel(
      "本机下载状态",
      "仅显示服务已报告的进展。没有百分比或预计完成时间的推算。",
    );
    box.append(
      fields([
        ["状态", show(safeValue.status)],
        ["导出身份", safeValue.export_id || "尚无下载"],
        [
          "已校验 / 总文件",
          `${count(safeValue.verified_files)} / ${count(safeValue.total_files)}`,
        ],
        [
          "已校验 / 总字节",
          `${bytes(safeValue.verified_bytes)} / ${bytes(safeValue.total_bytes)}`,
        ],
      ]),
    );
    if (safeValue.error_code || safeValue.error)
      box.append(
        el(
          "p",
          failure({ message: safeValue.error_code || safeValue.error }),
          "banner error",
        ),
      );
    if (["pending", "downloading"].includes(safeValue.status))
      box.append(
        el("p", "下载仍由本机后台服务处理。关闭网页不表示下载完成。", "muted"),
      );
    if (safeValue.status === "verified")
      box.append(
        el(
          "p",
          "所选文件已通过本机完整性校验。这不表示训练准入或研究质量已通过。",
          "banner good",
        ),
      );
    if (local && safeValue.status === "verified")
      box.append(link("在本机资料中导入已下载的成员归档", route("local-workspace")));
    box.append(technical(safeValue, "实际下载回执与观测"));
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
  const splitLabel = value => value === "assigned" ? "已分配训练/开发样本" :
    value === "purpose_assigned" ? "已按所选评测用途分配" :
    value === "insufficient_independent_run_components" ? "独立对局不足，尚不能形成独立划分" : "划分状态未知";
  const localDatasetBlockerLabel = code => ({
    legacy_gold_history_unknown: "相关旧资料的历史用途无法完整核实，不能作为 Gold；仍可按训练或测试用途重新检查。",
    gold_source_inventory_pending: "还有来源未完成索引，目前不能确认 Gold 隔离。",
    gold_already_in_other_dataset: "该来源已进入其他数据集；请为 Gold 选择独立来源。",
    gold_requires_gold_merge: "该来源已属于 Gold，不能作为新的 Gold 重复创建；本机暂不支持 Gold 合并。",
    gold_previously_used_for_training: "该来源已有训练使用记录，不能作为 Gold。",
    gold_reserved_data: "该来源已保留为 Gold，只能用于受控评估或 Gold 合并。",
    independent_groups_required: "当前划分无法形成训练和开发两组。数据可以保留；需要补充不同输入，或使用后续支持的划分方式。",
    empty_selection: "当前选择没有可保留的样本。",
  })[code];
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
    const box = panel("真人采集", "录制由你在游戏里开始和结束；本机保存与后台投递按下方实际状态显示。");
    if (status.device_id !== ctx.identity.device_id || (status.enrollment && status.enrollment.device_id !== ctx.identity.device_id)) throw new Error("collection_status_identity_mismatch");
    const enrollment = status.enrollment, preparation = enrollment?.preparation || {}, native = preparation.native_binding || {};
    const ready = native.bound === true && status.upload?.enabled === true && status.upload?.process === "running";
    if (status.upload?.enabled !== true)
      box.append(el("p", "本机后台上传未启用；录制是否可开始及本机保存状态，请按游戏内 Recorder 和下方准备状态核对。", "small muted"));
    else if (status.upload?.process === "not_configured")
      box.append(el("p", "上传设置已开启，但本机投递服务未配置运行；此状态不表示数据会自动上传。", "small muted"));
    else if (status.upload?.process !== "running")
      box.append(el("p", "上传设置已开启，但本机投递服务当前未运行；此状态不表示数据会自动上传。", "small muted"));
    else
      box.append(el("p", "本机后台投递已启用且当前运行；录制关闭后的处理结果以记录页显示的回执为准。", "small muted"));
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
      text_runtime_local_install_required: "文字模型需要先安装已核对的本地候选运行包；请按模型安装说明完成，现有旧运行器不会被替换。",
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
  function localStatus(ctx, data, onBudgetHost, catalog) {
    const box = panel(
      "本机运行状态",
      "状态来自本机服务与其管理的唯一 Runtime。操作请求已接收与执行成功分开显示。",
    );
    const runtime = data.runtime,
      operation = data.operation;
    const selectedPolicy = Array.isArray(catalog?.policies) ?
      catalog.policies.find(item => item.selection_id === data.selection_id) : null;
    const selectedProfile = runProfiles(selectedPolicy).find(
      profile => profile.id === data.run_profile);
    const profileStatus = data.run_profile === undefined
      ? (data.loaded || operation?.status === "pending" ? "旧会话未记录或尚未确认" : "尚未选择")
      : selectedProfile ? runProfileTitle(selectedProfile) : "运行配置未能核对";
    box.append(
      fields([
        ["本机服务", show(data.status)],
        ["模型加载", data.loaded === true ? "服务报告已加载" : "尚未确认加载"],
        ["运行配置", profileStatus],
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
    const budgetHost = el("div");
    budgetHost.append(localAutonomyBudget(runtime));
    box.append(budgetHost);
    onBudgetHost(budgetHost);
    const currentFailure = data.error_code;
    if (currentFailure) {
      const explanation = currentFailure === "environment_modset_fingerprint_drift"
        ? "上次本机操作曾发现游戏环境与模型绑定不一致"
        : failure({ message: currentFailure });
      box.append(
        el(
          "p",
          `上次本机操作诊断（历史记录）：${explanation}。它不单独决定当前操作资格；显式新操作仍会重新进行身份、epoch 和环境检查，不会自动重发或自动重绑。`,
          "banner warning",
        ),
      );
    }
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
        ? "最近一次 Runtime 环境检查曾拒绝当时的环境（历史诊断）：游戏环境与模型绑定不一致。它不单独决定当前操作资格；你明确点击开始后，Runtime 会重新核对当前环境，仍不兼容会安全返回人工模式。"
        : `最近一次 Runtime 诊断（历史记录）：${runtimeFailure}。它不单独决定当前操作资格；需要恢复时请明确点击操作，不会自动重发。`;
      box.append(el("p", explanation, "banner warning"));
    }
    box.append(el("p", runtime?.last_receipt
      ? "已收到动作回执，具体送达结果见下方记录。"
      : "尚无游戏动作送达记录。模型已加载不代表正在操作游戏。", "small"));
    const actions = el("div", null, "project-actions");
    actions.append(command(ctx, "model-refresh-status", "刷新运行状态", async () => {
      stopModelWatch();
      await reload(ctx);
    }));
    const recoverable = data.loaded === true || Boolean(data.previous_session) ||
      (operation?.status === "pending" && ["start", "prepare-and-load"].includes(operation.action));
    const changing = operation?.status === "pending";
    const safe =
      data.loaded === true &&
      runtime?.lifecycle === "running" &&
      !changing &&
      !data.observation_error &&
      !runtime?.tainted &&
      runtime?.mode === "human" &&
      runtime?.controller === "released" &&
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
  function localAutonomyBudget(runtime) {
    const budget = runtime?.autonomy_budget;
    const box = panel(
      "本次自主操作预算",
      "预算只约束当前自主授权，不是对局计时或结果。",
    );
    if (budget === undefined || budget === null) {
      box.append(el("p", "此 Runtime 未提供预算信息。", "small muted"));
      return box;
    }
    const states = new Set(["active", "exhausted", "inactive"]);
    const exhaustion = {
      submission_attempt_limit: "自主提交次数已到限额",
      policy_call_limit: "模型评分次数已到限额",
      deadline: "自主运行时间已到限额",
    };
    const ended = {
      human_recovery: "因人工接管结束",
      mode_changed: "因运行模式变更结束",
      stopped: "随 Runtime 停止结束",
    };
    const counters = [
      "max_submissions", "submissions_used", "max_policy_calls",
      "policy_calls_used", "deadline_ms", "elapsed_ms", "remaining_ms",
    ];
    const autonomousModes = new Set(["auto", "shadow", "one_step"]);
    const knownModes = new Set(["human", ...autonomousModes]);
    const runtimeConsistent = runtime &&
      ["running", "stopped"].includes(runtime.lifecycle) &&
      knownModes.has(runtime.mode) &&
      (budget.state === "active"
        ? runtime.lifecycle === "running" && autonomousModes.has(runtime.mode) && runtime.tainted === false
        : budget.state === "exhausted"
          ? runtime.mode === "human"
          : runtime.mode === "human");
    const validCounters = budget && typeof budget === "object" &&
      counters.every((key) => Number.isSafeInteger(budget[key]) && budget[key] >= 0) &&
      budget.max_submissions > 0 && budget.max_policy_calls > 0 && budget.deadline_ms > 0 &&
      budget.submissions_used <= budget.max_submissions &&
      budget.policy_calls_used <= budget.max_policy_calls &&
      budget.elapsed_ms <= budget.deadline_ms && budget.remaining_ms <= budget.deadline_ms;
    const stateKnown = budget && states.has(budget.state);
    const reasonKnown = budget.state === "exhausted"
      ? Object.hasOwn(exhaustion, budget.exhausted_reason) && budget.ended_reason === null
      : budget.state === "inactive"
        ? budget.exhausted_reason === null && (budget.ended_reason === null || Object.hasOwn(ended, budget.ended_reason))
        : budget.exhausted_reason === null && budget.ended_reason === null;
    if (!validCounters || !stateKnown || !reasonKnown || !runtimeConsistent) {
      box.append(el("p", "预算状态未提供或格式无法识别，暂不作判断。", "small muted"));
      return box;
    }
    const duration = (milliseconds) => `${Math.ceil(milliseconds / 1000).toLocaleString("zh-CN")} 秒`;
    const stateText = budget.state === "active"
      ? "正在使用"
      : budget.state === "exhausted"
        ? `已到限额：${exhaustion[budget.exhausted_reason]}`
        : budget.ended_reason
          ? `已结束：${ended[budget.ended_reason]}`
          : "尚未开始";
    box.append(fields([
      ["状态", stateText],
      ["自主提交", `${count(budget.submissions_used)} / ${count(budget.max_submissions)}`],
      ["模型评分", `${count(budget.policy_calls_used)} / ${count(budget.max_policy_calls)}`],
      ["时间", `${duration(budget.elapsed_ms)} / ${duration(budget.deadline_ms)}（剩余 ${duration(budget.remaining_ms)}）`],
    ]));
    if (budget.state === "exhausted") {
      box.append(el("p", "预算耗尽表示当前自主授权到限；不代表这一局已经结束或结果已确认。控制器是否释放请以上方状态为准。", "small muted"));
    } else if (budget.state === "inactive" && budget.ended_reason) {
      box.append(el("p", "预算结束不代表这一局已经结束或结果已确认；控制器是否释放请以上方状态为准。", "small muted"));
    }
    return box;
  }
  function evaluationPanel(value) {
    const box = panel(
      "本机已封装的评估记录",
      "这是有界 Runtime 操作与证据检查结果，不是游戏胜率或训练准入。",
    );
    const verified = value.evidence_verification === "pass";
    const recordedCounts = (counts) => {
      if (!counts || typeof counts !== "object" || Array.isArray(counts)) return null;
      const entries = Object.entries(counts);
      if (entries.length > 32 || entries.some(([kind, total]) =>
        !/^[a-z][a-z0-9_]{0,63}$/.test(kind) ||
        !Number.isSafeInteger(total) || total < 0)) return null;
      return entries.sort(([left], [right]) => left.localeCompare(right));
    };
    const actions = verified ? recordedCounts(value.recorded_action_verbs) : null;
    const deliveries = verified ? recordedCounts(value.delivery_counts) : null;
    const budgetReasons = {
      submission_attempt_limit:"自主提交次数已到限额",
      policy_call_limit:"模型评分次数已到限额",
      deadline:"自主运行时间已到限额",
    };
    const budget = value.budget_summary;
    const budgetCounts = verified && budget && typeof budget === "object" &&
      ["inactive", "active", "exhausted"].includes(budget.state) &&
      ["max_submissions", "submissions_used", "max_policy_calls", "policy_calls_used",
        "deadline_ms", "elapsed_ms"].every(key =>
        Number.isSafeInteger(budget[key]) && budget[key] >= 0);
    const terminal = value.terminal_screen_observation;
    const terminalLabel = !verified ? "证据未通过核验"
      : !Object.hasOwn(value, "terminal_screen_observation")
      ? "旧报告未提供此项观测"
      : terminal === null ? "未观察到"
        : terminal?.status === "ambiguous" ? "信息冲突"
          : terminal?.status === "observed" && terminal.result === "win" ? "胜利"
            : terminal?.status === "observed" && terminal.result === "loss" ? "失败"
              : "未能核对";
    box.append(
      fields([
        ["模型选择", value.selection_id],
        ["run ID", value.run_id],
        ["证据验证", value.evidence_verification || "未提供"],
        ["事件数", count(value.event_count)],
        ["原生投递尝试", verified && Number.isSafeInteger(value.native_submission_attempts) &&
          value.native_submission_attempts >= 0 ? count(value.native_submission_attempts) : "未确认"],
        ["预算耗尽原因", verified && Object.hasOwn(budgetReasons, value.budget_end_reason)
          ? budgetReasons[value.budget_end_reason] : "未从已验证证据确认"],
        ["本次记录观察到的结局页", terminalLabel],
        [
          "游戏结果",
          value.game_outcome === "not_measured"
            ? "未测量"
            : value.game_outcome || "未知",
        ],
      ]),
    );
    box.append(el("p", "仅已验证记录才显示动作类型与结局页；投递尝试不等于游戏接受，结局页不证明模型从开局完成整局。", "small muted"));
    box.append(actions ? table(["已记录结果的动作类型", "次数"],
      actions.map(([kind, total]) => [kind, count(total)])) :
      el("p", "动作类型：未从已验证证据确认。", "small muted"));
    box.append(deliveries ? table(["投递结果", "次数"],
      deliveries.map(([kind, total]) => [kind, count(total)])) :
      el("p", "投递结果：未从已验证证据确认。", "small muted"));
    if (budgetCounts) box.append(fields([
      ["预算提交", `${count(budget.submissions_used)} / ${count(budget.max_submissions)}`],
      ["预算评分", `${count(budget.policy_calls_used)} / ${count(budget.max_policy_calls)}`],
    ]));
    box.append(technical({
      evaluation_id:hex(value.evaluation_id) ? value.evaluation_id : null,
      evidence_content_id:hex(value.evidence_content_id) ? value.evidence_content_id : null,
      model_sha256:hex(value.model_sha256) ? value.model_sha256 : null,
      policy_manifest_sha256:hex(value.policy_manifest_sha256) ? value.policy_manifest_sha256 : null,
      runtime_code_sha256:hex(value.runtime_code_sha256) ? value.runtime_code_sha256 : null,
    }, "查看报告身份"));
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
    let budgetHost = null;
    if (state) box.append(localStatus(ctx, state, (node) => { budgetHost = node; }, catalog));
    else
      box.append(empty(
          "本机 Runtime 状态暂不可用",
          "执行控件保持关闭。请刷新状态，不要重复之前的命令。",
        ), command(ctx, "model-refresh-status", "刷新运行状态", async () => {
          stopModelWatch();
          await reload(ctx);
        }));
    const runtimeSetup = panel(
      "准备本机模型环境",
      "若当前工作台来自已验证发行包，可准备固定文本 Runtime；已有精确安装会直接复用。v2 记忆运行包目前需要维护者预置精确候选，未提供资产时会明确报错。此操作不会登记或加载模型。",
    );
    const lastSetup = state?.last_text_runtime_preparation;
    if (lastSetup?.status === "ready")
      runtimeSetup.append(el("p", `上次${["text-menu-m2-v1", "text-menu-m2-v2"].includes(lastSetup.runtime_profile) ? "记忆模型" : "文本菜单"}运行组件准备已通过核验；实际加载仍会重新检查。`, "small muted"));
    const canPrepareRuntime = state && !state.loaded &&
      state.operation?.status !== "pending" &&
      !["command_unknown", "recovery_required"].includes(state.status);
    for (const [profile, label] of [
      ["text-menu-v1", "准备文本菜单运行环境"],
      ["text-menu-m2-v1", "准备记忆模型运行环境"],
      ["text-menu-m2-v2", "检查 v2 记忆运行环境"],
    ])
      runtimeSetup.append(command(ctx, `prepare-runtime-${profile}`, label, async () => {
        await request(ctx, "/api/local-models/prepare-text-runtime", {runtime_profile: profile});
        note(ctx, "本机服务已接收准备请求；请以操作状态和安装回执为准。尚未登记或加载模型。");
        await reload(ctx);
      }, {disabled: !canPrepareRuntime}));
    box.append(runtimeSetup);
    const requestedSelection = new URLSearchParams(ctx.search).get("id");
    const focusSelection = selectionId(requestedSelection) ? requestedSelection : null;
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
      if (focusSelection === item.selection_id) {
        row.append(badge("刚登记的模型选择"));
        row.append(el("p", "登记不会加载模型。你仍可先检查本机加载条件，再明确选择准备并加载。", "small muted"));
      }
      row.append(
        technical({
          selection_id: item.selection_id,
          artifact_sha256: item.artifact_sha256,
          support: item.support,
          claims: item.claims,
        }),
      );
      const profiles = runProfiles(item);
      const profileKey = `model-run-profile:${ctx.scope}:${item.selection_id}`;
      const savedProfile = profiles.some(profile => profile.id === drafts.get(profileKey))
        ? drafts.get(profileKey) : null;
      const currentProfile = state?.loaded && state.selection_id === item.selection_id
        ? state.run_profile : savedProfile || item.default_run_profile;
      const selectedProfile = profiles.some(profile => profile.id === currentProfile)
        ? currentProfile : "";
      const profileChoice = select(row, "本次运行配置", `model-run-profile-${item.selection_id}`,
        profiles.map(profile => [profile.id, runProfileTitle(profile)]), selectedProfile);
      profileChoice.disabled = !state || state.loaded === true ||
        state.operation?.status === "pending" ||
        ["command_unknown", "recovery_required"].includes(state.status) ||
        !selectedProfile;
      profileChoice.onchange = () => {
        if (profiles.some(profile => profile.id === profileChoice.value))
          drafts.set(profileKey, profileChoice.value);
      };
      if (!selectedProfile)
        row.append(el("p", state?.loaded && state.selection_id === item.selection_id
          ? "本次已加载配置未能核对；不会改写当前运行。"
          : "本机服务未提供可核对的运行配置；暂不能请求加载。", "small muted"));
      if (item.run_profile_unavailable_reason === "extended_requires_text_menu_runtime")
        row.append(el("p", "这项旧模型选择仅支持默认短局；较长局需要文本菜单运行环境。", "small muted"));
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
            if (!profiles.some(profile => profile.id === profileChoice.value))
              throw new Error("invalid_run_profile");
            await request(ctx, "/api/local-models/prepare", {
              selection_id: item.selection_id,
              run_profile: profileChoice.value,
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
              !selectedProfile ||
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
    const evaluations = panel("本机评估历史",
      "这里只概览本次返回的本机记录（至多 100 条）；完整报告在评估结果页，不会自动上传。");
    if (Array.isArray(catalog?.evaluations)) {
      const records = catalog.evaluations;
      const passed = records.filter(value => value?.evidence_verification === "pass").length;
      const failed = records.filter(value => ["fail", "failed"].includes(value?.evidence_verification)).length;
      evaluations.append(fields([
        ["本次展示", count(records.length)],
        ["证据核验通过", count(passed)],
        ["证据核验未通过", count(failed)],
        ["核验状态未知或未提供", count(records.length - passed - failed)],
      ]));
    } else {
      evaluations.append(el("p", "本机评估历史暂不可用；未按空记录处理。", "small muted"));
    }
    evaluations.append(link("查看本机实战记录与完整报告", route("evaluations")));
    box.append(evaluations);
    if (live(ctx)) watchLocalModel(ctx, state, budgetHost);
    return box;
  }

  async function managedWorkspaceCard(ctx, box, {detail = false} = {}) {
    const data = await request(
      ctx,
      "/api/local-workspace/managed",
    );
    const localCsrfToken = data.csrf_token;
    delete data.csrf_token;
    if (data.status === "legacy_workspace_configured") {
      const curation = await request(ctx, "/api/local-workspace/curation");
      const curationReady = curation.schema === "stpd/local-curation-preparation-v1"
        && curation.status === "ready";
      if (detail && curationReady) return;
      box.append(panel(
        "正在使用现有本机资料库",
        curationReady
          ? "现有资料保留在原位置。用途记录已准备，可在同一资料库中检查和创建数据集。"
          : "现有资料保留在原位置。准备用途记录后，可在同一资料库中检查和创建数据集。",
      ));
      localCurationCard(ctx, box, curation);
      return;
    }
    if (data.status === "not_created") {
      const section = panel(
        "新建本机工作空间",
        "在本机工作台创建一个独立的空资料库，不需要登录或云端连接。不会导入资料、开始训练或替换已连接的旧资料库。",
      );
      if (data.orphaned_initializations)
        section.append(el("p", `检测到 ${data.orphaned_initializations} 个未登记的初始化目录；会保留原目录，不覆盖它们。`, "small muted"));
      if (typeof localCsrfToken === "string" && localCsrfToken) {
        section.append(command(ctx, "create-managed-local-workspace", "新建本机工作空间", async () => {
          await request(ctx, "/api/local-workspace/managed/create", {}, localCsrfToken);
          await reload(ctx);
        }, {primary:true}));
      } else {
        section.append(el("p", "本机保护验证暂不可用，请刷新页面后重试。", "small muted"));
      }
      box.append(section);
      return;
    }
    if (data.status !== "ready") {
      const section = panel("本机工作空间暂不可用", "登记或存储校验未通过。现有目录会保留；本页不会重新初始化或覆盖它们。");
      if (data.error_code) section.append(technical(data, "查看本机空间状态"));
      box.append(section);
      return;
    }
    const curation = await request(ctx, "/api/local-workspace/curation");
    const curationReady = curation.schema === "stpd/local-curation-preparation-v1"
      && curation.status === "ready";
    const workspaceReady = data.status === "ready" && data.curation_status === "ready";
    if (detail && (curationReady || workspaceReady)) return;
    const section = panel(
      "本机工作空间已就绪",
      "可在下方查看资料。创建本身不导入资料，也不表示资料已可训练。",
    );
    section.append(el("p", `空间 ${data.workspace_id} · 创建于 ${data.created_at}`, "small muted"));
    if (data.curation_status === "recovery_required")
      section.append(el("p", "本机用途记录需要恢复核对。现有资料仍可浏览；恢复完成前不能创建或授权数据集。", "small muted"));
    if (data.orphaned_initializations)
      section.append(el("p", `另有 ${data.orphaned_initializations} 个未登记的初始化目录保留在本机。`, "small muted"));
    box.append(section);
    localCurationCard(ctx, box, curation);
  }

  function localCurationCard(ctx, box, value) {
    const {csrf_token: csrfToken, ...data} = value;
    const section = panel("本机数据用途", "准备会保留现有资料；不能确认的旧用途会保持未知，不会自动获得 Gold 资格。");
    if (data.schema !== "stpd/local-curation-preparation-v1") {
      section.append(el("p", "本机用途准备状态格式暂不可用；资料仍保留在原位置。", "small muted"));
      box.append(section);
      return data;
    }
    const status = data.status;
    if (status === "preparation_required") {
      section.append(el("p", "首次准备会在本机建立用途记录，不会复制或删除录制和数据集。用途不明的旧记录不会被当作 Gold 数据。", "small muted"));
    } else if (status === "preparing") {
      section.append(el("p", "正在准备本机用途记录。刷新只读取进度，请勿重复提交。", "small muted"));
      if (data.phase) section.append(el("p", `进度：${show(data.phase)} · ${count(data.processed)} / ${count(data.total)}`, "small muted"));
    } else if (status === "ready") {
      const historyNote = data.historical_use_history === "unknown"
        ? "旧资料的用途历史未完全可证；Gold 仍按来源与已有用途限制。"
        : "用途不明的旧记录不会自动成为 Gold 数据。";
      section.append(el("p", `本机用途记录已准备。训练和测试数据仍按各自规则检查；${historyNote}`, "small muted"));
      if (Number.isInteger(data.known_dataset_count))
        section.append(el("p", `已核对数据集 ${count(data.known_dataset_count)} 份 · 用途未知 ${count(data.unknown_dataset_count)} 份 · 来源未知 ${count(data.unknown_source_count)} 份`, "small muted"));
    } else if (status === "recovery_required") {
      section.append(el("p", data.retry_available === true
        ? "上次准备遇到可继续的暂时错误。可显式继续同一次准备；不会另建用途账本。"
        : "本机用途记录需要恢复核对。现有资料仍保留；请先查看原因，不要重复准备。", "small muted"));
      if (data.retry_available === true && data.phase)
        section.append(el("p", `进度：${show(data.phase)} · ${count(data.processed)} / ${count(data.total)}`, "small muted"));
      if (data.reason) section.append(technical({reason: data.reason}, "查看恢复原因"));
    } else if (status === "not_applicable") {
      section.append(el("p", "当前资料库暂不需要用途准备。", "small muted"));
    } else {
      section.append(el("p", "本机用途状态暂不可用；请刷新读取，不会自动开始准备。", "small muted"));
    }
    if (Number.isInteger(data.legacy_dataset_count) && status === "preparation_required")
      section.append(el("p", `待核对的现有数据集：${count(data.legacy_dataset_count)} 份${Number.isInteger(data.unknown_dataset_count) ? `；旧用途未知：${count(data.unknown_dataset_count)} 份` : ""}`, "small muted"));
    if (status === "preparation_required" || status === "recovery_required" && data.retry_available === true) {
      const label = status === "preparation_required" ? "准备本机用途记录" : "继续准备本机用途记录";
      section.append(command(ctx, "prepare-local-curation", label, async () => {
        if (!csrfToken || !(status === "preparation_required" || status === "recovery_required" && data.retry_available === true)) return;
        await request(ctx, "/api/local-workspace/curation/prepare", {}, csrfToken);
        await reload(ctx);
      }, {primary:true, disabled:typeof csrfToken !== "string" || !csrfToken}));
    }
    if (["preparing", "ready", "recovery_required"].includes(status))
      section.append(command(ctx, "refresh-local-curation", "刷新用途状态", async () => reload(ctx), {type:"secondary"}));
    box.append(section);
    return data;
  }

  function localDatasetOverview(value) {
    const parameters = value.parameters && typeof value.parameters === "object"
      ? value.parameters : {};
    const purpose = {training:"训练", test:"测试", gold:"Gold 评估"}[parameters.purpose] || "未知";
    const records = Number.isSafeInteger(parameters.records) && parameters.records >= 0
      ? count(parameters.records) : "未知";
    const split = typeof parameters.split_status === "string" && parameters.split_status
      ? splitLabel(parameters.split_status) : "未知";
    const overview = panel("数据集概览", "以下信息来自本机已登记的数据集清单；未提供的字段显示为未知。");
    overview.append(fields([
      ["样本数", records],
      ["用途", purpose],
      ["数据划分", split],
    ]));

    const parents = Array.isArray(value.parents) ? value.parents.filter(parent =>
      parent && typeof parent === "object" && hex(parent.artifact_id)) : [];
    if (parents.length) {
      const parentLinks = el("div", null, "project-actions");
      for (const parent of parents) {
        const isSource = parent.role === `source_${parent.artifact_id}`;
        parentLinks.append(link(
          `${isSource ? "查看来源" : "查看父对象"} · ${parent.artifact_id.slice(0, 16)}`,
          route("local-workspace", parent.artifact_id),
        ));
      }
      overview.append(el("h3", "来源"), parentLinks);
    } else {
      overview.append(fields([["来源", "未知"]]));
    }
    if (hex(parameters.paired_training)) {
      overview.append(el("h3", "配对训练数据集"));
      overview.append(link(
        `查看配对训练数据集 · ${parameters.paired_training.slice(0, 16)}`,
        route("local-workspace", parameters.paired_training),
      ));
    }
    return overview;
  }

  const memoryRecipeViews = Object.freeze({
    "stage1a.dsimple.m2.k1.experimental.v1": {name:"M2-K1", reset:false, profile:"text-menu-v1"},
    "stage1a.dsimple.reset.k1.experimental.v1": {name:"Reset-K1", reset:true, profile:"text-menu-v1"},
    "stage1a.dsimple.m2.k8.experimental.v1": {name:"M2-K8", reset:false, profile:"text-menu-v1"},
    "stage1a.dsimple.reset.k8.experimental.v1": {name:"Reset-K8", reset:true, profile:"text-menu-v1"},
    "stage1a.dsimple.m2.k1.experimental.v2": {name:"M2-K1", reset:false, profile:"text-menu-v2"},
    "stage1a.dsimple.reset.k1.experimental.v2": {name:"Reset-K1", reset:true, profile:"text-menu-v2"},
    "stage1a.dsimple.m2.k8.experimental.v2": {name:"M2-K8", reset:false, profile:"text-menu-v2"},
    "stage1a.dsimple.reset.k8.experimental.v2": {name:"Reset-K8", reset:true, profile:"text-menu-v2"},
    "stage1a.dsimple.m2.k1.confirmed-interaction.v1": {name:"M2-K1", reset:false, profile:"text-menu-v1-confirmed-interaction", pageProfile:"text-menu-v1", history:true},
    "stage1a.dsimple.reset.k1.confirmed-interaction.v1": {name:"Reset-K1", reset:true, profile:"text-menu-v1-confirmed-interaction", pageProfile:"text-menu-v1", history:true},
    "stage1a.dsimple.m2.k8.confirmed-interaction.v1": {name:"M2-K8", reset:false, profile:"text-menu-v1-confirmed-interaction", pageProfile:"text-menu-v1", history:true},
    "stage1a.dsimple.reset.k8.confirmed-interaction.v1": {name:"Reset-K8", reset:true, profile:"text-menu-v1-confirmed-interaction", pageProfile:"text-menu-v1", history:true},
    "stage1a.dsimple.m2.k1.confirmed-interaction.v2": {name:"M2-K1", reset:false, profile:"text-menu-v2-confirmed-interaction", pageProfile:"text-menu-v2", history:true},
    "stage1a.dsimple.reset.k1.confirmed-interaction.v2": {name:"Reset-K1", reset:true, profile:"text-menu-v2-confirmed-interaction", pageProfile:"text-menu-v2", history:true},
    "stage1a.dsimple.m2.k8.confirmed-interaction.v2": {name:"M2-K8", reset:false, profile:"text-menu-v2-confirmed-interaction", pageProfile:"text-menu-v2", history:true},
    "stage1a.dsimple.reset.k8.confirmed-interaction.v2": {name:"Reset-K8", reset:true, profile:"text-menu-v2-confirmed-interaction", pageProfile:"text-menu-v2", history:true},
  });

  function memoryRecipeView(recipe) {
    return typeof recipe === "string" && Object.hasOwn(memoryRecipeViews, recipe)
      ? memoryRecipeViews[recipe] : null;
  }

  function memoryRecipeLabel(view) {
    const memory = view.history ? "操作记忆（含已确认的上一操作）" : "观察记忆";
    if (memoryRecipePageProfile(view) === "text-menu-v2")
      return `text-menu-v2 ${view.name} ${view.reset ? "工程对照" : "工程训练"} · ${memory}`;
    const name = view.reset ? `${view.name}（每步重置，独立训练对照）`
      : `实验性 D-Simple ${view.name}`;
    return `${name} · ${memory}`;
  }

  function memoryRecipePageProfile(view) {
    return view?.pageProfile || view?.profile;
  }

  function memoryModelVariant(value) {
    const parameters = value?.parameters;
    if (value?.kind !== "model" || !hex(value.artifact_id)
        || parameters?.schema !== "stpd/experimental-m2-model-v1") return null;
    return memoryRecipeView(value.workbench_memory_recipe);
  }

  function publicM0ModelProfile(value) {
    const parameters = value?.parameters;
    if (value?.kind !== "model" || !hex(value.artifact_id)
        || parameters?.schema !== "stpd/stage1a-light-action-m0-public-model-v1"
        || parameters.qualification !== "engineering_only"
        || parameters.input_schema !== "stpd/stage1a-light-action-m0-public-input-v1"
        || parameters.input_format !== "stpd-token-light-action-m0-public-v1") return null;
    const config = parameters.config && typeof parameters.config === "object"
      && !Array.isArray(parameters.config) ? parameters.config : {};
    const backbone = parameters.backbone && typeof parameters.backbone === "object"
      && !Array.isArray(parameters.backbone) ? parameters.backbone.kind : null;
    const recipes = {
      scratch: "stage1a.dsimple.light-action.m0.s.v1",
      pf: "stage1a.dsimple.light-action.m0.pf.v1",
      pl: "stage1a.dsimple.light-action.m0.pl.v1",
    };
    const renderer = parameters.source_renderer && typeof parameters.source_renderer === "object"
      && !Array.isArray(parameters.source_renderer) ? parameters.source_renderer : {};
    const profile = config.public_profile;
    const renderers = {
      public_lite: {version:"stpd-public-snapshot-lite-v1", profile:"public_lite", status:"provisional"},
      public_compact: {version:"stpd-public-snapshot-compact-v2", profile:"public_compact", status:"provisional"},
    };
    const expected = Object.hasOwn(renderers, profile) ? renderers[profile] : null;
    const binding = parameters.training_binding && typeof parameters.training_binding === "object"
      && !Array.isArray(parameters.training_binding) ? parameters.training_binding : null;
    const exactBinding = binding && Object.keys(binding).length === 5
      && ["schema", "dataset_ids", "training_operation_id", "allocation_id", "model_view_id"]
        .every(key => Object.hasOwn(binding, key))
      && binding.schema === "stpd/light-action-m0-training-binding-v1"
      && Array.isArray(binding.dataset_ids) && binding.dataset_ids.length > 0
      && binding.dataset_ids.every(identity => hex(identity))
      && binding.dataset_ids.join("\n") === [...new Set(binding.dataset_ids)].sort().join("\n")
      && hex(binding.training_operation_id, 32)
      && hex(binding.allocation_id) && hex(binding.model_view_id);
    if (config.recipe !== recipes[backbone] || parameters.recipe !== config.recipe || !expected
        || Object.keys(renderer).length !== 3
        || renderer.version !== expected.version || renderer.profile !== expected.profile
        || renderer.status !== expected.status
        || !exactBinding || config.device !== "cpu"
        || !Number.isSafeInteger(parameters.steps) || parameters.steps < 1
        || config.steps !== parameters.steps) return null;
    return {profile, backbone};
  }

  function supportsPublicM0WorkbenchActions(value) {
    return publicM0ModelProfile(value)?.backbone === "scratch";
  }

  function localModelOverview(value) {
    const parameters = value.parameters && typeof value.parameters === "object"
      && !Array.isArray(value.parameters) ? value.parameters : {};
    if (parameters.schema === "stpd/stage1a-light-action-m0-public-model-v1") {
      const variant = publicM0ModelProfile(value);
      const config = parameters.config && typeof parameters.config === "object"
        && !Array.isArray(parameters.config) ? parameters.config : {};
      const profileName = variant?.profile === "public_lite" ? "Public Lite（可读内联）"
        : variant?.profile === "public_compact" ? "Public Compact（兼容格式）" : "未知";
      const overview = panel("模型概览", "以下摘要来自本机模型清单；不会读取权重或代表模型质量。Public M0 按完整公开 Snapshot 和完整动作目录训练。 ");
      overview.append(fields([
        ["训练方式", "D-Simple 轻动作 M0"],
        ["公开输入", profileName],
        ["骨干", variant?.backbone === "scratch" ? "S · 从头训练"
          : variant?.backbone === "pf" ? "PF · 冻结骨干"
            : variant?.backbone === "pl" ? "PL · LoRA" : "未知"],
        ["训练步数", Number.isSafeInteger(parameters.steps)
          && parameters.steps > 0 && parameters.steps === config.steps
          ? count(parameters.steps) : "未知"],
        ["用途", "工程训练；不代表模型质量或实战能力"],
      ]));
      if (variant && variant.backbone !== "scratch")
        overview.append(el("p", "PF/PL Public M0 暂通过研究 CLI 管理；此 Workbench 尚不提供导出或登记操作。", "small muted"));
      return overview;
    }
    if (parameters.schema === "stpd/experimental-m2-model-v1") {
      const variant = memoryModelVariant(value);
      const overview = panel("模型概览", "以下是本机模型清单中的训练记录；此处不读取权重或评估模型质量。");
      overview.append(fields([
        ["训练配方", variant ? memoryRecipeLabel(variant) : "未知（模型结构不受支持）"],
        ["输入版本", memoryRecipePageProfile(variant) === "text-menu-v2"
          ? `${variant.profile} · Managed 工程操作，actor 未验证` : variant?.profile || "未知"],
        ["结果类型", memoryRecipePageProfile(variant) === "text-menu-v2"
          ? variant?.profile === "text-menu-v2-confirmed-interaction"
            ? "训练产物；可另选 Managed 工程来源做开发集评估，不代表独立游戏质量"
            : "训练产物；此输入版本暂不支持独立 Human 开发集评估"
          : variant ? "训练产物；开发集评估请在下方单独查看或启动"
          : "训练产物；模型结构未识别，暂不开放后续操作"],
        ["加载条件说明", variant
          ? "登记与加载时分别核验本机运行组件和当前环境；此处不表示实时状态"
          : "模型结构不受支持；不能从此页导出、登记或评估"],
      ]));
      return overview;
    }
    if (parameters.schema !== "stpd/stage1a-model-v1") return null;
    const config = parameters.config && typeof parameters.config === "object"
      && !Array.isArray(parameters.config) ? parameters.config : {};
    const recipes = {
      "stage1a.b.s.v1": {label:"B v1", backbone:"s", mode:"从头训练"},
      "stage1a.b.pf.v1": {label:"B v1", backbone:"pf", mode:"冻结预训练骨干"},
      "stage1a.dsimple.s.v1": {label:"D-Simple v1", backbone:"s", mode:"从头训练"},
      "stage1a.dsimple.pf.v1": {label:"D-Simple v1", backbone:"pf", mode:"冻结预训练骨干"},
      "stage1a.b.s.v2": {label:"B v2", backbone:"s", mode:"从头训练"},
      "stage1a.b.pf.v2": {label:"B v2", backbone:"pf", mode:"冻结预训练骨干"},
    };
    const recipe = typeof config.recipe === "string" && Object.hasOwn(recipes, config.recipe)
      ? recipes[config.recipe] : null;
    const backbone = parameters.backbone && typeof parameters.backbone === "object"
      && !Array.isArray(parameters.backbone) ? parameters.backbone.kind : null;
    const modelSource = recipe && backbone === (recipe.backbone === "s" ? "scratch" : "pf")
      ? recipe.mode
      : recipe && ["scratch", "pf"].includes(backbone)
        ? "未知（配方与模型来源记录不一致）" : "未知";
    const steps = Number.isSafeInteger(parameters.steps) && parameters.steps > 0
      && parameters.steps === config.steps ? count(parameters.steps) : "未知";
    const device = config.device === "cpu" ? "CPU"
      : config.device === "mps" ? "Apple MPS" : "未知";
    const qualification = parameters.qualification === "engineering_only"
      ? "工程验证用途；不代表模型质量或游戏实战资格"
      : "未知";
    const overview = panel("模型概览", "以下摘要来自本机模型清单；此处不会加载模型或读取权重文件。");
    overview.append(fields([
      ["训练配方", recipe?.label || "未知"],
      ["模型来源", modelSource],
      ["训练步数", steps],
      ["设备", device],
      ["用途说明", qualification],
    ]));
    const parentLabels = {run:"关联训练运行", model_view:"关联输入视图", checkpoint:"关联检查点"};
    const parents = Array.isArray(value.parents) ? value.parents.filter(parent =>
      parent && typeof parent === "object" && Object.hasOwn(parentLabels, parent.role)
        && hex(parent.artifact_id)) : [];
    if (parents.length) {
      const parentLinks = el("div", null, "project-actions");
      for (const parent of parents) {
        parentLinks.append(link(
          `${parentLabels[parent.role]} · ${parent.artifact_id.slice(0, 16)}`,
          route("local-workspace", parent.artifact_id),
        ));
      }
      overview.append(parentLinks);
    }
    return overview;
  }

  function supportsLocalModelExport(value) {
    const parameters = value?.parameters && typeof value.parameters === "object"
      && !Array.isArray(value.parameters) ? value.parameters : {};
    const config = parameters.config && typeof parameters.config === "object"
      && !Array.isArray(parameters.config) ? parameters.config : {};
    const serializer = parameters.serializer && typeof parameters.serializer === "object"
      && !Array.isArray(parameters.serializer) ? parameters.serializer : {};
    const backbone = parameters.backbone && typeof parameters.backbone === "object"
      && !Array.isArray(parameters.backbone) ? parameters.backbone : {};
    if (parameters.schema === "stpd/experimental-m2-model-v1")
      return memoryModelVariant(value) !== null;
    if (parameters.schema === "stpd/stage1a-light-action-m0-public-model-v1")
      return supportsPublicM0WorkbenchActions(value);
    const serializerKeys = ["input_profile", "profile", "source_schema", "status", "version"];
    return value?.kind === "model" && hex(value.artifact_id)
      && parameters.schema === "stpd/stage1a-model-v1"
      && parameters.qualification === "engineering_only"
      && ["stage1a.b.s.v2", "stage1a.dsimple.s.v1"].includes(config.recipe)
      && config.device === "cpu"
      && backbone.kind === "scratch"
      && Object.keys(serializer).length === serializerKeys.length
      && serializerKeys.every(key => Object.hasOwn(serializer, key))
      && serializer.version === "stpd-text-menu-current-page-v1"
      && serializer.profile === "text_menu_current_page"
      && serializer.source_schema === "sts2.player-environment/text-menu-snapshot-1"
      && serializer.input_profile === "text-menu-v1"
      && serializer.status === "provisional";
  }

  function localModelRegistrationReason(code) {
    const known = {
      local_models_extra_required: "本机模型计算依赖尚未按发行包准备；请先用已验证的开发者工具包初始化模型环境。尚未登记。",
      verified_export_required: "此模型当前没有可用的已校验导出；请先完成导出校验。",
      verified_export_receipt_required: "这份较早的记忆模型导出缺少校验回执；请点击“重新核验导出”，完成后再明确登记。",
      registration_timeout: "本次登记校验已超时；请先刷新状态核对结果，再按需明确重试。不会自动加载模型。",
      workspace_changed: "导出来自其他资料空间；请切回原资料空间再登记。",
      registration_metadata_invalid: "本机模型登记资料无法安全确认；请检查恢复状态。",
      source_binding_changed: "先前登记绑定的运行源码已变化；旧选择保留。可明确重新登记并生成新选择，不会改写旧登记。",
      text_runtime_profile_required: "本机文本菜单运行环境尚未准备；请先完成本机运行环境设置。",
      text_runtime_local_install_required: "本机文本菜单运行组件尚未准备；请检查运行环境状态。",
      text_menu_capabilities_unavailable: "暂时无法核对当前游戏的文本菜单能力。请打开游戏后刷新，再明确重试。",
      text_menu_capabilities_incompatible: "当前游戏环境不符合此模型的文本菜单要求；尚未登记。",
      observation_context_unavailable: "当前环境没有可验证的原子观察上下文；记忆模型尚未登记。",
      m2_runtime_contract_unavailable: "固定的记忆模型运行组件不支持所需决策协议；记忆模型尚未登记。",
      v2_runtime_contract_unavailable: "本机记忆模型运行组件缺少 text-menu-v2 SDK 合同；尚未登记。",
    };
    if (code === "managed_requires_confirmed_interaction_model") return "独立游戏环境需要支持已确认操作历史的 v2 模型。";
    if (["managed_contract_unavailable", "managed_environment_unavailable"].includes(code)) return "请先在环境与场景中启动兼容的独立游戏，再明确登记。";
    return known[code] || "当前无法完成登记。请查看本机模型页的环境状态后，再按需明确重试。";
  }

  async function localModelRegistrationCard(ctx, model, environmentKind = "native") {
    const managed = environmentKind === "managed";
    const actionName = managed ? "register-managed-model" : "register-local-model";
    const refreshName = managed ? "refresh-managed-model-registration" : "refresh-local-model-registration";
    const memory = model.parameters?.schema === "stpd/experimental-m2-model-v1";
    const publicM0Variant = publicM0ModelProfile(model);
    const publicM0 = publicM0Variant?.backbone === "scratch";
    if (publicM0Variant && !publicM0) {
      return panel("公共 M0 模型", "PF/PL Public M0 暂通过研究 CLI 管理；此 Workbench 尚不提供导出或登记操作。");
    }
    const expectedProfile = publicM0 ? "public-snapshot-m0-v1" : memory
      ? (model.workbench_memory_recipe?.endsWith(".v2")
        ? "text-menu-m2-v2" : "text-menu-m2-v1") : "text-menu-v1";
    const card = panel(
      managed ? "用于独立游戏环境" : publicM0 ? "登记到模型列表" : "用于原游戏",
      publicM0
        ? "登记会核对公开 Snapshot Runtime 合同与当前 Host/Connector 能力，并建立本机模型选择项；不会安装运行组件或加载模型。之后仍需在模型页单独检查条件并选择加载。"
        : "登记会依据本机文本菜单运行环境建立模型选择项；不会安装运行组件、加载模型或进入游戏。之后仍需在模型页单独检查条件并选择加载。",
    );
    if (managed) card.append(el("p", "先在“环境与场景”启动独立游戏。此登记复用同一份模型权重；加载时绑定所选环境，暂停模型后仍可继续同一局。", "small muted"));
    const statusPath = `/api/local-model-registrations/status?model_id=${encodeURIComponent(model.artifact_id)}${managed ? "&environment_kind=managed" : ""}`;
    let status;
    try {
      status = await request(ctx, statusPath);
    } catch {
      if (!live(ctx)) return card;
      card.append(el("p", "登记状态暂不可用；刷新只会重新读取状态。", "small muted"));
      card.append(command(ctx, refreshName, "刷新登记状态", async () => reload(ctx), {type:"secondary"}));
      return card;
    }
    if (!live(ctx)) return card;
    const validStatus = status && typeof status === "object" && !Array.isArray(status)
      && status.schema === "stpd/local-model-registration-v1"
      && status.model_id === model.artifact_id
      && (status.environment_kind || "native") === environmentKind
      && ["not_registered", "registered", "unavailable"].includes(status.status)
      && status.loaded === false && status.runtime_profile === expectedProfile;
    if (!validStatus || (status.status === "registered" && !selectionId(status.selection_id))) {
      card.append(el("p", "登记状态格式未知；未发起模型操作。", "small muted"));
      card.append(command(ctx, refreshName, "刷新登记状态", async () => reload(ctx), {type:"secondary"}));
      return card;
    }
    const csrf = typeof status.csrf_token === "string" && status.csrf_token.length > 0
      ? status.csrf_token : "";
    const registerAction = (label) => command(ctx, actionName, label, async () => {
      if (!live(ctx) || !supportsLocalModelExport(model)) return;
      try {
        const result = await request(ctx, "/api/local-model-registrations/register", {model_id:model.artifact_id, ...(managed ? {environment_kind:"managed"} : {})}, csrf);
        if (result.schema !== "stpd/local-model-registration-v1"
            || (result.environment_kind || "native") !== environmentKind
            || result.model_id !== model.artifact_id || result.status !== "registered"
            || result.loaded !== false || result.runtime_profile !== expectedProfile
            || !selectionId(result.selection_id))
          throw new Error("local_model_registration_invalid");
      } catch (error) {
        if (error.message === "request_unknown") {
          if (live(ctx)) await reload(ctx);
          else if (current?.account === ctx.account && current?.scope === ctx.scope)
            await window.SpireProject.reload();
          return;
        }
        if (["verified_export_required", "workspace_changed", "registration_metadata_invalid", "source_binding_changed"].includes(error.message)) {
          if (live(ctx)) await reload(ctx);
          else if (current?.account === ctx.account && current?.scope === ctx.scope)
            await window.SpireProject.reload();
          return;
        }
        throw error;
      }
      if (live(ctx)) await reload(ctx);
      else if (current?.account === ctx.account && current?.scope === ctx.scope)
        await window.SpireProject.reload();
    }, {primary:true, disabled:!csrf});
    if (!csrf && status.status !== "unavailable")
      card.append(el("p", "本机浏览器保护令牌暂不可用；刷新状态后再试。", "small muted"));
    if (status.status === "registered") {
      card.append(el("p", "此模型已登记到本机模型列表；这条登记不保证当前游戏环境兼容，Runtime 会在决策前重新检查。登记本身不会加载模型，当前运行状态请到模型页查看。", "small muted"));
      card.append(link("打开此模型选择", route("local-models", status.selection_id)));
      card.append(registerAction("重新核对登记"));
    } else if (status.status === "unavailable") {
      card.append(el("p", localModelRegistrationReason(status.reason_code), "small muted"));
      if (["text_runtime_profile_required", "text_runtime_local_install_required"].includes(status.reason_code))
        card.append(link("准备本机模型环境", route("local-models")));
      card.append(command(ctx, refreshName, "刷新登记状态", async () => reload(ctx), {type:"secondary"}));
    } else {
      card.append(el("p", status.reason_code === "source_binding_changed"
        ? localModelRegistrationReason(status.reason_code)
        : "登记只建立本机模型选择项，不会自动检查加载条件或执行游戏。", "small muted"));
      card.append(registerAction("登记到模型列表"));
      card.append(command(ctx, refreshName, "刷新登记状态", async () => reload(ctx), {type:"secondary"}));
    }
    return card;
  }

  async function localModelExportCard(ctx, model) {
    const variant = memoryModelVariant(model);
    const memory = variant !== null;
    const publicM0Variant = publicM0ModelProfile(model);
    const publicM0 = publicM0Variant?.backbone === "scratch";
    if (publicM0Variant && !publicM0) {
      return panel("公共 M0 模型", "PF/PL Public M0 暂通过研究 CLI 管理；此 Workbench 尚不提供导出或登记操作。");
    }
    const memoryName = variant?.name;
    const card = panel("导出并校验", publicM0
      ? "导出会重新核对 Public M0 的精确模型、完整动作目录和独立评分器加载；不会登记、加载或证明策略质量。"
      : memory
      ? `导出只保存并检查实验性 ${memoryName} 训练模型；导出校验不包含评估结论。登记前需单独固定记忆模型运行包并核对环境；导出不会自动登记或加载。`
      : "导出只保存并检查本机模型文件；不会登记为游戏模型或加载，也不检查游戏兼容性。服务端会重新验证模型身份。");
    const path = "/api/local-model-exports/status";
    let status;
    try {
      status = await request(ctx, path);
    } catch {
      if (live(ctx)) card.append(el("p", "导出状态暂不可用；请刷新状态后再试。", "small muted"));
      if (live(ctx)) card.append(command(ctx, "refresh-local-model-export", "刷新导出状态", async () => reload(ctx), {type:"secondary"}));
      return card;
    }
    if (!live(ctx)) return card;
    const operation = status?.operation && typeof status.operation === "object"
      && !Array.isArray(status.operation) ? status.operation : null;
    if (!["stpd/local-model-export-operation-v1",
          "stpd/local-model-export-operation-v2",
          "stpd/local-model-export-operation-v3"].includes(status?.schema)) {
      const message = "导出状态格式未知；未发起导出。";
      card.append(el("p", message, "small muted"));
      card.append(command(ctx, "refresh-local-model-export", "刷新导出状态", async () => reload(ctx), {type:"secondary"}));
      return card;
    }
    const knownStates = ["idle", "pending", "completed", "failed", "interrupted"];
    const validOwner = operation && (operation.status === "idle"
      ? operation.model_id === undefined || hex(operation.model_id)
      : hex(operation.model_id));
    const validType = status.schema === "stpd/local-model-export-operation-v1"
      ? operation?.model_type === undefined
      : status.schema === "stpd/local-model-export-operation-v2"
        ? operation?.model_type === "memory"
        : publicM0 && operation?.model_type === "public_m0"
          && operation?.profile === "public-snapshot-m0-v1";
    if (!operation || !knownStates.includes(operation.status) || !validOwner || !validType
        || (memory && operation.status !== "idle" && operation.model_id === model.artifact_id
            && status.schema !== "stpd/local-model-export-operation-v2")
        || (publicM0 && operation.status !== "idle" && operation.model_id === model.artifact_id
            && status.schema !== "stpd/local-model-export-operation-v3")) {
      card.append(el("p", "导出状态格式未知；未发起导出。", "small muted"));
      card.append(command(ctx, "refresh-local-model-export", "刷新导出状态", async () => reload(ctx), {type:"secondary"}));
      return card;
    }

    const csrf = typeof status.csrf_token === "string" && status.csrf_token.length > 0
      ? status.csrf_token : "";
    const startCommand = (label, disabled) => command(ctx, "start-local-model-export", label, async () => {
      if (!live(ctx) || !supportsLocalModelExport(model)) return;
      try {
        await request(ctx, "/api/local-model-exports/start", {model_id:model.artifact_id}, csrf);
      } catch (error) {
        if (live(ctx)) throw new Error(error.message === "request_unknown"
          ? "request_unknown" : "local_model_export_failed");
        throw error;
      }
      if (live(ctx)) await reload(ctx);
      else if (current?.account === ctx.account && current?.scope === ctx.scope)
        await window.SpireProject.reload();
    }, {primary:true, disabled});
    if (status.availability === "workspace_required") {
      card.append(el("p", "本机资料空间暂不可用；未显示导出结果，也未发起导出。", "small muted"));
      card.append(command(ctx, "refresh-local-model-export", "刷新导出状态", async () => reload(ctx), {type:"secondary"}));
      return card;
    }
    if (status.availability === "workspace_changed") {
      const unresolved = ["pending", "interrupted"].includes(operation.status);
      card.append(el("p", unresolved
        ? "之前本机资料空间的导出仍在处理中或结果未确认；请回到原资料空间核验，当前不能启动其他导出。"
        : "上次记录来自之前的本机资料空间；不会视为当前导出结果。可为当前模型明确重新导出并校验。", "small muted"));
      if (!csrf) card.append(el("p", "本机浏览器保护令牌暂不可用；刷新状态后再试。", "small muted"));
      card.append(startCommand(unresolved ? "等待核对原导出" : "为当前资料空间重新导出并校验", unresolved || !csrf));
      card.append(command(ctx, "refresh-local-model-export", "刷新导出状态", async () => reload(ctx), {type:"secondary"}));
      return card;
    }
    if (status.availability !== "ready") {
      card.append(el("p", "本机资料空间状态未知；未显示导出结果，也未发起导出。", "small muted"));
      card.append(command(ctx, "refresh-local-model-export", "刷新导出状态", async () => reload(ctx), {type:"secondary"}));
      return card;
    }

    const sameModel = operation.model_id === model.artifact_id;
    let label = "导出并校验";
    let disabled = !csrf;
    if (operation.status === "idle") {
      card.append(el("p", "尚无导出结果。", "small muted"));
    } else if (operation.status === "pending" && sameModel) {
      disabled = true;
      card.append(el("p", "此模型的导出与校验正在进行；刷新只读取状态。", "small muted"));
    } else if (operation.status === "pending") {
      disabled = true;
      label = "等待另一模型的导出完成";
      card.append(el("p", "另一模型的导出正在进行；完成前不能启动此模型的导出。", "small muted"));
    } else if (operation.status === "completed" && sameModel) {
      label = "重新核验导出";
      card.append(el("p", publicM0
        ? "Public M0 已通过独立评分器和产物身份校验；这不代表模型策略质量。登记仍会另行核对通用 Snapshot Runtime 和当前完整动作目录。"
        : memory
        ? `${memoryName} 训练模型已导出并校验；评估须在独立区域核对。登记还需核对记忆模型运行包与环境，加载另行操作。`
        : "导出校验本身不会加载模型；当前运行状态请到模型页查看。游戏兼容性仍须单独检查。", "small muted"));
      if (Number.isSafeInteger(operation.payload_bytes) && operation.payload_bytes >= 0)
        card.append(fields([["导出大小", bytes(operation.payload_bytes)]]));
      const registration = await localModelRegistrationCard(ctx, model);
      if (live(ctx)) card.append(registration);
      if (live(ctx) && variant?.profile === "text-menu-v2-confirmed-interaction") {
        const managedRegistration = await localModelRegistrationCard(ctx, model, "managed");
        if (live(ctx)) card.append(managedRegistration);
      }
    } else if (operation.status === "failed" && sameModel) {
      label = "重新导出并校验";
      card.append(el("p", "上次导出未完成。你可以明确再次发起；不会自动重试。", "small muted"));
    } else if (operation.status === "interrupted" && sameModel) {
      label = "核验或继续此模型导出";
      card.append(el("p", "上次操作中断，结果尚未确认。再次点击会明确核对此模型；不会自动重试。", "small muted"));
    } else if (operation.status === "interrupted") {
      disabled = true;
      label = "等待核对另一模型的导出";
      card.append(el("p", "另一模型的导出结果尚未确认；先核对该模型，再开始新的导出。", "small muted"));
    } else {
      card.append(el("p", "最近的导出记录属于另一模型。", "small muted"));
    }
    if (!csrf) card.append(el("p", "本机浏览器保护令牌暂不可用；刷新状态后再试。", "small muted"));
    card.append(startCommand(label, disabled));
    card.append(command(ctx, "refresh-local-model-export", "刷新导出状态", async () => reload(ctx), {type:"secondary"}));
    return card;
  }

  function offlineEvaluationMetrics(value) {
    const metrics = value && typeof value === "object" ? value : {};
    const number = key => Number.isFinite(metrics[key])
      ? new Intl.NumberFormat("zh-CN", {maximumFractionDigits:4, useGrouping:false}).format(metrics[key])
      : "未知";
    return fields([
      ["样本数", number("count")],
      ["首选命中率（非胜率，Top-1）", number("top1")],
      ["平均倒数排名（MRR）", number("mrr")],
      ["负对数似然（NLL）", number("nll")],
      ["置信度", number("confidence")],
      ["边际", number("margin")],
    ]);
  }

  async function localOfflineEvaluationDetail(ctx, artifact) {
    const parameters = artifact.parameters && typeof artifact.parameters === "object"
      ? artifact.parameters : {};
    const summary = panel("已记录的开发集结果", "这是生产者记录的离线摘要，不代表游戏通关或模型质量；本页未重新核验原始数据、模型权重或完整训练来源。");
    const schema = parameters.schema || parameters.evaluation_schema;
    if (parameters.partition === "test" || parameters.sealed_test === true) {
      summary.append(el("p", "封存测试评估不会在此读取或展示。", "small muted"));
      return summary;
    }
    if (parameters.partition !== "dev" || !hex(artifact.artifact_id)) {
      summary.append(el("p", "该对象未标明可展示的开发集分区；未请求评估摘要。", "small muted"));
      return summary;
    }
    if (!supportedOfflineEvaluationSchemas.has(schema)) {
      summary.append(el("p", "该开发集评估格式暂不支持指标摘要；此处仅显示对象metadata。", "small muted"));
      return summary;
    }
    try {
      const value = await request(ctx, `/api/local-workspace/evaluations/${artifact.artifact_id}`);
      if (value.schema !== "stpd/local-offline-evaluation-summary-v1"
          || value.evaluation_id !== artifact.artifact_id
          || value.evaluation_schema !== schema
          || value.partition !== "dev"
          || value.validation_scope !== "recorded_report_and_parent_identities"
          || value.interpretation !== "producer_recorded_summary_not_full_lineage_or_quality_verification") {
        summary.append(el("p", "评估摘要格式或核验范围未知，未将其视为已验证结果。", "small muted"));
        return summary;
      }
      const hasGroupingMetadata = value.grouping !== undefined
        || value.native_run_independence !== undefined;
      const memoryReport = schema === "stpd/experimental-m2-offline-evaluation-v1";
      const inputId = memoryReport ? value.evaluation_input_id : value.model_view_id;
      const inputSchema = memoryReport ? value.evaluation_input_schema : value.view_schema;
      const sessionScopedGroups = value.grouping === "session_scoped_run_group"
        && (value.native_run_independence === "unknown_across_sessions"
          || (memoryReport && value.native_run_independence === false));
      const facts = [
        ["评估格式", value.evaluation_schema || schema || "未知"],
        ["模型", hex(value.model_id) ? value.model_id.slice(0, 16) : "未知"],
        [memoryReport ? "评估输入" : "模型视图", hex(inputId) ? inputId.slice(0, 16) : "未知"],
        ["模型配方", typeof value.model_recipe === "string" && value.model_recipe ? value.model_recipe : "未知"],
        [memoryReport ? "输入格式" : "视图格式", typeof inputSchema === "string" && inputSchema ? inputSchema : "未知"],
        ["记录中的决策数", count(value.decision_count)],
        [sessionScopedGroups ? "录制分组数（不代表独立游戏局）" : "记录中的对局分组数（未复核独立性）",
          count(value.reported_run_groups)],
        ["多候选决策数", count(value.multi_candidate_count)],
        ["基准", value.baseline || "未知"],
      ];
      if (hasGroupingMetadata) facts.push(["独立性", sessionScopedGroups
        ? "未知（按录制分组计数，不证明来自不同游戏局）"
        : "未知（分组信息未确认，不据此认定为独立游戏局）"]);
      if (memoryReport) facts.push(
        ["来源隔离", value.semantic_overlap === true
          ? "已记录语义重叠诊断；不构成严格去重基准"
          : value.semantic_overlap === false ? "未发现语义重叠；不证明独立游戏局" : "未知"],
        ["严格去重基准", "未建立"],
        ["模型选择暴露", "未知"],
      );
      summary.append(fields(facts));
      const related = el("div", null, "project-actions");
      if (hex(value.model_id)) related.append(link(`查看本机模型 · ${value.model_id.slice(0, 16)}`, route("local-workspace", value.model_id)));
      if (hex(inputId)) related.append(link(`${memoryReport ? "查看评估输入" : "查看本机模型视图"} · ${inputId.slice(0, 16)}`, route("local-workspace", inputId)));
      if (memoryReport && hex(value.dev_source_id)) related.append(link(`查看开发来源 · ${value.dev_source_id.slice(0, 16)}`, route("local-workspace", value.dev_source_id)));
      if (related.children.length) summary.append(el("h3", "关联对象"), related);
      summary.append(el("h3", "总体记录指标"), offlineEvaluationMetrics(value.overall));
      if (value.baselines && typeof value.baselines === "object") {
        for (const [key, label] of [["uniform_legal", "均匀合法动作基准"], ["action_only", "仅动作基准"]]) {
          if (value.baselines[key]) summary.append(el("h3", label), offlineEvaluationMetrics(value.baselines[key]));
        }
      }
      summary.append(technical({
        validation_scope: value.validation_scope,
        qualification: value.qualification,
        scientific_verdict: value.scientific_verdict,
        interpretation: value.interpretation,
      }, "摘要范围与资格字段"));
    } catch (error) {
      summary.append(el("p", "无法读取这份开发集的本机摘要；这不表示评估为空或通过。", "small muted"));
      summary.append(technical({error: error?.message || "unknown"}, "查看摘要读取错误"));
    }
    return summary;
  }

  function localTrainingCode(value) {
    return typeof value === "string" && /^[a-z][a-z0-9_]{0,63}$/.test(value) ? value : null;
  }

  const localTrainingReasons = {
    local_models_extra_required: "本机模型计算依赖尚未按发行包准备；请先用已验证的开发者工具包初始化模型环境。尚未启动训练。",
    workspace_required: "本机资料空间尚未建立。",
    curation_preparation_required: "本机数据用途记录尚未准备。",
    curation_owner_recovery_required: "本机用途记录需要恢复核对。",
    human_engineering_sample_limit: "当前短训练只支持至多 32 条训练样本和 8 条开发样本；此数据集超过工程小样范围，数据仍保留。请减少录制来源并另建数据集；不会自动截断。",
    curated_training_dataset_required: "请从用途登记为训练的固定数据集启动。",
    training_claim_mismatch: "数据集用途记录与训练声明不一致。",
    explicit_training_dataset_required: "只能从用途已登记为训练的数据集启动。",
    nonempty_train_dev_required: "训练集与开发集都需要有合格的决策样本。",
    gold_reserved_data: "该来源包含已封存的 Gold 数据，不能用于训练。",
    source_isolation_index_pending: "来源隔离索引尚未就绪，不能启动训练。",
    source_index_incomplete: "来源隔离索引尚未就绪，不能启动训练。",
    independent_groups_required: "当前划分无法形成训练和开发两组。数据可以保留；需要补充不同输入，或使用后续支持的划分方式。操作标签登记本身不表示训练条件已满足。",
    clean_checkout_required: "当前源码状态未满足本机工程训练条件。",
    insufficient_independent_components: "独立对局数量不足，尚不能启动这项训练。",
    human_observation_missing: "缺少符合要求的公开真人观察，不能准备这项训练。",
    human_training_source_required: "记忆实验配方需要已发布并登记训练用途的 Human 观察来源。",
    human_observed_source_required: "记忆实验配方只接收完整核对的 Human 观察来源。",
    observed_sequence_limit_or_gap: "观察序列超出本机预算或存在缺口；不会裁剪后训练。",
    memory_episode_limit: "记忆实验来源超过本机最多 8 个重置段的预算。",
    episode_observation_limit: "记忆实验单个重置段超过 768 页预算；不会裁剪。",
    episode_input_token_limit: "记忆实验单个重置段超过 4,194,304 输入 token 预算；不会裁剪。",
    m2_limit_exceeded_no_truncation: "记忆实验页面超过 16,384 token 预算；不会截断。",
    episode_settling_limit: "已核对的过渡页超过每段 64 页预算；不会丢弃后训练。",
    projected_episode_count_mismatch: "记忆实验投影未保留完整重置段；请核对任务诊断。",
    memory_preparation_process_failed: "记忆实验输入准备未完成；结果待核对，不会自动重试。",
    unsupported_training_recipe: "训练配方不受支持。",
    new_experiment_precondition_failed: "请从已完成任务明确新建实验，并核对当前配方。",
    previous_training_outcome_unknown: "上次训练结果未确认；为防止重复任务，本机拒绝再次启动。",
  };

  function knownLocalTrainingReasonCode(code) {
    const safeCode = localTrainingCode(code);
    return safeCode && Object.hasOwn(localTrainingReasons, safeCode) ? safeCode : null;
  }

  function localTrainingReason(code) {
    const knownCode = knownLocalTrainingReasonCode(code);
    return (knownCode && localTrainingReasons[knownCode]) || "训练条件暂不可用。";
  }

  function localTrainingStage(stage) {
    const labels = {
      reserving: "登记训练任务",
      allocating: "固定训练数据分配",
      public_view: "准备训练视图",
      tokenizing: "准备模型输入",
      preparing_run: "准备训练运行",
      training: "正在训练",
      verifying_result: "核对训练结果",
      completed: "训练已完成",
    };
    return (stage && Object.hasOwn(labels, stage) && labels[stage]) || "训练阶段未知";
  }

  async function localMemoryEvaluationCard(ctx, model) {
    const variant = memoryModelVariant(model);
    const managed = variant.profile === "text-menu-v2-confirmed-interaction";
    const sourceSchema = managed ? "stpd/managed-text-menu-observed-source-v1"
      : "stpd/human-text-input-source-v1";
    const sourceName = managed ? "Managed 工程操作来源" : "Human 观察来源";
    const card = panel(`${variant.name} 独立来源开发集评估`,
      `仅对本机已登记的实验性 ${variant.name} 训练模型与另一份${sourceName}做开发用途工程评估。须明确点击才会启动；不是 Gold、独立游戏局、记忆收益或科学质量证明。`);
    if (variant.history) card.append(el("p",
      managed
        ? "操作记忆（含已确认的上一操作）需要已核对的 Managed v2 操作结果；同 seed 与精确候选会归为同一工程划分，资格由本机服务核对。"
        : "操作记忆（含已确认的上一操作）需要支持已确认操作历史的 Human 录制格式；可用历史与用途资格由本机服务核对，旧格式不会自动转换。", "small muted"));
    let status;
    try {
      status = await request(ctx, "/api/local-memory-evaluations/status");
    } catch {
      card.append(el("p", "评估状态暂不可读；没有启动评估。", "small muted"));
      return card;
    }
    if (!live(ctx)) return card;
    if (status?.schema !== "stpd/local-memory-evaluation-operation-v1"
        || !status.operation || typeof status.operation !== "object"
        || !["ready", "recovery_required"].includes(status.availability)) {
      card.append(el("p", "评估状态格式未知；无法启动。", "small muted"));
      return card;
    }
    const operation = status.operation;
    const sameModel = operation.model_id === model.artifact_id;
    if (status.availability !== "ready" || operation.status === "interrupted_unknown") {
      card.append(el("p", "上次评估结果或本机用途状态需要人工核对；不会自动重发。", "small muted"));
      return card;
    }
    if (operation.status === "pending") {
      card.append(el("p", sameModel ? "评估正在执行；刷新本页查看结果。" : "本机已有另一项评估正在执行。", "small muted"));
      card.append(command(ctx, "refresh-local-memory-evaluation", "刷新评估状态", async () => {
        await reload(ctx);
      }, {type:"secondary"}));
      return card;
    }
    if (sameModel && operation.status === "completed") {
      card.append(el("p", "开发用途离线工程评估已完成；仅显示生产者记录的结果摘要。", "small muted"));
      if (hex(operation.evaluation_id))
        card.append(link("查看开发集报告", route("local-workspace", operation.evaluation_id)));
      if (operation.semantic_overlap === true)
        card.append(el("p", "诊断提示训练与开发来源存在语义重叠；这不是严格去重基准。", "small muted"));
    } else if (sameModel && operation.status === "failed") {
      card.append(el("p", "开发集评估未完成。请核对模型、来源与本机用途记录；不会自动重试。", "small muted"));
      if (localTrainingCode(operation.error_code))
        card.append(technical({error_code:operation.error_code}, "查看失败代码"));
    } else if (!["idle", "completed", "failed"].includes(operation.status)) {
      card.append(el("p", "评估任务状态未知；无法启动。", "small muted"));
      return card;
    }
    if (!hex(model.artifact_id) || typeof status.csrf_token !== "string" || !status.csrf_token) {
      card.append(el("p", "本机浏览器保护令牌暂不可用；无法启动。", "small muted"));
      return card;
    }
    const offsetKey = `m2-dev-sources:${model.artifact_id}`;
    const offset = offsets.get(offsetKey) || 0;
    const params = new URLSearchParams({kind:"dataset", q:sourceSchema,
      limit:"100", offset:String(offset)});
    let inventory;
    try {
      inventory = await request(ctx, `/api/local-workspace?${params}`);
    } catch {
      card.append(el("p", `${sourceName}目录暂不可读；没有启动评估。`, "small muted"));
      return card;
    }
    if (!live(ctx)) return card;
    if (inventory?.schema !== "stpd/local-workspace-inventory-v1" || !Array.isArray(inventory.items)
        || !Number.isSafeInteger(inventory.total) || inventory.total < 0) {
      card.append(el("p", "来源目录格式未知；无法启动。", "small muted"));
      return card;
    }
    const sources = inventory.items.filter(item => item?.kind === "dataset"
      && item.parameters?.schema === sourceSchema && hex(item.artifact_id));
    if (!sources.length) card.append(el("p", `本页没有可选择的${sourceName}。`, "small muted"));
    else {
      const form = el("div", null, "project-form");
      const sourceLabel = item => {
        const suffix = item.artifact_id.slice(0, 16);
        if (sourceSchema !== "stpd/managed-text-menu-observed-source-v1")
          return `${sourceName} · ${suffix}`;
        const parameters = item.parameters;
        const expectedSeed = parameters.import_expectation?.seed;
        const seed = typeof expectedSeed === "string" && expectedSeed.trim()
          ? expectedSeed : "未知";
        const eventCount = Number.isSafeInteger(parameters.event_count) && parameters.event_count >= 0
          ? `${parameters.event_count} 条操作记录` : "记录数未知";
        return `${sourceName} · 种子 ${seed} · ${eventCount} · ${suffix}`;
      };
      const source = select(form, "开发来源", "local-memory-dev-source",
        [["", "请选择另一份来源"], ...sources.map(item =>
          [item.artifact_id, sourceLabel(item)])], "");
      card.append(form);
      const options = {primary:true};
      card.append(command(ctx, "start-local-memory-evaluation", "明确开始开发集评估", async () => {
        if (!live(ctx) || options.disabled || !sources.some(item => item.artifact_id === source.value)) return;
        options.disabled = true;
        await request(ctx, "/api/local-memory-evaluations/start",
          {model_id:model.artifact_id, source_id:source.value}, status.csrf_token);
        await reload(ctx);
      }, options));
    }
    const pager = el("div", null, "project-actions");
    if (offset > 0) pager.append(command(ctx, "m2-dev-sources-prev", "上一页来源", async () => {
      offsets.set(offsetKey, Math.max(0, offset - 100)); await reload(ctx);
    }, {type:"secondary"}));
    if (offset + 100 < inventory.total) pager.append(command(ctx, "m2-dev-sources-next", "下一页来源", async () => {
      offsets.set(offsetKey, offset + 100); await reload(ctx);
    }, {type:"secondary"}));
    if (pager.children.length) card.append(pager);
    return card;
  }

  async function localTrainingCard(ctx, dataset) {
    const curatedTrainingDataset = dataset.parameters?.schema === "stpd/curated-decision-dataset-v1"
      && dataset.parameters?.purpose === "training";
    const card = panel(
      "本机短训练",
      curatedTrainingDataset
        ? "Public M0 Lite 是此入口的默认工程小样：完整公开 Snapshot、完整动作目录、D-Simple 轻动作图、CPU 从头训练 3 步。既有任务的配方以其模型记录为准；可明确选择 Compact 兼容格式或保留既有 D-Simple 短配方。不会自动下载骨干、登记或加载模型，也不代表策略质量。"
        : dataset.parameters?.schema === "stpd/managed-text-menu-observed-source-v1"
        ? "此来源只可明确选择 text-menu-v2 M2 或 Reset 的 K1/K8 工程训练。操作者未验证；仅训练，不生成独立开发集指标，也不代表模型质量或记忆收益。"
        : "从此入口新启动的任务默认使用 D-Simple-S v1、CPU 2 线程和 3 步；既有任务的配方以其模型记录为准。可明确选择实验性 M2 或 Reset 的 K1/K8 配方（Reset 每步重置，独立训练对照）；记忆配方仅训练、不做独立评估或开发集指标。本机服务会核对训练用途与来源资格；结果不代表模型策略质量或记忆收益。",
    );
    if (!curatedTrainingDataset)
      card.append(el("p", "观察记忆使用页面观察；操作记忆（含已确认的上一操作）需要支持已确认操作历史的录制格式。可用历史与训练用途资格由本机服务核对，旧格式不会自动转换。", "small muted"));
    let data;
    try {
      data = await request(ctx, "/api/local-training/status");
    } catch (error) {
      if (!live(ctx)) return card;
      card.append(el("p", "本机训练服务暂不可用；没有启动训练。", "small muted"));
      card.append(technical({error:"local_training_status_unavailable"}, "查看训练服务错误"));
      return card;
    }
    if (!live(ctx)) return card;
    if (!data || typeof data !== "object" || Array.isArray(data)
        || !["stpd/local-training-operation-v1", "stpd/local-training-operation-v2",
          "stpd/local-training-operation-v3"].includes(data.schema)
        || !data.operation || typeof data.operation !== "object") {
      card.append(el("p", "本机训练状态格式未知，当前不能启动训练。", "small muted"));
      card.append(technical({error_code:"unknown_local_training_status_schema"}, "查看状态格式错误"));
      return card;
    }
    if (data.schema === "stpd/local-training-operation-v3"
        && data.operation.status !== "idle"
        && !["public_lite", "public_compact"].includes(data.operation.input_profile)) {
      card.append(el("p", "Public M0 训练状态缺少可核对的公开输入配置；未显示结果或启动新任务。", "small muted"));
      card.append(technical({error_code:"unknown_public_m0_training_profile"}, "查看训练状态"));
      return card;
    }
    const csrfToken = data.csrf_token;
    if (data.availability !== "ready") {
      card.append(el("p", localTrainingReason(data.reason || data.availability), "small muted"));
      const reasonCode = knownLocalTrainingReasonCode(data.reason);
      if (reasonCode) card.append(technical({reason:reasonCode}, "查看训练条件代码"));
      if (["workspace_required", "preparation_required", "recovery_required"].includes(data.availability))
        card.append(link("打开本机资料与准备状态", route("local-workspace")));
      return card;
    }
    const operation = data.operation;
    const currentForDataset = operation.dataset_id === dataset.artifact_id;
    const taskId = hex(operation.operation_id, 32) ? operation.operation_id : null;
    const v2MemoryRecipe = "stage1a.dsimple.m2.k1.experimental.v2";
    const managed = dataset.parameters?.schema === "stpd/managed-text-menu-observed-source-v1";
    const defaultRecipe = "stage1a.dsimple.s.v1";
    const memoryView = memoryRecipeView(operation.recipe);
    const currentPublicProfile = ["public_lite", "public_compact"].includes(operation.input_profile)
      ? operation.input_profile : null;
    const recipeLabel = currentPublicProfile === "public_lite" ? "Public M0 Lite · 3 步 CPU 工程训练"
      : currentPublicProfile === "public_compact" ? "Public M0 Compact · 3 步 CPU 工程训练"
        : memoryView ? memoryRecipeLabel(memoryView)
          : operation.recipe === defaultRecipe ? "D-Simple-S v1" : "以模型记录为准";
    const choices = curatedTrainingDataset
      ? [["public_lite", "Public M0 Lite（默认，可读内联）"],
        ["public_compact", "Public M0 Compact（兼容格式）"],
        [defaultRecipe, "既有 D-Simple-S v1（文本菜单输入）"]]
      : Object.entries(memoryRecipeViews)
        .filter(([, view]) => memoryRecipePageProfile(view) === (managed ? "text-menu-v2" : "text-menu-v1"))
        .map(([id, view]) => [id, `${memoryRecipeLabel(view)} · 仅训练`]);
    if (!managed && !curatedTrainingDataset)
      choices.unshift([defaultRecipe, "D-Simple-S v1（默认，短训练）"]);
    if (operation.status !== "idle") card.append(el("p", `当前任务配方：${recipeLabel}。${operation.result_type === "train_only" ? "此训练任务不执行独立评估。" : ""}`, "small muted"));
    if (operation.status === "pending") {
      const stage = localTrainingStage(operation.stage);
      card.append(el("p", currentForDataset
        ? `${stage}。关闭游戏不代表训练暂停；刷新只读取本机工作台报告的状态。`
        : "本机已有训练任务正在执行；需等待其明确结果后再启动另一项。", "small muted"));
      if (!currentForDataset && hex(operation.dataset_id))
        card.append(link("打开正在训练的数据集", route("local-workspace", operation.dataset_id)));
    } else if (["interrupted_unknown", "recovery_required"].includes(operation.status)) {
      card.append(el("p", currentForDataset
        ? "上次训练结果未能确认。为避免重复启动，本页不会重发或自动恢复；请查看任务诊断。"
        : "另一项本机训练结果未能确认。核对前不会启动新任务。", "small muted"));
      if (!currentForDataset && hex(operation.dataset_id))
        card.append(link("打开待核对的数据集", route("local-workspace", operation.dataset_id)));
    } else if (operation.status === "failed") {
      card.append(el("p", localTrainingReason(operation.error_code), "small muted"));
      const errorCode = knownLocalTrainingReasonCode(operation.error_code);
      if (errorCode) card.append(technical({error_code:errorCode}, "查看失败代码"));
      if (operation.error_code === "human_engineering_sample_limit")
        card.append(link("返回本机资料，另建较小数据集", route("local-workspace")));
      if (operation.run_id) {
        card.append(el("p", "已有训练运行记录；不会从未确认状态自动重试。", "small muted"));
        if (!currentForDataset && hex(operation.dataset_id))
          card.append(link("打开待核对的数据集", route("local-workspace", operation.dataset_id)));
      }
    } else if (currentForDataset && operation.status === "completed") {
      card.append(el("p", currentPublicProfile
        ? `Public M0 ${currentPublicProfile === "public_lite" ? "Lite" : "Compact"} 工程训练已完成；模型仍未登记或加载，结果不代表策略质量。`
        : operation.result_type === "train_only"
        ? `${memoryView?.name || "实验性"} 训练任务已完成；此任务不包含开发集评估指标，也没有加载到游戏。`
        : "本机训练已完成；这不表示模型已加载到游戏或具备已验证的策略质量。", "small muted"));
    } else if (!(["idle", "completed", "failed"].includes(operation.status))) {
      card.append(el("p", "本机训练状态暂不支持启动；请查看诊断信息。", "small muted"));
      card.append(technical({status:"unsupported_operation_status"}, "查看训练状态"));
      return card;
    }
    if (currentForDataset && operation.status === "completed") {
      for (const [field, label] of [
        ["result_id", "查看训练结果"],
        ["model_id", "查看本机模型"],
      ]) {
        if (hex(operation[field])) card.append(link(label, route("local-workspace", operation[field])));
      }
      if (hex(operation.evaluation_id))
        card.append(link("查看开发集结果", route("local-workspace", operation.evaluation_id)));
    }
    const previousCompleted = operation.previous_completed;
    if (previousCompleted && typeof previousCompleted === "object" && !Array.isArray(previousCompleted)
        && previousCompleted.dataset_id === dataset.artifact_id
        && hex(previousCompleted.operation_id, 32)
        && hex(previousCompleted.result_id) && hex(previousCompleted.model_id)
        && (hex(previousCompleted.evaluation_id) || previousCompleted.result_type === "train_only")) {
      card.append(el("p", "上一次已完成训练的结果仍可打开。", "small muted"));
      for (const [field, label] of [
        ["result_id", "查看上一次训练结果"],
        ["model_id", "查看上一次本机模型"],
        ["evaluation_id", "查看上一次开发集结果"],
      ]) if (hex(previousCompleted[field])) card.append(link(label, route("local-workspace", previousCompleted[field])));
    }
    const runId = hex(operation.run_id) ? operation.run_id : null;
    const checkpointId = hex(operation.checkpoint_id) ? operation.checkpoint_id : null;
    if (taskId) card.append(technical({operation_id:taskId, run_id:runId,
      checkpoint_id:checkpointId}, "查看运行身份"));

    const blocksStart = operation.status === "pending"
      || ["interrupted_unknown", "recovery_required"].includes(operation.status)
      || (currentForDataset && operation.status === "completed")
      || (currentForDataset && operation.status === "failed" && operation.error_code === "human_engineering_sample_limit")
      || (operation.status === "failed" && Boolean(operation.run_id));
    const hasCsrf = typeof csrfToken === "string" && Boolean(csrfToken);
    const canStart = !blocksStart && hasCsrf && hex(dataset.artifact_id);
    if (!hasCsrf)
      card.append(el("p", "本机浏览器保护令牌暂不可用，请刷新后重试。", "small muted"));
    if (canStart) {
      const form = el("div");
      const defaultChoice = currentPublicProfile || (curatedTrainingDataset
        ? "public_lite" : managed ? v2MemoryRecipe : defaultRecipe);
      const recipe = select(form, curatedTrainingDataset ? "训练方式" : "训练配方",
        "local-training-recipe", choices, choices.some(([id]) => id === defaultChoice)
          ? defaultChoice : choices[0]?.[0] || defaultRecipe);
      card.append(form);
      const startOptions = {primary:true};
      card.append(command(ctx, "start-local-training",
        currentForDataset && operation.status === "failed" ? "重新尝试一次短训练" : "开始本机短训练",
        async () => {
          if (!live(ctx) || startOptions.disabled || !hex(dataset.artifact_id)
              || !choices.some(([id]) => id === recipe.value)) return;
          startOptions.disabled = true;
          await request(ctx, "/api/local-training/start", {
            dataset_id:dataset.artifact_id,
            ...(["public_lite", "public_compact"].includes(recipe.value)
              ? {input_profile:recipe.value}
              : memoryRecipeView(recipe.value) ? {recipe:recipe.value} : {}),
          }, csrfToken);
          await reload(ctx);
        }, startOptions));
    }
    if (currentForDataset && operation.status === "completed" && taskId && hasCsrf
        && hex(operation.run_id) && hex(operation.result_id) && hex(operation.model_id)
        && (hex(operation.evaluation_id) || operation.result_type === "train_only")) {
      const form = el("div");
      const previousChoice = currentPublicProfile || operation.recipe;
      const recipe = select(form, "新实验配方", "local-training-new-recipe", choices,
        choices.some(([id]) => id === previousChoice) ? previousChoice
          : curatedTrainingDataset ? "public_lite" : managed ? v2MemoryRecipe : defaultRecipe);
      card.append(form);
      const newOptions = {type:"secondary"};
      card.append(command(ctx, "start-local-training-new", "新建一次训练", async () => {
        if (!live(ctx) || newOptions.disabled || !hex(dataset.artifact_id)
            || !choices.some(([id]) => id === recipe.value)) return;
        newOptions.disabled = true;
        await request(ctx, "/api/local-training/start", {
          dataset_id:dataset.artifact_id, after_completed_operation_id:taskId,
          ...(["public_lite", "public_compact"].includes(recipe.value)
            ? {input_profile:recipe.value}
            : memoryRecipeView(recipe.value) ? {recipe:recipe.value} : {}),
        }, csrfToken);
        await reload(ctx);
      }, newOptions));
    }
    card.append(command(ctx, "refresh-local-training-status", "刷新训练状态", async () => {
      await reload(ctx);
    }, {type:"secondary"}));
    return card;
  }

  function localRecordingCard(ctx, importStatus) {
    const section = panel(
      "本机录制来源",
      "选择已结束的录制，验证后加入本机资料库。原始录制保留，不会上传或开始训练。",
    );
    const {csrf_token: localCsrfToken, ...safeImportStatus} = importStatus;
    const memberArchiveImport = importStatus.source_kind === "member_archive";
    if (importStatus.status === "pending") {
      section.append(el("p", memberArchiveImport
        ? "本机正在核对并导入所选成员归档；可离开本页，稍后刷新状态。不会自动重试。"
        : "本机正在打包并验证所选录制；可离开本页，稍后刷新状态。", "small muted"));
    } else if (importStatus.status === "completed") {
      section.append(el("p", memberArchiveImport
        ? "成员归档已验证并导入本机。导入本身不会评定训练资格或开始训练。"
        : "上一次本机导入已完成。", "small muted"));
      if (importStatus.artifact_id)
        section.append(link(memberArchiveImport ? "打开对象并单独预览样本" : "查看已导入对象",
          route("local-workspace", importStatus.artifact_id)));
      if (memberArchiveImport)
        section.append(el("p", "下一步：在对象详情中明确预览样本；来源索引与训练用途资格仍需另行评估。", "small muted"));
    } else if (importStatus.status === "published_index_unavailable") {
      section.append(el("p", memberArchiveImport
        ? "成员归档证据已写入本机资料库，但本机索引更新失败；请查看状态并处理索引后再单独预览。不会自动重试或训练。"
        : "归档已写入本机资料库，但索引更新失败；请查看状态并修复后再明确重试。", "small muted"));
    } else if (["failed", "interrupted_unknown", "publication_unknown"].includes(importStatus.status)) {
      section.append(el("p", memberArchiveImport
        ? "成员归档导入未完成或结果未知。原下载保留，不会自动重试；请查看具体错误并刷新可用归档后再明确操作。"
        : "上一次导入未确认完成；不会自动重试。请查看状态并核对后再明确操作。", "small muted"));
    }
    if (importStatus.error_code) {
      if (memberArchiveImport && ["failed", "interrupted_unknown", "publication_unknown"].includes(importStatus.status))
        section.append(el("p", failure({message:importStatus.error_code}), "small muted"));
      section.append(technical(safeImportStatus, "查看导入状态"));
    }
    section.append(command(ctx, "refresh-local-import-status", "刷新导入状态", async () => {
      await reload(ctx);
    }, {type:"secondary"}));
    if (localRecordingSnapshot === null) {
      section.append(command(ctx, "read-local-recordings", "查看录制来源", async () => {
        localRecordingSnapshot = await request(ctx, "/api/local-recordings");
        await reload(ctx);
      }, {type:"secondary"}));
      return section;
    }
    const data = localRecordingSnapshot;
    section.append(command(ctx, "refresh-local-recordings", "刷新录制来源", async () => {
      localRecordingSnapshot = await request(ctx, "/api/local-recordings");
      await reload(ctx);
    }, {type:"secondary"}));
    if (data.status === "tool_registration_missing") {
      section.append(el("p", "尚未注册本机录制组件。请按安装说明完成注册后，再查看录制来源。", "small muted"));
      return section;
    }
    if (data.status !== "ready") {
      const message = data.status === "recordings_unavailable"
        ? "本机录制目录当前无法读取，请检查游戏内录制设置。"
        : "本机录制来源暂不可用；请查看诊断信息后再刷新。";
      section.append(el("p", message, "small muted"));
      if (data.error_code) section.append(technical(data, "查看本机来源状态"));
      return section;
    }
    const basis = data.root_basis === "current_runtime"
      ? "当前游戏连接与加载身份已核对。"
      : "游戏当前未运行；此状态只表示录制目录已配置。";
    section.append(el("p", basis, "small muted"));
    section.append(el("p", "列表中的录制清单与结束回执相互匹配；内容尚未验证，也不能证明由真人操作。", "small muted"));
    section.append(el("p", `观察时间：${data.observed_at || "未知"}`, "small muted"));
    if (data.unsealed_count)
      section.append(el("p", `${data.unsealed_count} 个尚未结束的录制未列出。`, "small muted"));
    if (data.truncated)
      section.append(el("p", "已达到列表上限，当前列表不包含全部记录。", "small muted"));
    const rows = (data.candidates || []).map(item => [
      String(item.session_id || "未知").slice(0, 24),
      String(item.timeline_id || "未知").slice(0, 24),
      item.closed_at || "未知",
      "录制已结束，内容待验证",
    ]);
    section.append(table(["录制", "时间线", "结束时间", "状态"], rows));
    const candidates = (data.candidates || []).filter(item =>
      !(importStatus.status === "completed" && importStatus.candidate_id === item.candidate_id));
    if (candidates.length) {
      const selection = select(section, "要导入的录制", "local-recording-selection",
        [["", "请选择一条录制"], ...candidates.map(item => [item.candidate_id,
          `${item.closed_at || "结束时间未知"} · ${item.session_id}`])], "");
      const checkbox = input(section, "我确认所选录制来自真人操作", "local-recording-attestation", false, "checkbox");
      const canImport = () => checkbox.checked && candidates.some(item => item.candidate_id === selection.value)
        && importStatus.status !== "pending" && !!localCsrfToken;
      const button = command(ctx, "import-local-recording", "验证并导入本机", async () => {
        if (!canImport()) return;
        await request(ctx, "/api/local-recordings/import", {
          candidate_id: selection.value,
          human_origin_attested: true,
        }, localCsrfToken);
        await reload(ctx);
      }, {disabled:true});
      selection.onchange = () => {
        checkbox.checked = false;
        button.disabled = true;
      };
      checkbox.onchange = () => { button.disabled = !canImport(); };
      section.append(button);
    }
    if (!data.candidate_count)
      section.append(empty("没有检测到已结束的录制", "尚未结束或信息不完整的录制不会列出。"));
    return section;
  }

  function localMemberArchiveCard(ctx, importStatus, csrfToken) {
    const section = panel(
      "导入已下载的成员归档",
      "只列出本机已有完整下载回执的集合归档。此操作仅导入到本机；来源索引和训练资格另行评估，不会自动训练。",
    );
    if (localMemberArchiveSnapshot === null) {
      section.append(command(ctx, "read-member-archives", "查看已下载成员归档", async () => {
        localMemberArchiveSnapshot = await request(ctx, "/api/local-recordings/member-archives");
        await reload(ctx);
      }, {type:"secondary"}));
      return section;
    }

    const data = localMemberArchiveSnapshot;
    section.append(command(ctx, "refresh-member-archives", "刷新已下载归档", async () => {
      localMemberArchiveSnapshot = await request(ctx, "/api/local-recordings/member-archives");
      await reload(ctx);
    }, {type:"secondary"}));
    if (data.schema !== "stpd/local-member-collection-archive-catalog-v1"
        || !Array.isArray(data.items)) {
      section.append(el("p", "本机已下载归档目录格式不可用；请刷新状态。", "small muted"));
      return section;
    }
    if (data.excluded_count)
      section.append(el("p", `${count(data.excluded_count)} 个不完整或不符合条件的下载未列入可导入清单。`, "small muted"));
    if (data.truncated)
      section.append(el("p", "本次目录检查只处理了前 200 个导出目录；超出部分暂未列出。", "small muted"));

    const items = data.items.filter(item =>
      hex(item?.export_id) && hex(item?.file_id) && hex(item?.artifact_id)
      && hex(item?.upload_id, 32) && Number.isSafeInteger(item?.size) && item.size > 0
      && item.name === "项目成员集合归档");
    const rows = items.map(item => [
      item.name,
      bytes(item.size),
      el("span", item.upload_id, "mono break"),
      el("span", `${item.export_id} · ${item.file_id}`, "mono break"),
    ]);
    if (rows.length) section.append(table(["名称", "大小", "来源 collection ID", "导出 / 文件身份"], rows));

    const alreadyImported = item => importStatus.status === "completed"
      && importStatus.source_kind === "member_archive"
      && importStatus.export_id === item.export_id && importStatus.file_id === item.file_id;
    const selectable = items.filter(item => !alreadyImported(item));
    if (importStatus.status === "pending") {
      section.append(el("p", "已有本机导入任务正在处理。刷新导入状态后再选择其他归档。", "small muted"));
    } else if (selectable.length) {
      const choiceId = item => `${item.export_id}/${item.file_id}`;
      const selection = select(section, "选择一个集合归档", "member-archive-selection", [
        ["", "请选择一份已下载归档"],
        ...selectable.map(item => [choiceId(item), `${item.name} · ${item.upload_id.slice(0, 12)} · ${bytes(item.size)}`]),
      ], "");
      const checkbox = input(section, "我确认所选归档来自真人操作", "member-archive-attestation", false, "checkbox");
      const selectedItem = () => selectable.find(item => choiceId(item) === selection.value);
      const canImport = () => checkbox.checked && !!selectedItem() && !!csrfToken;
      const button = command(ctx, "import-member-archive", "验证并导入本机", async () => {
        const item = selectedItem();
        if (!canImport() || !item) return;
        await request(ctx, "/api/local-recordings/import-member-archive", {
          export_id: item.export_id,
          file_id: item.file_id,
          human_origin_attested: true,
        }, csrfToken);
        await reload(ctx);
      }, {disabled:true, primary:true});
      selection.onchange = () => {
        checkbox.checked = false;
        button.disabled = true;
      };
      checkbox.onchange = () => { button.disabled = !canImport(); };
      section.append(button, command(ctx, "cancel-member-archive-selection", "取消选择", async () => {
        selection.value = "";
        checkbox.checked = false;
        button.disabled = true;
      }, {type:"secondary"}));
    } else if (!items.length) {
      section.append(empty("没有可导入的已验证成员归档", "这里只显示可验证的 collection archive；产物文件与其他下载不会作为录制导入。"));
    } else {
      section.append(el("p", "当前列出的成员归档已导入。", "small muted"));
    }
    return section;
  }

  async function localDatasetCard(ctx, artifactId) {
    const section = panel(
      "本机数据集检查",
      "完整决策数量来自已核验的当前录制；输入标签不能当作完整转移。检查或创建都需要明确点击，不代表数据量已足以训练。",
    );
    const data = await request(ctx, "/api/local-datasets/status");
    if (data.schema !== "stpd/local-dataset-operation-v1") {
      section.append(el("p", "本机数据集状态格式暂不可用；现有录制仍可浏览。", "small muted"));
      return section;
    }
    if (data.availability !== "ready") {
      const message = data.availability === "workspace_required"
        ? "本机资料空间尚未建立。录制仍可浏览；创建空间后才能检查数据集。"
        : data.availability === "preparation_required"
          ? "本机用途记录尚未准备。录制仍可浏览；请先在资料目录准备用途记录，再检查数据集。"
        : data.availability === "recovery_required"
          ? "本机用途记录需要恢复。录制仍可浏览；完成恢复前不能创建或授权数据集。"
          : "本机数据集服务暂不可用；录制仍可浏览。";
      section.append(el("p", message, "small muted"));
      if (data.reason) section.append(el("p", String(data.reason), "small muted"));
      return section;
    }
    const operation = data.operation && typeof data.operation === "object"
      ? data.operation : {status:"idle"};
    const purposes = ["training", "test", "gold"];
    const pairedTraining = Array.isArray(data.paired_training) ? data.paired_training : [];
    const humanInputOperation = operation.kind === "human_input";
    const operationForArtifact = !humanInputOperation && operation.artifact_id === artifactId;
    const initialPurpose = operationForArtifact && purposes.includes(operation.purpose)
      ? operation.purpose : "training";
    const initialParent = operationForArtifact && initialPurpose !== "training"
      && pairedTraining.some(item => item.artifact_id === operation.paired_training)
      ? operation.paired_training : "";
    const purpose = select(section, "数据用途", "local-dataset-purpose", [
      ["training", "训练"], ["test", "测试"], ["gold", "Gold 评估"],
    ], initialPurpose);
    const selectionNote = el("p", null, "small muted");
    const parentOptions = [["", "不关联训练数据集"], ...pairedTraining.map(item => [
      item.artifact_id,
      `${String(item.artifact_id).slice(0, 16)} · ${count(item.records)} 条记录`,
    ])];
    const parent = select(section, "关联的本机训练数据集（可选；仅测试或 Gold）",
      "local-dataset-paired-training", parentOptions, initialParent);
    const parentApplies = () => purpose.value !== "training";
    parent.disabled = !parentApplies();
    const selectedParent = () => parentApplies() && parent.value ? parent.value : null;
    const validParent = () => !selectedParent()
      || pairedTraining.some(item => item.artifact_id === selectedParent());
    const sameSelection = () => operationForArtifact
      && operation.purpose === purpose.value
      && (operation.paired_training ?? null) === selectedParent();
    const changed = () => {
      parent.disabled = !parentApplies();
      if (confirmButton) {
        confirmButton.disabled = true;
        selectionNote.textContent = "用途或训练配对已改变；旧预览失效，请重新检查后再确认。";
      }
    };
    purpose.onchange = changed;
    parent.onchange = changed;
    section.append(selectionNote);

    const pending = operation.status === "pending";
    const publishing = pending && hex(operation.preview_id, 32);
    const failedOrInterrupted = operationForArtifact
      && ["failed", "interrupted"].includes(operation.status);
    const recoveryRequired = ["failed", "interrupted"].includes(operation.status)
      && (operation.recovery_available === true || operation.error_code === "publication_recovery_required");
    if (pending) {
      section.append(el("p", operationForArtifact
        ? publishing
          ? "正在创建这份数据集；可刷新查看进度，不会重复提交。"
          : "正在检查这份录制；可刷新查看进度，不会重复提交。"
        : publishing
          ? "本机另一项数据集正在创建；等待其明确结果后再检查当前录制。"
          : "本机另一项数据集检查正在进行；等待其明确结果后再检查当前录制。", "small muted"));
    } else if (recoveryRequired && !operationForArtifact) {
      section.append(el("p", "另一份录制的创建结果尚未确认；请先返回该录制核对或恢复用途记录。", "small muted"));
      if (hex(operation.artifact_id))
        section.append(link("打开待核对的录制", route("local-workspace", operation.artifact_id)));
    } else if (recoveryRequired) {
      section.append(el("p", operation.recovery_available === true
        ? "上次创建结果尚未核对；请先点击“核对上次创建结果”，不要重新检查。"
        : "上次创建结果需要恢复用途记录后才能继续；请勿重新检查或重建。", "small muted"));
    } else if (failedOrInterrupted) {
      section.append(el("p", "上次检查失败或中断，不会自动重试。确认当前用途后，可明确点击重新检查。", "small muted"));
      if (operation.error_code) section.append(technical({error_code: operation.error_code}, "查看检查错误"));
    }

    if (operation.status === "preview_ready" && !sameSelection()) {
      section.append(el("p", humanInputOperation
        ? "当前共享预览属于操作标签数据集，不是完整决策检查；不会用于此用途。"
        : "上次检查对应另一份录制或用途选择；旧预览不能用于当前选择。请明确重新检查。", "small muted"));
    }
    let confirmButton = null;
    if (operationForArtifact && ["failed", "interrupted"].includes(operation.status)
        && operation.recovery_available === true && hex(operation.preview_id, 32)
        && sameSelection()) {
      const recoveryOptions = {primary:true, disabled:!data.csrf_token};
      section.append(command(ctx, "recover-local-dataset-publication", "核对上次创建结果", async () => {
        if (!operationForArtifact || !["failed", "interrupted"].includes(operation.status)
            || operation.recovery_available !== true || !sameSelection()
            || !hex(operation.preview_id, 32) || !data.csrf_token) return;
        recoveryOptions.disabled = true;
        await request(ctx, "/api/local-datasets/publish", {preview_id: operation.preview_id}, data.csrf_token);
        await reload(ctx);
      }, recoveryOptions));
    }
    if (operation.status === "preview_ready" && sameSelection()) {
      section.append(fields([
        ["预览保留决策", count(operation.selected)],
        ["数据划分与隔离状态", splitLabel(operation.split_status)],
      ]));
      section.append(technical({split_status: operation.split_status ?? null}, "查看划分状态代码"));
      if (operation.exclusions && typeof operation.exclusions === "object")
        section.append(table(["排除原因", "数量"], Object.entries(operation.exclusions).map(
          ([reason, amount]) => [reason, count(amount)],
        )));
      if (operation.can_publish === true && hex(operation.preview_id, 32)) {
        const confirmOptions = {primary:true, disabled:!data.csrf_token};
        confirmButton = command(ctx, "publish-local-dataset", "确认创建数据集", async () => {
          if (!sameSelection() || operation.can_publish !== true || !hex(operation.preview_id, 32)
              || !data.csrf_token) return;
          confirmOptions.disabled = true;
          await request(ctx, "/api/local-datasets/publish", {preview_id: operation.preview_id}, data.csrf_token);
          await reload(ctx);
        }, confirmOptions);
        section.append(confirmButton);
      } else {
        section.append(el("p", "后端尚未确认此选择符合用途隔离条件，当前不能创建数据集。", "small muted"));
        const blockerLabel = localDatasetBlockerLabel(operation.error_code);
        if (blockerLabel) section.append(el("p", blockerLabel, "small muted"));
        if (operation.error_code)
          section.append(technical({error_code: operation.error_code}, "查看用途限制代码"));
      }
    }
    if (operation.status === "completed" && sameSelection()) {
      if (hex(operation.result_artifact_id)) {
        section.append(el("p", "本机数据集已创建。", "small muted"));
        section.append(link("打开本机数据集", route("local-workspace", operation.result_artifact_id)));
      } else {
        section.append(el("p", "创建状态已完成，但结果身份与当前检查不匹配；请刷新本机状态核对。", "small muted"));
      }
    }
    if (operation.status === "interrupted" && operationForArtifact)
      section.append(el("p", "上次操作中断，结果尚未确认。刷新只读取状态；不要自动重发。", "small muted"));

    const checkOptions = {primary:true, disabled:pending || recoveryRequired || !validParent() || !data.csrf_token};
    section.append(command(ctx, "check-local-dataset",
      recoveryRequired
        ? !operationForArtifact ? "先处理另一份创建"
          : operation.recovery_available === true ? "先核对上次创建结果" : "需恢复用途记录"
        : failedOrInterrupted
        ? "重新检查数据集" : pending ? publishing ? "正在创建" : "正在检查" : "检查数据集", async () => {
      if (pending || recoveryRequired || !validParent()) return;
      checkOptions.disabled = true;
      await request(ctx, "/api/local-datasets/preview", {
        artifact_id: artifactId,
        purpose: purpose.value,
        paired_training: selectedParent(),
      }, data.csrf_token);
      await reload(ctx);
    }, checkOptions));
    section.append(command(ctx, "refresh-local-dataset-status", publishing ? "刷新创建状态" : "刷新检查状态", async () => {
      await reload(ctx);
    }, {type:"secondary"}));
    return section;
  }

  async function localHumanDatasetCard(ctx, candidates) {
    const section = panel("从录制创建操作标签数据集",
      "从已验证录制中选择操作标签，可保存为本机训练用途数据集。这些标签不是完整动作记录，训练分组仍由训练服务单独检查。选择可跨页保留，最多 256 份。");
    const selectionKey = "local-human-dataset-source-ids";
    const savedSelection = drafts.get(selectionKey);
    const selected = new Set(Array.isArray(savedSelection)
      ? savedSelection.filter((id, index) => hex(id) && savedSelection.indexOf(id) === index).slice(0, 256)
      : []);
    let previewButton = null;
    let canPreview = () => false;
    const currentIds = () => [...selected];
    const sameSelection = operation => {
      const chosen = currentIds();
      return operation?.kind === "human_input"
        && operation.purpose === "training"
        && Array.isArray(operation.artifact_ids)
        && operation.artifact_ids.length === chosen.length
        && chosen.every((id, index) => operation.artifact_ids[index] === id)
        && chosen.length > 0
        && (operation.artifact_id == null || operation.artifact_id === chosen[0]);
    };
    const sameHumanResult = operation => sameSelection(operation) && operation.sample_type === "human_input";
    const selectors = new Map();
    for (const item of candidates) {
      const label = el("label", null, "project-check");
      const checkbox = el("input");
      checkbox.type = "checkbox";
      checkbox.name = "local-human-source";
      checkbox.checked = selected.has(item.artifact_id);
      checkbox.dataset.action = "select-local-human-source";
      label.append(checkbox, el("span", `加入 · ${item.artifact_id.slice(0, 16)}`));
      selectors.set(item.artifact_id, label);
      checkbox.onchange = () => {
        if (checkbox.checked && !selected.has(item.artifact_id) && selected.size >= 256) {
          checkbox.checked = false;
          selectionLimit.textContent = "一次最多选择 256 份录制；当前选择未更改。";
          return;
        }
        if (checkbox.checked) selected.add(item.artifact_id);
        else selected.delete(item.artifact_id);
        drafts.set(selectionKey, currentIds());
        updateSelectionSummary();
        if (previewButton) previewButton.disabled = !canPreview();
      };
    }
    const selectedOnPage = () => candidates.filter(item => selected.has(item.artifact_id)).length;
    const selectionSummary = el("p", null, "small muted");
    const selectionLimit = el("p", null, "small muted");
    const updateSelectionSummary = () => {
      selectionSummary.textContent = `已选 ${selected.size} 份录制（本页 ${selectedOnPage()} 份）。`;
      selectionLimit.textContent = "";
      for (const [artifactId, label] of selectors) {
        const checkbox = label.children[0];
        checkbox.checked = selected.has(artifactId);
      }
      clearButton.disabled = selected.size === 0;
    };
    const clearButton = el("button", "清空所选录制", "button");
    clearButton.type = "button";
    clearButton.dataset.action = "clear-human-input-selection";
    clearButton.disabled = selected.size === 0;
    clearButton.onclick = () => {
      if (clearButton.disabled) return;
      selected.clear();
      drafts.delete(selectionKey);
      updateSelectionSummary();
      if (previewButton) previewButton.disabled = !canPreview();
    };
    updateSelectionSummary();
    section.append(selectionSummary, selectionLimit, clearButton);

    let status;
    try {
      status = await request(ctx, "/api/local-datasets/status");
    } catch {
      section.append(el("p", "本机数据集状态暂不可用；录制仍可浏览，尚未创建预览。", "small muted"));
      return {section, selectors};
    }
    if (status.schema !== "stpd/local-dataset-operation-v1") {
      section.append(el("p", "本机数据集状态格式未知；尚未创建预览。", "small muted"));
      return {section, selectors};
    }
    if (status.availability !== "ready") {
      const message = status.availability === "workspace_required"
        ? "本机资料空间尚未建立；请先在资料目录建立空间。"
        : status.availability === "preparation_required"
          ? "本机用途记录尚未准备；请先在资料目录准备用途记录。"
          : status.availability === "recovery_required"
            ? "本机用途记录需要恢复核对；完成前不能创建预览。"
            : "本机数据集服务暂不可用；尚未创建预览。";
      section.append(el("p", message, "small muted"));
      return {section, selectors};
    }
    const operation = status.operation && typeof status.operation === "object"
      ? status.operation : {status:"idle"};
    const matching = sameSelection(operation);
    const matchingResult = sameHumanResult(operation);
    const pending = operation.status === "pending";
    const recoveryRequired = ["failed", "interrupted"].includes(operation.status)
      && (operation.recovery_available === true || operation.error_code === "publication_recovery_required");
    const hasCsrf = typeof status.csrf_token === "string" && Boolean(status.csrf_token);
    canPreview = () => hasCsrf && currentIds().length >= 1 && currentIds().length <= 256
      && !pending && !recoveryRequired;

    if (pending) {
      section.append(el("p", matching
        ? "正在检查所选操作标签来源；可刷新查看共享任务状态，不会重复提交。"
        : "本机另一项数据集预览或创建正在处理；结束前不能启动新的预览。", "small muted"));
    } else if (recoveryRequired) {
      section.append(el("p", matching
        ? "上一次保存结果需要核对；不会自动重发。"
        : "本机另一项数据集操作需要恢复核对；完成前不能启动新的预览。", "small muted"));
      if (matchingResult && operation.recovery_available === true && hex(operation.preview_id, 32) && hasCsrf) {
        section.append(command(ctx, "recover-human-dataset-publish", "核对并保存已预览数据集", async () => {
          if (!sameHumanResult(operation) || !operation.can_publish || !hex(operation.preview_id, 32)) return;
          await request(ctx, "/api/local-datasets/publish", {preview_id:operation.preview_id}, status.csrf_token);
          await reload(ctx);
        }, {primary:true}));
      } else if (matching && operation.recovery_available === true) {
        section.append(el("p", "上次操作身份信息不完整，不能确认或保存；请刷新状态核对。", "small muted"));
      }
    } else if (operation.status === "preview_ready" && matchingResult) {
      const accepted = Number.isSafeInteger(operation.accepted_labels) && operation.accepted_labels >= 0
        ? count(operation.accepted_labels) : "未知";
      section.append(fields([
        ["已接受操作标签", accepted],
        ["训练划分状态", operation.split_status === "not_checked_for_training"
          ? "尚未检查；不表示独立训练分组已就绪" : "未知"],
      ]));
      if (operation.error_code === "independent_groups_required")
        section.append(el("p", localDatasetBlockerLabel(operation.error_code), "small muted"));
      if (operation.can_publish === true && hex(operation.preview_id, 32) && hasCsrf) {
        section.append(el("p", "当前预览可以保存为操作标签数据集；训练资格仍由训练服务单独检查。", "small muted"));
        section.append(command(ctx, "publish-human-input-dataset", "确认保存操作标签数据集", async () => {
          if (!sameHumanResult(operation) || operation.can_publish !== true || !hex(operation.preview_id, 32)) return;
          await request(ctx, "/api/local-datasets/publish", {preview_id:operation.preview_id}, status.csrf_token);
          await reload(ctx);
        }, {primary:true}));
      } else {
        section.append(el("p", localDatasetBlockerLabel(operation.error_code)
          || "当前预览未确认可保存；不会更改用途或训练资格。", "small muted"));
      }
    } else if (operation.status === "preview_ready" && matching) {
      section.append(el("p", "预览缺少完整的操作标签来源信息，不能用于当前选择。", "small muted"));
    } else if (operation.status === "preview_ready") {
      section.append(el("p", operation.kind === "human_input"
        ? "当前共享预览对应另一组录制；不会用于当前选择。"
        : "当前共享预览属于完整决策数据集检查；不会用于操作标签数据集。", "small muted"));
    } else if (operation.status === "completed" && matchingResult && hex(operation.result_artifact_id)) {
      section.append(el("p", "操作标签数据集已保存；训练分组尚未确认，也没有自动启动训练。", "small muted"));
      section.append(link("打开操作标签数据集", route("local-workspace", operation.result_artifact_id)));
    } else if (["failed", "interrupted"].includes(operation.status) && matching) {
      section.append(el("p", "上次操作失败或中断；不会自动重试。确认选择后可明确重新检查。", "small muted"));
      if (operation.error_code === "independent_groups_required")
        section.append(el("p", localDatasetBlockerLabel(operation.error_code), "small muted"));
    }

    previewButton = command(ctx, "preview-human-input-dataset", "预览所选操作标签", async () => {
      const artifactIds = currentIds();
      if (!canPreview() || artifactIds.some(id => !hex(id))) return;
      await request(ctx, "/api/local-datasets/human-preview", {artifact_ids:artifactIds}, status.csrf_token);
      await reload(ctx);
    }, {primary:true, disabled:!canPreview()});
    if (!hasCsrf) section.append(el("p", "本机浏览器保护令牌暂不可用；请刷新后查看。", "small muted"));
    section.append(previewButton);
    section.append(command(ctx, "refresh-human-input-dataset-status", "刷新操作标签数据集状态", async () => {
      await reload(ctx);
    }, {type:"secondary"}));
    return {section, selectors};
  }

  async function refreshEnvironment() {
    const saved = environmentSnapshot;
    if (!saved || !live(saved.ctx)) return false;
    let values;
    try {
      values = await Promise.all([
        "/api/local-environment", "/api/local-environment/scenes",
        "/api/local-environment/reports", "/api/local-environment/comparisons",
      ].map(path => request(saved.ctx, path)));
    } catch (error) {
      if (environmentSnapshot === saved) environmentSnapshot = null;
      throw error;
    }
    // Preserve the mounted view while owner facts are unchanged. In particular,
    // polling must not discard expanded immutable reports or unfinished forms.
    return environmentSnapshot === saved && live(saved.ctx) &&
      JSON.stringify(values) === saved.signature;
  }

  async function localEnvironment(ctx) {
    const box = el("div", null, "project-page");
    const data = await request(ctx, "/api/local-environment");
    if (data.schema !== "stpd/local-managed-environment-v1" ||
        !Array.isArray(data.scenarios) || !data.session) throw new Error("request_unavailable");
    const scenario = data.scenarios.find(item => item && typeof item.id === "string" &&
      typeof item.seed === "string") || null;
    box.append(panel("环境与场景", "固定种子会重新开一局；若已有运行环境，继续当前环境只会重新连接同一局，不重置或重放。这不是房间存档或决策点恢复。此入口提供人工文字单步，不启动模型。新局由当前 Host 管理环境运行。"));
    const session = data.session;
    const ready = data.availability === "configured";
    const setup = panel("本机游戏环境", ready
      ? `已配置 ${data.input_profile}。启动时仍会复核安装包、候选和游戏身份。`
      : "环境未准备。请由本机环境维护者完成受信任的精确包与候选配置；此页面不接受文件路径或命令。");
    box.append(setup);
    const csrf = typeof data.csrf_token === "string" && data.csrf_token ? data.csrf_token : "";
    const savedScenes = await request(ctx, "/api/local-environment/scenes");
    const sceneLibrary = panel("已保存的固定种子起点", "每次从起点启动都会在 Host 管理的运行中开始新局；保存的是固定种子、输入格式与构建身份，不是游戏存档。");
    if (!scenario) sceneLibrary.append(el("p", "当前没有可用场景定义；起点创建和启动暂不可用。已保存报告与当前实例操作仍可查看。", "small muted"));
    const draftKey = `environment:${ctx.scope}`;
    const sceneName = input(sceneLibrary, "起点名称", "environment-scene-name",
      drafts.get(`${draftKey}:name`) ?? "故障机器人 A0 固定种子");
    sceneName.maxLength = 80;
    sceneName.oninput = () => {
      if (live(ctx)) drafts.set(`${draftKey}:name`, sceneName.value);
    };
    const sceneSeed = input(sceneLibrary, "开局种子", "environment-scene-seed",
      drafts.get(`${draftKey}:seed`) ?? scenario?.seed ?? "");
    sceneSeed.maxLength = 64;
    sceneSeed.autocomplete = "off";
    sceneSeed.spellcheck = false;
    sceneSeed.disabled = !scenario;
    sceneSeed.oninput = () => {
      if (live(ctx)) drafts.set(`${draftKey}:seed`, sceneSeed.value);
    };
    sceneLibrary.append(command(ctx, "environment-scene-save", "保存当前开局配置", async () => {
      await request(ctx, "/api/local-environment/scenes/save", {
        name:sceneName.value.trim(), seed:sceneSeed.value,
      }, csrf);
      await reload(ctx);
    }, {disabled:!ready || !csrf || !scenario}));
    for (const item of savedScenes.items || []) {
      if (!hex(item.artifact_id)) continue;
      const row = panel(item.name, `${item.input_profile} · 种子 ${item.seed} · ${item.artifact_id.slice(0, 12)}`);
      row.append(command(ctx, `environment-scene-start-${item.artifact_id}`, "从此起点新开一局", async () => {
        await request(ctx, "/api/local-environment/start", {
          scenario_id:scenario?.id, scene_artifact_id:item.artifact_id,
        }, csrf);
        await reload(ctx);
      }, {disabled:!ready || !csrf || !scenario || !["idle", "stopped", "stopped_outcome_unknown", "failed"].includes(data.session.status)}));
      sceneLibrary.append(row);
    }
    box.append(sceneLibrary);
    const sessionId = typeof session.session_id === "string" && /^[a-f0-9]{32}$/.test(session.session_id)
      ? session.session_id : null;
    const currentStatus = session.status;
    box.append(panel("当前实例", {
      idle:"尚未启动本机游戏环境。", starting:"正在准备 Host 环境与新局，请刷新状态。",
      resuming:"正在核对并连接当前游戏环境；不会重置或重放。",
      control_held:"已连接当前环境；模型或其他客户端正在控制，人工菜单不可用。",
      active:"当前游戏环境可用；每次人工动作都要明确选择当前菜单。",
      submitting:"动作已提交，结果尚未确定；请刷新状态，不要重试。",
      stopping:"正在停止；结束结果尚待确认。",
      stopped:"当前操作已结束。",
      stopped_outcome_unknown:"本段结果未知；不要重试先前动作。可查看报告或显式继续同一游戏环境。",
      failed:"启动或连接失败；请查看诊断和环境状态。",
      unknown:"本段动作或后续页面结果未知；不要重试先前动作。可结束本段，或显式继续同一游戏环境。",
      interrupted_unknown:"工作台上次中断，旧段结果未知；可显式继续已绑定的同一游戏环境，不会重置或重放。",
      cleanup_unknown:"本段清理结果未确认；不要重试游戏动作。请刷新状态后再选择继续或结束本段。",
    }[currentStatus] || "当前实例状态未知，不能操作。"));
    const errorMessage = {
      managed_control_claim_unknown:"Host 控制请求回复未确认；只有管理状态仍能按原请求编号、游戏实例和局面确认归属时，才可显式释放。",
      managed_control_release_unknown:"Host 控制释放回复未确认；可显式按原租约确认释放，先前游戏动作结果仍未知。",
    }[session.error_code];
    if (errorMessage) box.append(el("p", errorMessage, "small muted"));
    if (session.session_semantics === "workbench-segment-v2-host-owned-service") {
      box.append(el("p", "本段只记录本工作台客户端实际执行的操作；结束本段不会关闭游戏环境。可继续当前局，或在无人控制时显式关闭 Host 环境。", "small muted"));
    } else if (["stopped", "stopped_outcome_unknown"].includes(currentStatus)) {
      box.append(el("p", "这是旧版实例记录；当时的停止流程关闭了该 Host 实例。", "small muted"));
    }
    if (session.error_code) box.append(el("p", `诊断：${session.error_code}`, "small muted"));
    if (["stopped", "stopped_outcome_unknown", "failed"].includes(currentStatus) &&
        !session.report_artifact_id) {
      box.append(el("p", "本次报告尚未保存；请明确重试归档。", "small muted"));
    }
    box.append(command(ctx, "environment-refresh", "刷新实例状态", () => reload(ctx)));
    const hostControl = data.host_control;
    if (hostControl?.held === true && ["managed_control_claim_unknown",
        "managed_control_release_unknown"].includes(session.error_code)) {
      box.append(el("p", "确认释放控制不会确认或撤销先前游戏动作；动作结果仍未知。", "small muted"));
      box.append(command(ctx, "environment-recover-control", "确认释放未确认的 Host 控制", async () => {
        await request(ctx, "/api/local-environment/recover-control", {
          session_id:sessionId, ...session.service_binding,
        }, csrf);
        await reload(ctx);
      }, {danger:true, disabled:!csrf || !sessionId || !session.service_binding ||
        hostControl.tainted || hostControl.closed}));
    }
    const recoveredUnknown = session.control_recovery ===
      "released_acknowledged_outcome_still_unknown";
    const canContinue = session.service_binding && hostControl &&
      !["starting", "resuming", "submitting", "stopping"].includes(currentStatus) &&
      (!["idle", "failed"].includes(currentStatus) || recoveredUnknown);
    if (canContinue) {
      box.append(command(ctx, "environment-resume", "继续当前环境", async () => {
        await request(ctx, "/api/local-environment/resume", {
          session_id:sessionId, ...session.service_binding,
        }, csrf);
        await reload(ctx);
      }, {primary:true, disabled:!csrf || hostControl.tainted || hostControl.closed ||
        hostControl.status === "unavailable"}));
    }
    if (hostControl && hostControl.closed !== true &&
        !["starting", "resuming", "active", "control_held", "submitting", "stopping"].includes(currentStatus)) {
      box.append(command(ctx, "environment-close-host", "关闭 Host 环境", async () => {
        await request(ctx, "/api/local-environment/close", {
          ...(sessionId ? {session_id:sessionId} : {}),
          service_instance_id:hostControl.service_instance_id,
          runtime_instance_id:hostControl.runtime_instance_id,
          game_continuity_id:hostControl.game_continuity_id,
        }, csrf);
        await reload(ctx);
      }, {danger:true, disabled:!csrf || hostControl.held ||
        !hostControl.service_instance_id || !hostControl.runtime_instance_id ||
        !hostControl.game_continuity_id ||
        hostControl.status === "unavailable"}));
    }
    if (sessionId && (["starting", "active", "control_held", "resuming", "submitting", "unknown", "interrupted_unknown", "cleanup_unknown"].includes(currentStatus) ||
        (["stopped", "stopped_outcome_unknown", "failed"].includes(currentStatus) && !session.report_artifact_id))) {
      box.append(command(ctx, "environment-stop", currentStatus === "failed" || currentStatus.startsWith("stopped")
        ? "重试归档报告" : "结束本段操作", async () => {
        await request(ctx, "/api/local-environment/stop", {session_id:sessionId}, csrf);
        await reload(ctx);
      }, {danger:true, disabled:!csrf}));
    }
    if (["idle", "stopped", "stopped_outcome_unknown", "failed"].includes(currentStatus)) {
      for (const scenario of data.scenarios) {
        const section = panel(scenario.label, scenario.scope);
        section.append(fields([["起点", `新局固定种子 ${scenario.seed}`], ["角色", scenario.character]]));
        section.append(command(ctx, `environment-start-${scenario.id}`, "启动独占实例", async () => {
          await request(ctx, "/api/local-environment/start", {scenario_id:scenario.id}, csrf);
          await reload(ctx);
        }, {primary:true, disabled:!ready || !csrf}));
        box.append(section);
      }
    }
    const context = session.context;
    const snapshot = context?.snapshot;
    const expectedSnapshotSchema = data.input_profile === "text-menu-v2"
      ? "sts2.player-environment/text-menu-snapshot-2" : "sts2.player-environment/text-menu-snapshot-1";
    if (currentStatus === "active" && hostControl?.held !== true &&
        hostControl?.tainted !== true && hostControl?.closed !== true &&
        snapshot?.schema === expectedSnapshotSchema &&
        snapshot?.input_profile === data.input_profile) {
      const content = snapshot.interaction?.content || {};
      const surface = content.surface || {};
      const run = snapshot.persistent?.content?.run || {};
      const player = snapshot.persistent?.content?.player || {};
      const page = panel("当前文字页", surface.title || surface.kind || snapshot.interaction?.kind || "当前公开页");
      const prompt = surface.description || surface.prompt || snapshot.interaction?.prompt;
      if (prompt) page.append(el("p", prompt));
      page.append(fields([
        ["位置", Number.isSafeInteger(run.floor) ? `第 ${run.floor} 层` : "当前页"],
        ["角色", player.character_name || player.character_definition_id || "未提供"],
        ["生命", Number.isSafeInteger(player.hp) && Number.isSafeInteger(player.max_hp)
          ? `${player.hp} / ${player.max_hp}` : "未提供"],
        ["金币", Number.isSafeInteger(player.gold) ? String(player.gold) : "未提供"],
      ]));
      const position = value => Number.isSafeInteger(value?.col) && Number.isSafeInteger(value?.row)
        ? `(${value.col},${value.row})` : null;
      const map = content.context?.kind === "map" ? content.context : null;
      if (map) {
        const visited = Array.isArray(map.visited) ? map.visited.map(position).filter(Boolean) : [];
        page.append(fields([
          ["地图当前位置", position(map.current_position) || "未提供"],
          ["已走过的节点", visited.length ? visited.join(" → ")
            : Array.isArray(map.visited) ? "无" : "未提供"],
        ]));
      }
      const combat = content.context?.kind === "combat" ? content.context : null;
      const combatPlayer = combat?.player || {};
      if (combat) page.append(fields([
        ["回合", Number.isSafeInteger(combat.round) ? String(combat.round) : "未提供"],
        ["当前行动方", combat.turn_owner || "未提供"],
        ["能量", Number.isSafeInteger(combatPlayer.energy) && Number.isSafeInteger(combatPlayer.max_energy)
          ? `${combatPlayer.energy} / ${combatPlayer.max_energy}` : "未提供"],
        ["格挡", Number.isSafeInteger(combatPlayer.block) ? String(combatPlayer.block) : "未提供"],
        ["牌堆数量", ["draw_pile_count", "discard_pile_count", "exhaust_pile_count"].every(key =>
          Number.isSafeInteger(combatPlayer[key]))
          ? `抽牌 ${combatPlayer.draw_pile_count} · 弃牌 ${combatPlayer.discard_pile_count} · 消耗 ${combatPlayer.exhaust_pile_count}`
          : "未提供"],
      ]));
      const hand = new Map((Array.isArray(combatPlayer.hand) ? combatPlayer.hand : [])
        .filter(card => typeof card?.entity_id === "string").map(card => [card.entity_id, card]));
      const describeReferent = item => {
        const properties = item.properties || {};
        const details = [];
        let label = item.label || item.name || item.role || "公开对象";
        if (map && ["node", "option"].includes(item.role) && position(properties)) {
          label = `${item.role === "option" ? "可选路线" : "地图节点"} ${properties.point_type || "类型未提供"} ${position(properties)}`;
          if (typeof properties.state === "string") details.push(`状态 ${properties.state}`);
          if (Array.isArray(properties.children)) details.push(`连接 ${properties.children.length
            ? properties.children.map(child => `${child.point_type || "类型未提供"} ${position(child) || "位置未提供"}`).join("、")
            : "无"}`);
        }
        if (combat && item.role === "enemy") {
          if (Number.isSafeInteger(properties.hp) && Number.isSafeInteger(properties.max_hp))
            details.push(`生命 ${properties.hp}/${properties.max_hp}`);
          if (Number.isSafeInteger(properties.block)) details.push(`格挡 ${properties.block}`);
          if (Array.isArray(properties.statuses) && properties.statuses.length)
            details.push(`状态 ${properties.statuses.map(value =>
              `${value.name || value.definition_id || "未命名"} ${value.amount ?? ""}${value.description ? `：${value.description}` : ""}`).join("、")}`);
          if (Array.isArray(properties.intents) && properties.intents.length)
            details.push(`意图 ${properties.intents.map(value =>
              [value.label, value.title, value.description].filter(Boolean).join(" · ")).join("、")}`);
        }
        const card = combat && ["hand", "playable_card"].includes(item.role)
          ? hand.get(item.referent_id) : null;
        if (card) {
          if (card.cost != null) details.push(`费用 ${card.cost}`);
          if (card.type) details.push(`类型 ${card.type}`);
          if (card.description) details.push(`说明 ${card.description}`);
          if (card.is_upgraded === true) details.push("已升级");
          if (card.can_play === false) details.push(`当前不可打出${card.unplayable_reason ? `：${card.unplayable_reason}` : ""}`);
        }
        if (item.state?.enabled === false) details.push("当前不可用");
        if (item.state?.selected === true) details.push("已选择");
        if (item.state?.visible === false) details.push("当前不可见");
        return `${label}${details.length ? ` · ${details.join(" · ")}` : ""}`;
      };
      if (Array.isArray(snapshot.referents) && snapshot.referents.length) {
        page.append(el("h3", `当前页公开对象 · ${snapshot.referents.length} 项`));
        const objects = el("ul");
        for (const item of snapshot.referents) {
          objects.append(el("li", describeReferent(item)));
        }
        page.append(objects);
      }
      if (Array.isArray(snapshot.menu?.selection) && snapshot.menu.selection.length) {
        const referentLabels = new Map((snapshot.referents || []).map(item => [item.referent_id, describeReferent(item)]));
        page.append(el("p", `当前选择：${snapshot.menu.selection.map(item =>
          `${item.role} ${referentLabels.get(item.referent_id) || "公开对象"}`).join(" → ")}`));
      }
      page.append(technical(snapshot, "查看完整公开快照与诊断"));
      const menu = snapshot.menu_actions;
      if (menu?.status === "complete" && Array.isArray(menu.actions) &&
          menu.materialized_count === menu.actions.length && menu.total_count === menu.actions.length) {
        page.append(el("h3", `完整菜单 · ${menu.actions.length} 项`));
        for (const [index, item] of menu.actions.entries()) {
          const row = el("div", null, "project-card");
          const actionLabel = item.label || item.verb || "未命名动作";
          row.append(el("strong", actionLabel));
          row.append(el("p", `${item.verb || ""} · ${item.effect_domain || ""}`, "small muted"));
          if (item.arguments?.length) row.append(technical(item.arguments, "查看目标和参数"));
          if (typeof item.action_id === "string" && item.action_id && sessionId) {
            const effect = item.effect_domain === "text_menu" ? "操作文字菜单"
              : item.effect_domain === "native_input" ? "向游戏提交" : "选择当前动作";
            const button = command(ctx, `environment-action-${item.action_id}`, `${effect}：${actionLabel}`, async () => {
              await request(ctx, "/api/local-environment/submit", {
                session_id:sessionId, action_id:item.action_id,
                expected_snapshot_id:snapshot.snapshot_id,
                expected_game_continuity_id:context.game_continuity_id,
              }, csrf);
              await reload(ctx);
            }, {disabled:!csrf});
            button.setAttribute("aria-label", `菜单第 ${index + 1} 项，${effect}：${actionLabel}`);
            row.append(button);
          }
          page.append(row);
        }
      } else page.append(el("p", "当前页没有完整可执行菜单；不会猜测或补全动作。", "small muted"));
      box.append(page);
    }
    const eventCard = (event, key) => {
      const card = panel("动作记录", `${event.result_status || "结果未留存"} · ${event.native_delivery || "无原生交付"}${event.error_code ? ` · ${event.error_code}` : ""}`);
      if (/^[a-f0-9]{64}$/.test(event.event_artifact_id || "")) {
        card.append(command(ctx, `environment-event-${key}`, "查看完整动作 Receipt", async () => {
          const detail = await request(ctx, `/api/local-environment/events/${event.event_artifact_id}`);
          card.append(technical(detail, "完整公开动作记录"));
        }));
      } else card.append(el("p", "完整动作记录未能归档；当前摘要保留可确认的交付状态。", "small muted"));
      return card;
    };
    if (session.events?.length) {
      const recent = session.events.at(-1);
      box.append(eventCard(recent, `current-${recent.request_id}`));
    }
    const reports = await request(ctx, "/api/local-environment/reports");
    const archive = panel("已保留的运行报告", "只记录此工程入口的公开文字页、动作 Receipt 和精确身份；不作为 Human 或模型评估。 ");
    for (const item of reports.items || []) {
      if (/^[a-f0-9]{64}$/.test(item.artifact_id || "")) {
        archive.append(command(ctx, `environment-report-${item.artifact_id}`, `查看 ${item.status} · ${item.artifact_id.slice(0, 12)}`, async () => {
          const report = await request(ctx, `/api/local-environment/reports/${item.artifact_id}`);
          const detail = panel("已归档报告", `${report.status} · ${report.events?.length || 0} 次明确动作`);
          detail.append(technical(report, "查看报告索引与身份"));
          for (const event of report.events || []) detail.append(eventCard(event, `${item.artifact_id}-${event.request_id}`));
          if (report.status === "stopped" && report.input_profile === "text-menu-v2" &&
              report.error_code === null && report.host_package_pin) {
            const save = panel("保存到本机资料", "选择用途后保存这份工程操作记录。不会启动训练或上传，也不会把它标为真人示范。");
            const purpose = select(save, "资料用途", `environment-purpose-${item.artifact_id}`,
              [["", "请选择用途"], ["training", "工程训练"], ["test", "工程测试"]], "");
            const options = {get disabled() {
              return !csrf || !["training", "test"].includes(purpose.value);
            }};
            const button = command(ctx, `environment-import-${item.artifact_id}`, "保存到本机资料", async () => {
              const chosenPurpose = purpose.value;
              if (!["training", "test"].includes(chosenPurpose)) return;
              const saved = await request(ctx, "/api/local-environment/reports/import",
                {report_artifact_id:item.artifact_id, purpose:chosenPurpose}, csrf);
              if (saved.schema !== "stpd/local-managed-source-import-v1" || saved.status !== "admitted" ||
                  saved.report_artifact_id !== item.artifact_id || !hex(saved.artifact_id) ||
                  saved.curation_purpose !== chosenPurpose || saved.scope !== "engineering_control" ||
                  saved.sample_type !== "managed_control_input_stream" || saved.actor !== "unverified")
                throw new Error("request_unavailable");
              save.append(link("打开已保存的资料", route("local-workspace", saved.artifact_id)));
              note(ctx, "已保存工程资料和用途；尚未开始训练。");
            }, options);
            purpose.onchange = () => {button.disabled = options.disabled;};
            save.append(button);
            detail.append(save);
          }
          archive.append(detail);
        }));
      }
    }
    box.append(archive);
    const comparisonDetail = el("div");
    const showComparison = (result, open = true) => {
      if (!live(ctx)) return;
      const saved = {result, open};
      drafts.set(`${draftKey}:comparison`, saved);
      const detail = technical(result, "比较结果与来源");
      detail.open = open;
      detail.dataset.preserve = `environment-comparison-${result.artifact_id}`;
      detail.ontoggle = () => { if (live(ctx)) saved.open = detail.open; };
      comparisonDetail.replaceChildren(detail);
    };
    const savedComparison = drafts.get(`${draftKey}:comparison`);
    if (savedComparison) showComparison(savedComparison.result, savedComparison.open);
    const comparisonSelect = (form, label, name, choices, initial) => {
      const saved = drafts.get(`${draftKey}:${name}`);
      const control = select(form, label, name, choices,
        choices.some(([key]) => key === saved) ? saved : initial);
      control.onchange = () => {
        if (live(ctx)) drafts.set(`${draftKey}:${name}`, control.value);
      };
      return control;
    };
    if ((savedScenes.items || []).length && (reports.items || []).length >= 2) {
      const compare = panel("比较两次独立开局", "只核对同一保存起点下两份已关闭报告的种子、实例身份、首屏菜单和动作数量；不判断策略或轨迹相同。");
      const sceneChoice = comparisonSelect(compare, "保存的起点", "environment-compare-scene",
        savedScenes.items.filter(item => hex(item.artifact_id)).map(item => [item.artifact_id, item.name]),
        savedScenes.items[0].artifact_id);
      const choices = reports.items.filter(item => hex(item.artifact_id)).map(item =>
        [item.artifact_id, `${item.status} · ${item.artifact_id.slice(0, 12)}`]);
      const first = comparisonSelect(compare, "第一份报告", "environment-compare-first", choices, choices[0][0]);
      const second = comparisonSelect(compare, "第二份报告", "environment-compare-second", choices, choices[1][0]);
      compare.append(command(ctx, "environment-compare-save", "保存并查看比较", async () => {
        const result = await request(ctx, "/api/local-environment/compare", {
          scene_artifact_id:sceneChoice.value,
          report_artifact_ids:[first.value, second.value],
        }, csrf);
        showComparison(result);
      }, {disabled:!csrf}));
      box.append(compare);
    }
    const comparisons = await request(ctx, "/api/local-environment/comparisons");
    if ((comparisons.items || []).length) {
      const history = panel("已保存的开局比较", "每份比较都有不可变起点和两份运行报告作为来源。");
      for (const item of comparisons.items) {
        if (!hex(item.artifact_id)) continue;
        history.append(command(ctx, `environment-comparison-${item.artifact_id}`,
          `查看比较 · ${item.artifact_id.slice(0, 12)}`, async () => {
            const result = await request(ctx, `/api/local-environment/comparisons/${item.artifact_id}`);
            showComparison(result);
          }));
      }
      box.append(history);
    }
    box.append(comparisonDetail);
    if (live(ctx)) environmentSnapshot = {
      ctx, signature:JSON.stringify([data, savedScenes, reports, comparisons]),
    };
    return box;
  }

  async function localWorkspace(ctx) {
    const box = el("div", null, "project-page");
    const id = new URLSearchParams(ctx.search).get("id");
    if (!id)
      box.append(panel("本机资料", "浏览本机资料来源。此页在本机读取，不需要云端登录；开始任何导入或训练都需要独立的明确操作。"));
    await managedWorkspaceCard(ctx, box, {detail:Boolean(id)});
    if (id) {
      const value = await request(ctx, `/api/local-workspace/artifacts/${encodeURIComponent(id)}`);
      box.append(command(ctx, "local-workspace-back", "返回本机资料目录", () => {
        offsets.set("local-workspace", 0);
        window.SpireProject.navigate("local-workspace");
      }, {type:"secondary"}));
      const candidateName = value.parameters?.display_name || value.parameters?.name || value.parameters?.title;
      const heading = typeof candidateName === "string" && candidateName.trim()
        ? candidateName.trim().slice(0, 120)
        : value.kind === "evidence" && value.parameters?.schema === "stpd/local-verified-bundle-v1"
          ? "录制来源" : show(value.kind);
      box.append(panel(heading, `本机对象 · ${value.artifact_id.slice(0, 16)}`));
      const facts = localDataFacts(value.data_facts, value);
      if (facts) box.append(facts);
      if (value.kind === "dataset" && value.parameters?.schema === "stpd/curated-decision-dataset-v1")
        box.append(localDatasetOverview(value));
      if (value.kind === "dataset" && value.parameters?.schema === "stpd/managed-text-menu-observed-source-v1") {
        const managed = panel("工程操作资料", "来源是已封存的 Managed 运行报告，操作者身份未验证。它不是已验证真人示范，也不证明模型水平。");
        let trainingBinding = false;
        try {
          const binding = await request(ctx, `/api/local-managed-sources/binding/${value.artifact_id}`);
          if (binding.schema !== "stpd/local-managed-source-binding-v1" || binding.status !== "admitted" ||
              binding.artifact_id !== value.artifact_id || binding.scope !== "engineering_control" ||
              binding.sample_type !== "managed_control_input_stream" || binding.actor !== "unverified" ||
              !["training", "test"].includes(binding.curation_purpose) ||
              !Number.isSafeInteger(binding.event_count) || binding.event_count < 1)
            throw new Error("request_unavailable");
          managed.append(fields([["用途", binding.curation_purpose === "training" ? "工程训练" : "工程测试"],
            ["已记录操作", count(binding.event_count)]]));
          trainingBinding = binding.curation_purpose === "training";
          managed.append(el("p", "这里仅登记资料用途；未自动启动训练。", "small muted"));
        } catch {
          managed.append(el("p", "本机用途暂未核对成功；资料仍保留。", "small muted"));
        }
        box.append(managed);
        if (trainingBinding) box.append(await localTrainingCard(ctx, value));
      }
      if (value.kind === "model" && ["stpd/stage1a-model-v1",
          "stpd/experimental-m2-model-v1",
          "stpd/stage1a-light-action-m0-public-model-v1"].includes(value.parameters?.schema))
        box.append(localModelOverview(value));
      if (value.kind === "model" && value.parameters?.schema === "stpd/experimental-m2-model-v1"
          && memoryModelVariant(value) !== null
          && (memoryRecipePageProfile(memoryModelVariant(value)) === "text-menu-v1"
              || memoryModelVariant(value).profile === "text-menu-v2-confirmed-interaction"))
        box.append(await localMemoryEvaluationCard(ctx, value));
      if (supportsLocalModelExport(value)) {
        const exportCard = await localModelExportCard(ctx, value);
        if (live(ctx)) box.append(exportCard);
      }
      if (value.kind === "offline_evaluation")
        box.append(await localOfflineEvaluationDetail(ctx, value));
      if (value.kind === "dataset" && value.parameters?.schema === "stpd/curated-decision-dataset-v1"
          && value.parameters?.purpose === "training")
        box.append(await localTrainingCard(ctx, value));
      if (value.kind === "dataset" && value.parameters?.schema === "stpd/human-text-input-source-v1") {
        const bindingCard = panel("操作标签训练入口", "只在本机用途账本明确登记此来源用于训练后显示训练入口；操作标签不补成完整决策。");
        let binding = null;
        try {
          if (hex(value.artifact_id))
            binding = await request(ctx, `/api/local-datasets/binding/${value.artifact_id}`);
        } catch {
          // An unavailable binding is not proof that the source has training use.
        }
        if (binding?.schema === "stpd/local-dataset-binding-v1"
            && binding.artifact_id === value.artifact_id
            && binding.sample_type === "human_input"
            && binding.curation_purpose === "training") {
          box.append(await localTrainingCard(ctx, value));
        } else {
          bindingCard.append(el("p", binding?.schema === "stpd/local-dataset-binding-v1"
            && binding.artifact_id === value.artifact_id && binding.sample_type === "human_input"
            ? "本机用途账本尚未确认训练用途；当前不显示训练入口。"
            : "无法确认此对象的本机用途登记；当前不显示训练入口。", "small muted"));
          box.append(bindingCard);
        }
      }
      box.append(technical(value, "查看来源详情与内容文件摘要"));
      if (value.kind === "evidence" && value.parameters?.schema === "stpd/local-verified-bundle-v1") {
        const preview = panel("本机样本预览", "选择这份已导入录制后，明确检查其中的样本。不会自动创建数据集。");
        const status = await request(ctx, "/api/local-recordings/preview/status");
        const matched = status.artifact_id === value.artifact_id;
        if (matched && status.status === "pending") {
          preview.append(el("p", "正在检查这份录制。", "small muted"));
        } else if (matched && status.status === "completed") {
          if (status.availability === "archival_format") {
            preview.append(el("p", "旧版归档未提供当前输入标签与完整决策计数，无法给出样本预览。", "small muted"));
          } else {
            preview.append(el("p", `操作标签 ${status.human_input_labels} 条 / 完整决策 ${status.canonical_decisions} 条`, "small muted"));
            preview.append(technical({
              输入记录总数: status.human_input_total,
              输入排除原因: status.human_input_exclusions,
              决策侧观察项: status.decision_exclusions,
              观察到的run数量: status.run_ids_observed,
              独立run资格: status.independent_run_qualification === "insufficient_canonical_decisions" ? "完整决策不足" : "未知",
              说明: "决策侧计数可能重叠；输入标签不能充当完整轨迹或已执行动作。",
            }, "查看统计与排除原因"));
          }
          preview.append(el("p", "样本预览本身不会创建数据集；如已检查用途，创建结果显示在下方用途检查中。", "small muted"));
        } else if (matched && status.status === "failed") {
          preview.append(el("p", "归档预览核验失败，未产生样本结果。", "small muted"));
          preview.append(technical({error_code: status.error_code}, "查看核验错误"));
        }
        if (matched && status.status === "completed" && status.availability === "available") {
          if (Number.isInteger(status.canonical_decisions) && status.canonical_decisions > 0) {
            preview.append(await localDatasetCard(ctx, value.artifact_id));
          } else {
            preview.append(el("p", "这份录制没有可用于数据集检查的完整决策。操作输入标签不会补成完整决策。", "small muted"));
          }
        }
        preview.append(command(ctx, "refresh-local-recording-preview", status.status === "pending" ? "刷新进度" : "刷新预览状态", async () => {
          await reload(ctx);
        }, {type:"secondary"}));
        preview.append(command(ctx, "preview-local-recording", matched && status.status === "completed" ? "重新检查" : "预览样本", async () => {
          await request(ctx, "/api/local-recordings/preview", {artifact_id: value.artifact_id}, status.csrf_token);
          await reload(ctx);
        }, {disabled: status.status === "pending" || !status.csrf_token}));
        box.append(preview);
      }
      return box;
    }

    const importStatus = await request(ctx, "/api/local-recordings/import/status");
    box.append(localRecordingCard(ctx, importStatus));
    box.append(localMemberArchiveCard(ctx, importStatus, importStatus.csrf_token));

    const query = drafts.get("local-workspace-search") || "";
    const selectedKind = drafts.get("local-workspace-kind") || "";
    const categories = [
      ["recordings", "录制"],
      ["datasets", "数据集"],
      ["models", "模型"],
      ["reports", "报告"],
      ["all", "全部"],
    ];
    const requestedCategory = drafts.get("local-workspace-category") || "all";
    const selectedCategory = categories.some(([value]) => value === requestedCategory)
      ? requestedCategory : "all";
    const categoryNav = el("div", null, "project-actions");
    for (const [value, label] of categories) {
      categoryNav.append(command(ctx, `local-workspace-category-${value}`, label, async () => {
        drafts.set("local-workspace-category", value);
        drafts.set("local-workspace-kind", "");
        offsets.set("local-workspace", 0);
        await reload(ctx);
      }, {primary:value === selectedCategory}));
    }
    box.append(categoryNav);
    const filters = el("div", null, "project-form");
    filters.dataset.projectEditor = "local-workspace-search";
    const search = input(filters, "搜索本机对象名称或对象 ID", "local-workspace-search", query);
    search.maxLength = 128;
    const kindOptions = [
      ["", "全部类型"],
      ...["evidence", "dataset", "model_view", "feature_set", "feature_job", "training_input",
        "experiment", "run", "checkpoint", "model", "offline_evaluation", "live_evaluation",
        "performance", "run_event", "run_result", "gold_tasks", "gold_labels", "protocol", "analysis"]
        .map(kind => [kind, show(kind)]),
    ];
    const kind = select(filters, "类型", "local-workspace-kind", kindOptions, selectedKind);
    kind.onchange = () => {
      drafts.set("local-workspace-kind", kind.value);
      offsets.set("local-workspace", 0);
      reload(ctx);
    };
    search.addEventListener("keydown", event => {
      if (event.key === "Enter") {
        event.preventDefault();
        drafts.set("local-workspace-search", search.value.trim());
        offsets.set("local-workspace", 0);
        reload(ctx);
      }
    });
    filters.append(command(ctx, "search-local-workspace", "搜索", async () => {
      drafts.set("local-workspace-search", search.value.trim());
      offsets.set("local-workspace", 0);
      await reload(ctx);
    }));
    box.append(filters);
    const limit = 25, offset = offsets.get("local-workspace") || 0;
    const params = new URLSearchParams({limit:String(limit), offset:String(offset)});
    if (selectedCategory !== "all") params.set("category", selectedCategory);
    if (query.trim()) params.set("q", query.trim());
    if (selectedKind) params.set("kind", selectedKind);
    const data = await request(ctx, `/api/local-workspace?${params}`);
    if (data.status === "not_configured") {
      box.append(empty(
        "尚未建立本机资料空间",
        "新建空间后，你可以在这里浏览和管理本机资料。",
      ));
      return box;
    }
    if (data.status === "unavailable") {
      box.append(empty("本机资料暂不可用", "登记的本机资料库或索引当前无法读取；原有文件不会被修改。"));
      if (data.error_code) box.append(technical(data, "查看本机读取状态"));
      return box;
    }
    const categoryLabel = categories.find(([value]) => value === selectedCategory)?.[1] || "全部";
    box.append(el("p", `${categoryLabel} · 共 ${count(data.total)} 项`, "small muted"));

    const humanSources = (data.items || []).filter(item => item?.kind === "evidence"
      && item.parameters?.schema === "stpd/local-verified-bundle-v1" && hex(item.artifact_id));
    const savedHumanSelection = drafts.get("local-human-dataset-source-ids");
    const hasSavedHumanSelection = Array.isArray(savedHumanSelection) && savedHumanSelection.some(id => hex(id));
    const humanDataset = humanSources.length || hasSavedHumanSelection
      ? await localHumanDatasetCard(ctx, humanSources) : null;
    const rows = [];
    for (const item of data.items || []) {
      const candidateName = item.parameters?.display_name || item.parameters?.name || item.parameters?.title;
      const name = typeof candidateName === "string" && candidateName.trim()
        ? candidateName.trim().slice(0, 120) : show(item.kind);
      const title = link(`${name} · ${item.artifact_id.slice(0, 16)}`, route("local-workspace", item.artifact_id));
      const payloadCount = (item.payloads || []).length;
      const selectionCell = humanDataset?.selectors.get(item.artifact_id) || "—";
      rows.push([
        title,
        count(payloadCount),
        item.registry_indexed ? (item.registry_cached ? "索引已标记缓存" : "本机索引") : "尚未进入索引",
        technical(item, "查看 metadata 与 payload 摘要"),
        ...(humanDataset ? [selectionCell] : []),
      ]);
    }
    box.append(table(["本机资料", "内容文件", "本机索引", "来源信息",
      ...(humanDataset ? ["加入操作标签集"] : [])], rows));
    if (humanDataset) box.append(humanDataset.section);
    if (!data.total) box.append(empty("没有匹配的本机对象", "可清除搜索词，或先在本机准备研究资料。"));
    const pagerBox = el("div", null, "project-actions");
    if (offset > 0) pagerBox.append(command(ctx, "local-workspace-prev", "上一页", async () => {
      offsets.set("local-workspace", Math.max(0, offset - limit)); await reload(ctx);
    }, {type:"secondary"}));
    if (offset + limit < data.total) pagerBox.append(command(ctx, "local-workspace-next", "下一页", async () => {
      offsets.set("local-workspace", offset + limit); await reload(ctx);
    }, {type:"secondary"}));
    pagerBox.append(el("span", data.total ? `本页 ${offset + 1}–${Math.min(offset + limit, data.total)} / ${data.total}` : "共 0 项", "subtext"));
    box.append(pagerBox);
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
  async function localOfflineEvaluationCatalog(ctx) {
    const box = panel(
      "本机离线评估",
      "已记录的开发集结果是描述性离线统计，不代表游戏通关或模型质量。",
    );
    const limit = 25, offset = offsets.get("local-offline-evaluations") || 0;
    const params = new URLSearchParams({kind:"offline_evaluation", limit:String(limit), offset:String(offset)});
    let data;
    try {
      data = await request(ctx, `/api/local-workspace?${params}`);
    } catch (error) {
      box.append(el("p", "本机离线评估目录暂不可用；读取失败不会当作空列表。", "small muted"));
      box.append(technical({error: error?.message || "unknown"}, "查看读取错误"));
      return box;
    }
    if (data.status === "not_configured") {
      box.append(empty("尚未建立本机资料空间", "建立并准备本机资料空间后，可在这里查看离线评估。"));
      return box;
    }
    if (data.status === "unavailable") {
      box.append(empty("本机离线评估暂不可用", "本机资料索引读取失败；请查看错误详情。"));
      if (data.error_code) box.append(technical({error_code: data.error_code}, "查看读取错误"));
      return box;
    }
    if (data.schema !== "stpd/local-workspace-inventory-v1"
        || !Array.isArray(data.items) || !Number.isSafeInteger(data.total) || data.total < 0) {
      box.append(el("p", "本机离线评估目录格式未知，未按空列表处理。", "small muted"));
      return box;
    }
    if (data.total === 0) {
      box.append(empty("还没有本机离线评估", "完成明确的开发集评估后，已记录结果会显示在这里。"));
      return box;
    }
    const visible = data.items.filter(item => {
      const parameters = item?.parameters && typeof item.parameters === "object"
        ? item.parameters : {};
      return parameters.partition !== "test" && parameters.sealed_test !== true;
    });
    if (visible.length) {
      const rows = visible.map(item => {
        const parameters = item.parameters && typeof item.parameters === "object"
          ? item.parameters : {};
        const schema = typeof parameters.schema === "string" ? parameters.schema : "未知格式";
        const candidateName = parameters.display_name || parameters.name || parameters.title;
        const name = typeof candidateName === "string" && candidateName.trim()
          ? candidateName.trim().slice(0, 120) : "离线评估";
        const identity = hex(item.artifact_id) ? item.artifact_id : null;
        const title = identity
          ? link(`${name} · ${identity.slice(0, 16)}`, route("local-workspace", identity))
          : el("span", name);
        const partition = parameters.partition === "dev" ? "开发集" : "未知";
        return [title, partition, schema, technical(item, "查看评估metadata")];
      });
      box.append(table(["本机评估", "分区", "格式", "metadata"], rows));
    } else {
      box.append(empty("当前页没有可展示的开发集评估", "封存测试评估不在本机列表中展示。"));
    }
    const pagerBox = el("div", null, "project-actions");
    if (offset > 0) pagerBox.append(command(ctx, "local-offline-evaluations-prev", "上一页", async () => {
      offsets.set("local-offline-evaluations", Math.max(0, offset - limit)); await reload(ctx);
    }, {type:"secondary"}));
    if (offset + limit < data.total) pagerBox.append(command(ctx, "local-offline-evaluations-next", "下一页", async () => {
      offsets.set("local-offline-evaluations", offset + limit); await reload(ctx);
    }, {type:"secondary"}));
    pagerBox.append(el("span", `metadata页 ${data.total ? `${offset + 1}–${Math.min(offset + limit, data.total)} / ${data.total}` : "0 项"}`, "subtext"));
    box.append(pagerBox);
    return box;
  }
  async function evaluations(ctx) {
    const box = el("div", null, "project-page");
    if (local) {
      box.append(await localOfflineEvaluationCatalog(ctx));
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
    refresh: async view => view === "datasets" ? refreshDataset() :
      view === "local-environment" ? refreshEnvironment() :
      view === "local-models" && current?.view === view && live(current),
    openDatasetLibrary() {
      drafts.set("dataset-tab", "library");
      if (window.SpireProject.navigate) window.SpireProject.navigate("datasets");
    },
    datasetContext: () => `${datasetTab()}:${drafts.get("dataset-task-id") || ""}`,
    async render(view, identity, mount) {
      if (!supported.has(view)) throw new Error("unsupported_project_view");
      if (["local-workspace", "local-environment"].includes(view) && !local) throw new Error("unsupported_project_view");
      const nextAccount = `${identity?.principal?.subject || "anonymous"}:${identity?.principal?.role || ""}:${identity?.status || ""}`;
      if (account !== nextAccount) {
        account = nextAccount;
        localMemberArchiveSnapshot = null;
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
        view,
        account,
        key: `${account}:${view}:${location.search}`,
        identity,
        search: location.search,
        scope: scope(),
      };
      current = ctx;
      if (modelWatch?.timer !== null && modelWatch?.timer !== undefined) {
        clearTimeout(modelWatch.timer);
        modelWatch.timer = null;
      }
      if (view !== "local-models") stopModelWatch();
      commandControls.clear();
      if (!(["local-models", "local-workspace", "local-environment", "evaluations"].includes(view) || (local && ["campaigns", "collection-overview"].includes(view))) && !signedIn(ctx)) return authNotice(ctx);
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
          "local-environment": localEnvironment,
          "local-workspace": localWorkspace,
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
