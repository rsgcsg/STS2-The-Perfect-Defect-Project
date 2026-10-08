using Godot;
using System.Globalization;
using System.Text.Json;
using static Godot.Control;

namespace STS2PlatformLiveUi;

/// <summary>Five native presentations over one application backend. No worker or job state.</summary>
internal sealed class PlatformNativeWorkbenchPanel : IDisposable
{
    private readonly PlatformNativeWorkbenchClient _client = new();
    private readonly PlatformNativeWorkbenchCommands _commands = new();
    private readonly CancellationTokenSource _lifetime = new();
    private readonly Dictionary<string, string> _drafts = new(StringComparer.Ordinal);
    private readonly Dictionary<string, Control> _fields = new(StringComparer.Ordinal);
    private readonly List<(Task<PlatformNativeWorkbenchCommandResult> Task, string Action)> _writes = [];
    private readonly VBoxContainer _body = new();
    private readonly VBoxContainer _cards = new();
    private readonly VBoxContainer _items = new();
    private readonly VBoxContainer _forms = new();
    private readonly VBoxContainer _result = new();
    private readonly Label _connection = new() { AutowrapMode = TextServer.AutowrapMode.WordSmart };
    private readonly Label _notice = new() { AutowrapMode = TextServer.AutowrapMode.WordSmart };
    private readonly Label _fences = new() { AutowrapMode = TextServer.AutowrapMode.WordSmart };
    private readonly Label _pagination = new();
    private readonly Button _previous = new() { Text = "上一页" };
    private readonly Button _next = new() { Text = "下一页" };
    private Task<(long Generation, JsonElement? View, string? Error)>? _read;
    private JsonElement? _view;
    private string _page = "play";
    private string? _context;
    private int _offset;
    private long _generation;
    private string? _formsKey;
    private string? _recordingContext;
    private string? _itemsKey;
    private bool _disposed;
    private bool _visible;
    internal ScrollContainer Root { get; } = new() { SizeFlagsHorizontal = SizeFlags.ExpandFill, SizeFlagsVertical = SizeFlags.ExpandFill };

    internal PlatformNativeWorkbenchPanel(Action<PlatformPolicyCommand> directRecovery)
    {
        Root.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        _body.SizeFlagsHorizontal = SizeFlags.ExpandFill;
        _body.AddThemeConstantOverride("separation", 8);
        Root.AddChild(_body);
        var navigation = new HBoxContainer();
        foreach ((string page, string label) in new[] { ("play", "实战"), ("data", "采集与资料"), ("training", "训练与任务"), ("models", "模型与分析"), ("settings", "账号与资源") })
        {
            string selected = page;
            navigation.AddChild(Button(label, () => Navigate(selected, _context)));
        }
        _body.AddChild(navigation);
        _body.AddChild(_connection);
        var recovery = new HBoxContainer();
        recovery.AddChild(Button("暂停本机加载/接管", () => ModelRecovery("models.human")));
        recovery.AddChild(Button("结束本机模型任务", () => ModelRecovery("models.stop")));
        recovery.AddChild(Button("直接归还 Human", () => directRecovery(PlatformPolicyCommand.Human)));
        _body.AddChild(recovery);
        recovery = new HBoxContainer();
        recovery.AddChild(Button("直接停止当前 Runtime", () => directRecovery(PlatformPolicyCommand.Stop)));
        recovery.AddChild(Button("刷新本页", () => Refresh(true)));
        _body.AddChild(recovery);
        _body.AddChild(Label("关闭工作台或游戏不会停止已独立运行的训练/上传。这里显示服务实际能力；未知结果不会自动重试。"));
        _body.AddChild(_notice);
        _body.AddChild(_fences);
        _body.AddChild(_cards);
        _body.AddChild(_items);
        var pager = new HBoxContainer();
        _previous.Pressed += () => { _offset = Math.Max(0, _offset - 25); _generation++; Refresh(true); };
        _next.Pressed += () => { _offset += 25; _generation++; Refresh(true); };
        pager.AddChild(_previous); pager.AddChild(_pagination); pager.AddChild(_next);
        _body.AddChild(pager);
        _body.AddChild(_forms);
        _body.AddChild(_result);
    }

    private static Label Label(string value) => new() { Text = value, AutowrapMode = TextServer.AutowrapMode.WordSmart, SizeFlagsHorizontal = SizeFlags.ExpandFill };
    private static Button Button(string text, Action pressed)
    { var button = new Button { Text = text }; button.Pressed += pressed; return button; }
    private static void Clear(Node node) { foreach (Node child in node.GetChildren()) { node.RemoveChild(child); child.QueueFree(); } }
    private static string? String(JsonElement value, string key) => value.ValueKind == JsonValueKind.Object && value.TryGetProperty(key, out JsonElement result) && result.ValueKind == JsonValueKind.String ? result.GetString() : null;
    private static bool Boolean(JsonElement value, string key) => value.ValueKind == JsonValueKind.Object && value.TryGetProperty(key, out JsonElement result) && result.ValueKind == JsonValueKind.True;
    private static JsonElement? Object(JsonElement value, string key) => value.ValueKind == JsonValueKind.Object && value.TryGetProperty(key, out JsonElement result) && result.ValueKind == JsonValueKind.Object ? result : null;
    private static string Show(JsonElement value) => value.ValueKind == JsonValueKind.String ? value.GetString() ?? "" : value.GetRawText();

    private void Navigate(string page, string? context)
    {
        bool changed = _page != page || _context != context;
        _page = page; _context = context; _offset = 0; _generation++;
        if (changed) { _formsKey = null; _itemsKey = null; _drafts.Clear(); }
        Refresh(true);
    }

    internal void Refresh(bool visible)
    {
        if (_disposed || !visible || _read is { IsCompleted: false }) return;
        PlatformNativeWorkbenchConnection? connection = PlatformNativeWorkbenchConnection.Current;
        if (connection is null || connection.Binding.ExpiresAt <= DateTimeOffset.UtcNow.ToUnixTimeSeconds())
        {
            _connection.Text = "本机访问未连接或已过期。需要已验证工作台的明确本机访问设置；旧工具包保留浏览器入口。直接 Human/Stop 不依赖此连接。";
            foreach (Control field in _fields.Values) if (field is BaseButton button) button.Disabled = true;
            return;
        }
        _connection.Text = "正在读取本机工作台…";
        long generation = _generation;
        string page = _page; int offset = _offset; string? context = _context;
        _read = Task.Run(async () =>
        {
            try { return (generation, (JsonElement?)await _client.ViewAsync(connection, page, offset, context, _lifetime.Token), (string?)null); }
            catch (Exception error) when (error is HttpRequestException or OperationCanceledException or JsonException or InvalidOperationException or IOException)
            { return (generation, (JsonElement?)null, "本机状态不可用；未提交操作。请检查工作台连接。" ); }
        });
    }

    internal void OnFrame(bool visible)
    {
        _visible = visible;
        if (_disposed) return;
        if (_read is { IsCompleted: true })
        {
            var result = _read.GetAwaiter().GetResult(); _read = null;
            if (result.Generation == _generation)
            {
                if (result.View is JsonElement view)
                {
                    PlatformNativeWorkbenchConnection? current = PlatformNativeWorkbenchConnection.Current;
                    if (current is not null && PlatformNativeWorkbenchPair.Read(view.GetProperty("binding")) == current.Binding)
                    { _view = view; if (_visible) Render(view, current); }
                }
                else _connection.Text = result.Error;
            }
        }
        for (int index = _writes.Count - 1; index >= 0; index--)
        {
            var write = _writes[index]; if (!write.Task.IsCompleted) continue;
            _writes.RemoveAt(index);
            PlatformNativeWorkbenchCommandResult result = write.Task.GetAwaiter().GetResult();
            // A completed write can leave owner IDs/capabilities unchanged.
            // Recompute button eligibility only from the next fresh owner view.
            _formsKey = null;
            _notice.Text = result.Status switch {
                "accepted" => "服务已接收操作；请求登记与真正完成是不同状态，请查看所属任务的实际进度。",
                "rejected" => "操作未投递：" + result.ErrorCode,
                _ => "操作响应未确认。保留原任务上下文；不会自动重发。请明确核对或恢复原任务。"
            };
            Clear(_result);
            if (result.OwnerResponse is JsonElement owner)
            {
                _result.AddChild(Label("最近操作 · " + write.Action));
                RenderSummary(_result, owner);
                if (write.Action == "recordings.refresh" && owner.TryGetProperty("candidates", out JsonElement candidates))
                    foreach (JsonElement candidate in candidates.EnumerateArray().Take(50))
                    {
                        string? identity = String(candidate, "candidate_id");
                        if (identity is not null) _result.AddChild(Button("选择录制 · " + identity[..Math.Min(12, identity.Length)], () => SetDraft("recordings.import:candidate_id", identity)));
                    }
                if (write.Action == "identity.login" && String(owner, "approval_url") is string approval)
                {
                    string hub = HubUrl();
                    if (PlatformNativeWorkbenchClient.TrustedLoginUrl(hub, approval))
                        _result.AddChild(Button("打开此账号的授权页", () => OS.ShellOpen(approval)));
                    if (String(owner, "user_code") is string code) _result.AddChild(Label("账号授权码：" + code));
                }
                if (String(owner, "selection_id") is string selection)
                    _result.AddChild(Button("查看已登记模型", () => Navigate("play", selection)));
            }
            Refresh(_visible);
        }
        _fences.Text = _commands.Unconfirmed.Length == 0 ? "" : "未确认的原命令（更换认证连接不会清除）：\n" + string.Join('\n', _commands.Unconfirmed);
    }

    private string HubUrl()
    {
        if (_view is not JsonElement view) return "";
        foreach (JsonElement card in view.GetProperty("cards").EnumerateArray())
            if (String(card, "owner") == "configuration") return String(card.GetProperty("data"), "trusted_hub_url") ?? "";
        return "";
    }

    private JsonElement? OwnerData(JsonElement view, string owner)
    {
        foreach (JsonElement card in view.GetProperty("cards").EnumerateArray())
            if (String(card, "owner") == owner) return Object(card, "data");
        return null;
    }

    private static readonly Dictionary<string, string> Names = new(StringComparer.Ordinal) {
        ["status"]="状态", ["phase"]="阶段", ["stage"]="阶段", ["availability"]="可用性", ["reason"]="原因",
        ["requested_action"]="已登记控制意图", ["worker_state"]="Worker 实际状态", ["domain_completion_state"]="领域完成状态",
        ["validation_state"]="结果验证", ["selected_result"]="结果是否选用", ["elapsed_seconds"]="累计用时（秒）",
        ["error_code"]="错误", ["operation_id"]="任务", ["attempt_id"]="Attempt", ["recipe_id"]="训练配方",
        ["source_id"]="来源", ["candidate_count"]="已结束录制", ["records"]="记录数", ["purpose"]="用途",
        ["mode"]="控制模式", ["loaded"]="模型已加载", ["selection_id"]="模型选择", ["device_name"]="电脑名称",
        ["device_id"]="电脑授权", ["team_configured"]="团队资源已配置", ["platform_local"]="当前本机游戏",
        ["name"]="名称", ["description"]="说明", ["qualification"]="资格范围", ["claim"]="当前证据范围",
        ["progress"]="训练进度", ["configuration_id"]="配置身份", ["checkpoint_id"]="Checkpoint",
        ["preview_id"]="已核对预览", ["artifact_id"]="来源对象", ["input_id"]="训练输入", ["run_id"]="训练运行", ["consent_required"]="需要明确采集授权",
        ["result_id"]="训练结果", ["model_id"]="模型产物", ["evaluation_id"]="评估产物"
    };
    private void RenderSummary(VBoxContainer target, JsonElement data)
    {
        if (String(data, "schema") == "spireagent/native-recording-view-1" && Object(data, "status") is JsonElement recording)
        {
            target.AddChild(Label("原生交互与观察 · " + String(recording, "recording_lifecycle")));
            target.AddChild(Label("来源仅由操作员明确声明，不是机器验证的真人证明。"));
            if (Object(recording, "source") is JsonElement source)
            {
                target.AddChild(Label($"公开观察 {Show(source.GetProperty("observations"))} · 输入 {Show(source.GetProperty("inputs"))} · 待完成 {Show(source.GetProperty("pending_inputs"))} · 缺口 {Show(source.GetProperty("gaps"))}"));
                target.AddChild(Label(Boolean(source, "accounting_complete") ? "当前无记账失败；不代表零缺口或覆盖资格。" : "记账不完整，请查看录制诊断。"));
                if (Object(source, "declaration") is JsonElement declaration)
                    target.AddChild(Label("当前来源 · " + (String(declaration, "source_kind") switch { "declared_human" => "本人操作", "agent_native_ui" => "AI界面操作", "agent_protocol" => "Agent协议", _ => "未知来源" }) + " · " + String(declaration, "actor_id")));
            }
            if (Boolean(data, "model_recovery_required"))
                target.AddChild(Label("本机模型尚在实战或需要恢复。开始/更改为本人、AI界面或未知来源前，请先明确归还 Human 或 Stop。"));
            if (Object(data, "unconfirmed") is JsonElement uncertain)
                target.AddChild(Label("保留未确认录制请求 · " + String(uncertain, "command_id") + "。刷新不会重试或证明它执行。"));
            return;
        }
        foreach (JsonProperty property in data.EnumerateObject())
        {
            if (Names.TryGetValue(property.Name, out string? label))
            {
                string text = Show(property.Value);
                if (text.Length > 400) text = text[..400] + "…";
                target.AddChild(Label(label + "：" + text));
                if (property.Name is "result_id" or "model_id" or "evaluation_id" or "checkpoint_id"
                    && property.Value.ValueKind == JsonValueKind.String && PlatformNativeWorkbenchPair.Hex(property.Value.GetString(), 64))
                {
                    string identity = property.Value.GetString()!;
                    target.AddChild(Button("查看 " + label, () => Navigate("models", identity)));
                }
            }
        }
        if (data.TryGetProperty("candidates", out JsonElement candidates) && candidates.ValueKind == JsonValueKind.Array)
            foreach (JsonElement candidate in candidates.EnumerateArray().Take(50))
                if (String(candidate, "candidate_id") is string identity)
                    target.AddChild(Button("选择已结束录制 · " + identity[..Math.Min(12, identity.Length)], () => SetDraft("recordings.import:candidate_id", identity)));
        if (data.TryGetProperty("items", out JsonElement exports) && exports.ValueKind == JsonValueKind.Array)
            foreach (JsonElement exported in exports.EnumerateArray().Take(50))
                if (String(exported, "export_id") is string identity && PlatformNativeWorkbenchPair.Hex(identity, 64))
                    target.AddChild(Button("选择授权团队导出 · " + identity[..12], () => SetDraft("downloads.start:export_id", identity)));
        if (Object(data, "operation") is JsonElement operation)
        {
            RenderSummary(target, operation);
            if (String(operation, "status") == "pending" && String(operation, "requested_action") is "pause" or "cancel")
                target.AddChild(Label("暂停/取消意图已登记；Worker 仍未确认停止。"));
            if (String(operation, "status") == "interrupted_unknown")
                target.AddChild(Label("结果未知：先明确核对原 attempt，不自动启动或恢复。"));
        }
        if (Object(data, "session") is JsonElement session) RenderSummary(target, session);
        if (Object(data, "parameters") is JsonElement parameters) RenderSummary(target, parameters);
        if (Object(data, "runtime") is JsonElement runtime) RenderSummary(target, runtime);
        if (Object(data, "default") is JsonElement settings && Object(settings, "template") is JsonElement template)
            if (String(template, "consent_text") is string consent) target.AddChild(Label("当前完整采集声明：\n" + consent));
        if (Object(data, "enrollment") is JsonElement enrollment && Object(enrollment, "template") is JsonElement previousTemplate)
            if (String(previousTemplate, "consent_text") is string consent) target.AddChild(Label("已登记采集声明：\n" + consent));
    }

    private void Render(JsonElement view, PlatformNativeWorkbenchConnection connection)
    {
        _connection.Text = "已连接本机工作台 · " + connection.Binding.WorkbenchInstanceId[..8] + " · 独立后台任务";
        Clear(_cards);
        foreach (JsonElement card in view.GetProperty("cards").EnumerateArray())
        { _cards.AddChild(Label("— " + (String(card, "owner") ?? "任务") + " —")); RenderSummary(_cards, card.GetProperty("data")); }
        JsonElement pagination = view.GetProperty("pagination");
        int total = pagination.GetProperty("total").GetInt32();
        _pagination.Text = $"{_offset + 1}–{Math.Min(_offset + 25, total)} / {total}";
        _previous.Disabled = _offset == 0; _next.Disabled = _offset + 25 >= total || _offset + 25 > 10000;
        JsonElement items = view.GetProperty("items");
        string itemsKey = _page + ":" + items.GetRawText();
        if (_itemsKey != itemsKey)
        {
            _itemsKey = itemsKey; Clear(_items);
            foreach (JsonElement item in items.EnumerateArray())
            {
                string? identity = String(item, "artifact_id") ?? String(item, "selection_id");
                if (identity is null) continue;
                string title = String(item, "label") ?? String(item, "kind") ?? "对象";
                if (Object(item, "parameters") is JsonElement metadata) title = String(metadata, "display_name") ?? String(metadata, "name") ?? title;
                string selected = identity;
                _items.AddChild(Button(title + " · " + identity[..Math.Min(12, identity.Length)], () => Navigate(_page, selected)));
                if (_page == "data" && PlatformNativeWorkbenchPair.Hex(identity, 64))
                {
                    var pick = new CheckBox { Text = "加入 Human 来源选择（资格仍由服务核对）" };
                    string key = "datasets.human-preview:artifact_ids";
                    pick.ButtonPressed = _drafts.GetValueOrDefault(key, "").Split(',').Contains(identity);
                    pick.Toggled += chosen => {
                        var ids = _drafts.GetValueOrDefault(key, "").Split(',', StringSplitOptions.RemoveEmptyEntries).ToHashSet(StringComparer.Ordinal);
                        if (chosen) ids.Add(selected); else ids.Remove(selected);
                        SetDraft(key, string.Join(',', ids.Order(StringComparer.Ordinal)));
                    };
                    _items.AddChild(pick);
                }
            }
        }
        JsonElement capabilities = view.GetProperty("capabilities");
        string ownerContexts = string.Join(';', view.GetProperty("cards").EnumerateArray().Select(card => {
            JsonElement data = card.GetProperty("data");
            JsonElement? operation = Object(data, "operation") ?? (Object(data, "session") is JsonElement session ? Object(session, "operation") : null);
            if (String(card, "owner") == "native_recording" && Object(data, "status") is JsonElement recording)
            {
                _recordingContext = PlatformNativeWorkbenchCommands.RecordingContext(String(recording, "runtime_instance_id"), String(recording, "recording_session_id"));
                return "native_recording:" + _recordingContext + ":" + String(recording, "recording_lifecycle")
                    + ":" + (Object(recording, "source") is JsonElement source ? String(source, "segment_id") : "")
                    + ":" + Boolean(data, "recovery_required");
            }
            return String(card, "owner") + ":" + (operation is JsonElement task ?
                (String(task, "operation_id") ?? String(task, "id")) + ":" + String(task, "attempt_id") : "");
        }));
        string formsKey = _page + ":" + _context + ":" + connection.Binding.PairId + ":"
            + ownerContexts + ":" + capabilities.GetRawText() + ":" + _commands.Unconfirmed.Length;
        if (formsKey != _formsKey)
        {
            _formsKey = formsKey; string? focused = _fields.FirstOrDefault(item => item.Value.HasFocus()).Key;
            Clear(_forms); _fields.Clear();
            foreach (JsonElement descriptor in capabilities.GetProperty("actions").EnumerateArray())
            {
                string? action = String(descriptor, "action_id");
                if (action is null || !PlatformNativeWorkbenchClient.Actions.Contains(action)) continue;
                if (action == "training.start") BuildTrainingForm(view, connection, descriptor);
                else BuildActionForm(view, connection, descriptor, action);
            }
            foreach (string capability in new[] { "source_aware_prepare", "remote_execution" })
                if (capabilities.TryGetProperty(capability, out JsonElement support) && !Boolean(support, "enabled"))
                    _forms.AddChild(Label((capability == "remote_execution" ? "远端计算" : "新来源准备") + "暂不可用：" + String(support, "reason")));
            _forms.AddChild(Button("在外部继续此对象/任务", () => OS.ShellOpen(PlatformNativeWorkbenchClient.ExternalUrl(connection, view.GetProperty("context")))));
            if (focused is not null && _fields.TryGetValue(focused, out Control? next)) next.GrabFocus();
        }
    }

    private void SetDraft(string key, string value)
    {
        _drafts[key] = value;
        if (_fields.TryGetValue(key, out Control? field) && field is LineEdit input) input.Text = value;
    }

    private void BuildActionForm(JsonElement view, PlatformNativeWorkbenchConnection connection, JsonElement descriptor, string action)
    {
        var form = new VBoxContainer();
        form.AddChild(Label(String(descriptor, "label") ?? action));
        var controls = new Dictionary<string, (string Type, Control Control)>();
        foreach (JsonElement declared in descriptor.GetProperty("fields").EnumerateArray())
        {
            string name = String(declared, "name")!; string type = String(declared, "type")!;
            string key = action + ":" + name;
            form.AddChild(Label(String(declared, "label") ?? name));
            string saved = _drafts.GetValueOrDefault(key, Show(declared.GetProperty("default")));
            Control control;
            if (type == "boolean")
            {
                var check = new CheckBox { Text = String(declared, "label"), ButtonPressed = saved == "true" };
                check.Toggled += selected => _drafts[key] = selected ? "true" : "false"; control = check;
            }
            else if (type is "enum" or "selection")
            {
                var select = new OptionButton(); select.AddItem("请选择"); select.SetItemMetadata(0, "");
                if (type == "selection")
                    foreach (JsonElement item in view.GetProperty("items").EnumerateArray())
                    { string? identity = String(item, "selection_id"); if (identity is null) continue; select.AddItem(String(item, "label") ?? identity); select.SetItemMetadata(select.ItemCount - 1, identity); }
                else foreach (JsonElement option in declared.GetProperty("options").EnumerateArray())
                { select.AddItem(String(option, "label")); select.SetItemMetadata(select.ItemCount - 1, String(option, "value") ?? ""); }
                for (int index = 0; index < select.ItemCount; index++) if (select.GetItemMetadata(index).AsString() == saved) select.Select(index);
                select.ItemSelected += index => _drafts[key] = select.GetItemMetadata((int)index).AsString(); control = select;
            }
            else
            {
                var input = new LineEdit { Text = saved, MaxLength = type == "artifact-list" ? 32768 : 128 };
                input.TextChanged += value => _drafts[key] = value; control = input;
            }
            _fields[key] = control; controls[name] = (type, control); form.AddChild(control);
        }
        if (action == "collection.consent")
            form.AddChild(Label("此声明包含真实 Human 来源、上传及项目共享授权。AI 或未知来源不能声明为 Human；登录不自动上传旧私有资料。"));
        Button submit = Button(String(descriptor, "label") ?? action, () =>
        {
            try
            {
                Dictionary<string, object?> body;
                if (action.StartsWith("training.", StringComparison.Ordinal)) body = TrainingControlPayload(view, action);
                else
                {
                    body = [];
                    foreach (var item in controls)
                    {
                        string value = item.Value.Control switch { LineEdit input => input.Text.Trim(), OptionButton select => select.GetItemMetadata(select.Selected).AsString(), _ => "" };
                        object? selected = item.Value.Type switch {
                            "boolean" => ((CheckBox)item.Value.Control).ButtonPressed,
                            "optional-artifact" => value.Length == 0 ? null : value,
                            "artifact-list" => value.Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries),
                            _ => value
                        };
                        if (item.Value.Type == "artifact" && !PlatformNativeWorkbenchPair.Hex(value, 64)) throw new InvalidOperationException("请选择完整对象编号。");
                        if (item.Value.Type is "selection" or "enum" && value.Length == 0) throw new InvalidOperationException("请选择服务声明的选项。");
                        body[item.Key] = selected;
                    }
                    if (action.StartsWith("recording.", StringComparison.Ordinal))
                    {
                        JsonElement recording = view.GetProperty("cards").EnumerateArray()
                            .First(card => String(card, "owner") == "native_recording").GetProperty("data").GetProperty("status");
                        body["kind"] = action switch { "recording.start" => "start_new_session", "recording.change_source" => "change_source", _ => action.Split('.')[1] };
                        body["runtime_instance_id"] = String(recording, "runtime_instance_id");
                        body["recording_session_id"] = String(recording, "recording_session_id");
                        body["source_segment_id"] = Object(recording, "source") is JsonElement source ? String(source, "segment_id") : null;
                        body["command_id"] = Guid.NewGuid().ToString("D");
                        if (!body.ContainsKey("source_kind")) { body["source_kind"] = null; body["actor_id"] = null; }
                    }
                }
                Submit(connection, action, body);
            }
            catch (Exception error) when (error is JsonException or InvalidOperationException or FormatException)
            { _notice.Text = "尚未提交：" + error.Message; }
        });
        submit.Disabled = !Boolean(descriptor, "enabled") || !_commands.CanSubmit(connection.Binding.ConfigurationId, action, _recordingContext);
        if (!Boolean(descriptor, "enabled")) form.AddChild(Label("当前不可用：" + String(descriptor, "reason")));
        _fields[action + ":submit"] = submit; form.AddChild(submit); _forms.AddChild(form);
    }

    private static Dictionary<string, object?> TrainingControlPayload(JsonElement view, string action)
    {
        JsonElement operation = view.GetProperty("cards").EnumerateArray().First(card => String(card, "owner") == "training").GetProperty("data").GetProperty("operation");
        var body = new Dictionary<string, object?> { ["operation_id"] = String(operation, "operation_id"), ["expected_attempt_id"] = String(operation, "attempt_id") };
        if (action == "training.resume")
        {
            if (String(operation, "worker_state") != "terminal" || String(operation, "status") == "interrupted_unknown") throw new InvalidOperationException("先核对原 attempt 的停止与 checkpoint。");
            body["checkpoint_id"] = String(operation, "checkpoint_id"); body["intent_id"] = Guid.NewGuid().ToString("N");
            body["limits"] = operation.GetProperty("limits").Clone();
        }
        return body;
    }

    private void BuildTrainingForm(JsonElement view, PlatformNativeWorkbenchConnection connection, JsonElement descriptor)
    {
        JsonElement training = view.GetProperty("capabilities").GetProperty("training");
        if (String(training, "schema") != "spireagent/training-capabilities-v1") return;
        var form = new VBoxContainer();
        form.AddChild(Label("明确训练：先选择来源与可信配方，资格/use 仍由服务核对。"));
        var source = new LineEdit { Text = _drafts.GetValueOrDefault("training.start:source_id", _context ?? ""), MaxLength = 64 };
        source.TextChanged += value => _drafts["training.start:source_id"] = value;
        _fields["training.start:source_id"] = source; form.AddChild(Label("训练来源")); form.AddChild(source);
        var recipe = new OptionButton();
        JsonElement[] recipes = training.GetProperty("recipes").EnumerateArray().ToArray();
        foreach (JsonElement declared in recipes) recipe.AddItem(String(declared, "recipe_id"));
        string savedRecipe = _drafts.GetValueOrDefault("training.start:recipe", "structured-m2-cpu-v2");
        int selectedIndex = Array.FindIndex(recipes, declared => String(declared, "recipe_id") == savedRecipe);
        recipe.Select(selectedIndex);
        _fields["training.start:recipe"] = recipe; form.AddChild(Label("训练配方")); form.AddChild(recipe);
        var detail = new VBoxContainer(); form.AddChild(detail);
        Dictionary<string, SpinBox> configFields = [], limitFields = [];
        JsonElement selectedRecipe = default;
        OptionButton placement = new();
        Button submit = null!;
        void Update()
        {
            Clear(detail); configFields.Clear(); limitFields.Clear();
            if (recipe.Selected < 0 || recipe.Selected >= recipes.Length)
            { detail.AddChild(Label("此配方未声明；请明确选择服务配方。")); if (submit is not null) submit.Disabled = true; return; }
            selectedRecipe = recipes[recipe.Selected]; _drafts["training.start:recipe"] = String(selectedRecipe, "recipe_id")!;
            placement = new OptionButton();
            string[] advertised = selectedRecipe.GetProperty("placement_ids").EnumerateArray().Select(item => item.GetString()!).ToArray();
            foreach (JsonElement resource in training.GetProperty("placements").EnumerateArray())
            {
                string identity = String(resource, "placement_id")!; if (!advertised.Contains(identity)) continue;
                placement.AddItem(identity + (Boolean(resource, "remote") ? " · 远端" : " · 本机")); placement.SetItemMetadata(placement.ItemCount - 1, identity);
            }
            detail.AddChild(Label("计算资源")); detail.AddChild(placement);
            foreach (JsonProperty declared in selectedRecipe.GetProperty("config_fields").EnumerateObject())
            {
                double minimum = declared.Value[0].GetDouble(), maximum = declared.Value[1].GetDouble();
                double initial = selectedRecipe.GetProperty("config_defaults").GetProperty(declared.Name).GetDouble();
                string key = "training.start:config:" + declared.Name;
                if (_drafts.TryGetValue(key, out string? saved) && double.TryParse(saved, NumberStyles.Float, CultureInfo.InvariantCulture, out double previous)) initial = previous;
                var number = new SpinBox { MinValue = minimum, MaxValue = maximum, Step = 1, Value = initial };
                number.ValueChanged += value => _drafts[key] = value.ToString(CultureInfo.InvariantCulture);
                configFields[declared.Name] = number; _fields[key] = number;
                detail.AddChild(Label(declared.Name switch { "epochs" => "遍历轮数", "max_updates" => "更新步数上限", "checkpoint_every_boundaries" => "Checkpoint 保存间隔", _ => declared.Name })); detail.AddChild(number);
            }
            if (selectedRecipe.TryGetProperty("fixed_config_fields", out JsonElement fixedFields))
                foreach (JsonElement fixedField in fixedFields.EnumerateArray())
                    detail.AddChild(Label(fixedField.GetString() + "（配方固定）：" + Show(selectedRecipe.GetProperty("config_defaults").GetProperty(fixedField.GetString()!))));
            foreach (JsonProperty declared in selectedRecipe.GetProperty("limits").EnumerateObject())
            {
                double minimum = declared.Value.GetProperty("minimum").GetDouble(), maximum = declared.Value.GetProperty("maximum").GetDouble();
                double initial = declared.Value.TryGetProperty("default", out JsonElement supplied) ? supplied.GetDouble() : Math.Min(600, maximum);
                string key = "training.start:limit:" + declared.Name;
                if (_drafts.TryGetValue(key, out string? saved) && double.TryParse(saved, NumberStyles.Float, CultureInfo.InvariantCulture, out double previous)) initial = previous;
                var number = new SpinBox { MinValue = minimum, MaxValue = maximum, Step = 1, Value = initial };
                number.ValueChanged += value => _drafts[key] = value.ToString(CultureInfo.InvariantCulture);
                limitFields[declared.Name] = number; _fields[key] = number;
                detail.AddChild(Label(declared.Name == "wall_seconds" ? "累计运行时间上限（秒）" : "scratch 监测阈值 / artifact 预留上限（字节，分别检查）")); detail.AddChild(number);
            }
            bool available = Boolean(descriptor, "enabled") && Boolean(selectedRecipe, "dependencies_available") && placement.ItemCount > 0;
            detail.AddChild(Label(available ? "不会自动上传、重试或加载到游戏。恢复保留原累计预算。" : "当前任务、配方依赖或计算资源不允许启动。"));
            if (submit is not null) submit.Disabled = !available || !_commands.CanSubmit(connection.Binding.ConfigurationId, "training.start");
        }
        recipe.ItemSelected += _ => Update();
        submit = Button("明确开始 / 新建一次训练", () =>
        {
            try
            {
                if (!PlatformNativeWorkbenchPair.Hex(source.Text.Trim(), 64) || placement.Selected < 0) throw new InvalidOperationException("请选择完整来源与计算资源。");
                var config = JsonSerializer.Deserialize<Dictionary<string, object?>>(selectedRecipe.GetProperty("config_defaults").GetRawText())!;
                foreach (var item in configFields) config[item.Key] = checked((long)item.Value.Value);
                var limits = new Dictionary<string, long>(); foreach (var item in limitFields) limits[item.Key] = checked((long)item.Value.Value);
                JsonElement? operation = OwnerData(view, "training") is JsonElement status ? Object(status, "operation") : null;
                string? after = operation is JsonElement prior && String(prior, "status") == "completed"
                    && (String(prior, "dataset_id") == source.Text.Trim() || Object(prior, "input_refs") is JsonElement inputs && String(inputs, "source_id") == source.Text.Trim()) ? String(prior, "operation_id") : null;
                var payload = new Dictionary<string, object?> { ["schema"] = "spireagent/training-request-v1", ["intent_id"] = Guid.NewGuid().ToString("N"),
                    ["recipe_id"] = String(selectedRecipe, "recipe_id"), ["source_id"] = source.Text.Trim(), ["config"] = config,
                    ["placement_id"] = placement.GetItemMetadata(placement.Selected).AsString(), ["limits"] = limits, ["after_completed_operation_id"] = after };
                Submit(connection, "training.start", payload);
            }
            catch (Exception error) when (error is JsonException or InvalidOperationException or OverflowException)
            { _notice.Text = "尚未提交：" + error.Message; }
        });
        Update(); _fields["training.start:submit"] = submit; form.AddChild(submit); _forms.AddChild(form);
    }

    private void ModelRecovery(string action)
    {
        PlatformNativeWorkbenchConnection? current = PlatformNativeWorkbenchConnection.Current;
        long now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        if (current is not null && current.Binding.ExpiresAt > now)
        { Submit(current, action, new Dictionary<string, object?>()); return; }
        PlatformNativeWorkbenchCommands.NativeModelIntent? admitted = _commands.SubmittedModelIntent;
        if (admitted is null || now > admitted.Connection.Binding.ExpiresAt + 600)
        { _notice.Text = "没有本面板提交的可恢复模型意图，或恢复证明已过期。当前 Runtime 的直接 Human/Stop 仍可使用。"; return; }
        Submit(admitted.Connection, action, new Dictionary<string, object?> {
            ["native_request_id"] = admitted.RequestId }, originalRecovery: true);
    }

    private void Submit(PlatformNativeWorkbenchConnection connection, string action, object payload, bool originalRecovery = false)
    {
        if (_disposed) return;
        if (!originalRecovery && PlatformNativeWorkbenchConnection.Current?.Binding != connection.Binding)
        { _notice.Text = "认证连接已变化。保留原任务上下文，请刷新后明确操作。"; return; }
        if (!_commands.CanSubmit(connection.Binding.ConfigurationId, action, _recordingContext)) return;
        if (_fields.TryGetValue(action + ":submit", out Control? control) && control is Button button) button.Disabled = true;
        _notice.Text = "正在提交本次意图；不会重复发送。";
        _writes.Add((_commands.RunAsync(_client, connection, action, payload, _lifetime.Token), action));
    }

    public void Dispose()
    {
        if (_disposed) return; _disposed = true; _lifetime.Cancel(); _client.Dispose();
        Task[] pending = _writes.Select(item => (Task)item.Task).Concat(_read is null ? [] : new Task[] { _read }).ToArray();
        _ = Task.WhenAll(pending).ContinueWith(task => { _ = task.Exception; _lifetime.Dispose(); }, TaskScheduler.Default);
    }
}
