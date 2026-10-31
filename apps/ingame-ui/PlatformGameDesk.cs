using Godot;
using System.Text.Json;
using HttpClient = System.Net.Http.HttpClient;
using static Godot.Control;
using static STS2PlatformLiveUi.PlatformNativeWorkbenchPanel;

namespace STS2PlatformLiveUi;

/// <summary>Four presentations over one application. Window events never submit a business command.</summary>
internal sealed class PlatformLivePanel : IDisposable
{
    private readonly PlatformNativeWorkbenchPanel _desk = new();
    private readonly PlatformDeskSelection _selection = new();
    private readonly HttpClient _openHttp = PlatformWorkbenchOpenClient.CreateHttpClient();
    private readonly CancellationTokenSource _lifetime = new();
    private readonly PlatformLiveStatusClient _diagnosticClient = new();
    private readonly PlatformPolicyCommands _recoveryCommands = new();
    private readonly List<PlatformDeskWindow> _windows = [];
    private readonly List<Label> _recordingLabels = [], _modelLabels = [], _noticeLabels = [];
    private readonly List<Label> _recordingRecent = [];
    private readonly List<OptionButton> _modelSelectors = [];
    private readonly List<(Button Button, string Action)> _commandButtons = [];
    private readonly List<Button> _mistakeButtons = [];
    private readonly Dictionary<PlatformDeskWindow, Button> _windowButtons = [];
    private Label _mainSummary = null!;
    private HBoxContainer _recoveryRow = null!;
    private Button _recoveryButton = null!;
    private string? _recoveryAction;
    private PlatformDeskWindow _main = null!, _recording = null!, _model = null!, _actions = null!, _diagnostics = null!;
    private VBoxContainer _actionRows = null!;
    private Label _choiceMessage = null!, _diagnosticText = null!;
    private Button _execute = null!, _clear = null!, _launcher = null!;
    private SceneTree? _tree;
    private Action? _frame;
    private Task<PlatformWorkbenchOpenResult>? _open;
    private Task<PlatformLiveStatus>? _diagnosticRead;
    private Task<IPlatformRuntimeStatus>? _runtimeRead;
    private Task<IPlatformRuntimeStatus>? _directRecovery;
    private string? _rememberedRun, _selectedModel, _modelListKey, _choicesKey, _lastHumanStep;
    private long _renderedRevision = -1;
    private bool _disposed, _autoConnectStarted, _choicesAttempted;
    private string _localNotice = "";
    private bool _choicesReady;
    internal Control Root { get; } = new() { Name = "PlatformGameDesk", MouseFilter = MouseFilterEnum.Ignore };

    internal PlatformLivePanel()
    {
        Root.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        Root.Theme = new Theme { DefaultFontSize = 16 };
        _launcher = Button("对局台 · 正在连接", () => _main.Open());
        _launcher.Position = new Vector2(12, 12);
        _launcher.CustomMinimumSize = new Vector2(260, 36);
        Root.AddChild(_launcher);
        BuildWindows();
        _desk.Reconnect = BeginConnect;
        var timer = new Godot.Timer { WaitTime = 1, Autostart = true };
        timer.Timeout += Poll;
        Root.AddChild(timer);
    }

    private static Label Label(string text = "") => new() {
        Text = text, AutowrapMode = TextServer.AutowrapMode.WordSmart,
        MouseFilter = MouseFilterEnum.Ignore, SizeFlagsHorizontal = SizeFlags.ExpandFill
    };
    private static Button Button(string text, Action pressed)
    {
        var button = new Button { Text = text, FocusMode = FocusModeEnum.None,
            MouseFilter = MouseFilterEnum.Stop, CustomMinimumSize = new Vector2(0, 36) };
        button.Pressed += pressed;
        return button;
    }
    private PlatformDeskWindow Window(string id, string title, Vector2 size, bool visible)
    {
        var window = new PlatformDeskWindow(id, title, size);
        Root.AddChild(window.Root); _windows.Add(window);
        window.Root.Visible = visible;
        return window;
    }
    private void BuildWindows()
    {
        _main = Window("main", "对局台", new Vector2(430, 300), true);
        _recording = Window("recording", "录制与标记", new Vector2(390, 330), true);
        _model = Window("model", "模型运行", new Vector2(390, 250), false);
        _actions = Window("actions", "动作列表", new Vector2(520, 540), false);
        _diagnostics = Window("diagnostics", "诊断", new Vector2(520, 410), false);
        _mainSummary = Label("正在连接本机后台…");
        _main.Body.AddChild(_mainSummary);
        var manager = Row(_main.Body);
        foreach (var window in new[] { _recording, _model, _actions })
        {
            var owned = window;
            var open = Button("打开" + owned.Name, () => { if (owned.Root.Visible) owned.Close(); else owned.Open(); RenderWindowButtons(); });
            _windowButtons.Add(owned, open); manager.AddChild(open);
            owned.Root.VisibilityChanged += RenderWindowButtons;
        }
        _main.Body.AddChild(Label("点窗口名打开或收回；收回、最小化都不停录制或模型。"));
        var inline = new VBoxContainer { Visible = false, SizeFlagsHorizontal = SizeFlags.ExpandFill };
        _main.Body.AddChild(Button("在主窗口操作", () => { inline.Visible = !inline.Visible; }));
        _main.Body.AddChild(inline);
        AddRecordingControls(inline);
        AddModelControls(inline);
        AddMarkControls(inline);
        AddState(_main.Body, _noticeLabels);
        _recoveryRow = Row(_main.Body); _recoveryRow.Visible = false;
        _recoveryButton = Button("需要处理", () => {
            if (_recoveryAction == "desk.reconnect") BeginConnect();
            else if (_recoveryAction is "desk.model_stop" or "desk.record_stop") _desk.Submit(_recoveryAction, new { });
        });
        _recoveryRow.AddChild(_recoveryButton);
        var mainFooter = Row(_main.Body);
        mainFooter.AddChild(Button("重新连接", BeginConnect));
        mainFooter.AddChild(Button("诊断", () => { _diagnostics.Open(); PollDiagnostics(); }));
        RenderWindowButtons();

        AddState(_recording.Body, _recordingLabels);
        var recent = Label("最近记录：等待后台确认");
        _recordingRecent.Add(recent); _recording.Body.AddChild(recent);
        AddRecordingControls(_recording.Body, _recording.Footer);
        AddMarkControls(_recording.Body);
        AddState(_recording.Body, _noticeLabels);
        AddState(_model.Body, _modelLabels);
        AddModelControls(_model.Body, _model.Footer);
        _model.Body.AddChild(Label("换模型前先点“停，我来”。每个模型步都会记录。"));
        AddState(_model.Body, _noticeLabels);
        _choiceMessage = Label("打开后读取当前全部合法动作。");
        _actions.Body.AddChild(_choiceMessage);
        _actionRows = new VBoxContainer { SizeFlagsHorizontal = SizeFlags.ExpandFill };
        _actionRows.AddThemeConstantOverride("separation", 6);
        _actions.Body.AddChild(_actionRows);
        var actionsFooter = Row(_actions.Footer);
        actionsFooter.AddChild(Command("准备动作列表", "desk.choices_open"));
        _actions.Root.VisibilityChanged += () => {
            if (_actions.Root.Visible && !_choicesAttempted)
            { _choicesAttempted = true; _desk.Submit("desk.choices_open", new { }); }
        };
        _execute = Button("执行第 1 个", ExecuteRanking);
        _clear = Button("清空", () => { _selection.Clear(); RenderChoices(true); });
        actionsFooter.AddChild(_execute); actionsFooter.AddChild(_clear);
        _actions.Footer.AddChild(Label("按先后点几个，再执行第 1 个；再点已选项可取消。"));
        _diagnosticText = Label("诊断只读；不会提交游戏动作。");
        _diagnostics.Body.AddChild(_diagnosticText);
        var recovery = Row(_diagnostics.Body);
        recovery.AddChild(Button("停，我来（直接恢复）", () => DirectRecovery(PlatformPolicyCommand.Human)));
        recovery.AddChild(Button("停 Runtime", () => DirectRecovery(PlatformPolicyCommand.Stop)));
        _diagnostics.Body.AddChild(Button("刷新诊断", PollDiagnostics));
    }
    private static HBoxContainer Row(Node parent)
    {
        var row = new HBoxContainer { SizeFlagsHorizontal = SizeFlags.ExpandFill };
        row.AddThemeConstantOverride("separation", 6); parent.AddChild(row); return row;
    }
    private static void AddState(Node parent, List<Label> labels)
    { var label = Label(); parent.AddChild(label); labels.Add(label); }
    private Button Command(string text, string action, Func<object>? payload = null)
    {
        var button = Button(text, () => {
            _localNotice = "";
            if (action == "desk.model_stop" && !_desk.Connected) DirectRecovery(PlatformPolicyCommand.Human);
            else _desk.Submit(action, payload?.Invoke() ?? new { });
            RenderStatus();
        });
        _commandButtons.Add((button, action)); return button;
    }
    private void AddRecordingControls(Node parent, Node? fixedFooter = null)
    {
        var row = Row(fixedFooter ?? parent);
        row.AddChild(Command("开始本人录制", "desk.record_start"));
        row.AddChild(Command("停录制并保存", "desk.record_stop"));
    }
    private void AddModelControls(Node parent, Node? fixedFooter = null)
    {
        var selector = new OptionButton { SizeFlagsHorizontal = SizeFlags.ExpandFill, FocusMode = FocusModeEnum.None,
            CustomMinimumSize = new Vector2(0, 36) };
        selector.AddItem("正在读取模型…");
        selector.ItemSelected += index => {
            _selectedModel = selector.GetItemMetadata((int)index).AsString();
            foreach (OptionButton other in _modelSelectors)
                for (int i = 0; i < other.ItemCount; i++)
                    if (other.GetItemMetadata(i).AsString() == _selectedModel) other.Select(i);
        };
        _modelSelectors.Add(selector); parent.AddChild(selector);
        var row = Row(fixedFooter ?? parent);
        row.AddChild(Button("连续", () => SubmitModel("desk.model_auto")));
        _commandButtons.Add(((Button)row.GetChild(0), "desk.model_auto"));
        row.AddChild(Button("一步", () => SubmitModel("desk.model_step")));
        _commandButtons.Add(((Button)row.GetChild(1), "desk.model_step"));
        row.AddChild(Command("停，我来", "desk.model_stop"));
    }
    private void SubmitModel(string action)
    {
        if (string.IsNullOrEmpty(_selectedModel)) { _localNotice = "先用鼠标选择一个模型。"; RenderStatus(); return; }
        _localNotice = "";
        _desk.Submit(action, new { selection_id = _selectedModel }); RenderStatus();
    }
    private void AddMarkControls(Node parent)
    {
        var note = new LineEdit { PlaceholderText = "问题说明（可选）", SizeFlagsHorizontal = SizeFlags.ExpandFill,
            CustomMinimumSize = new Vector2(0, 36) };
        parent.AddChild(note);
        var row = Row(parent);
        row.AddChild(Command("标记问题", "desk.mark", () => new { note = note.Text, step_id = (string?)null }));
        var mistake = Command("我点错了", "desk.mistake", () => new { step_id = (string?)null });
        _mistakeButtons.Add(mistake); row.AddChild(mistake);
    }

    internal bool Mount(SceneTree tree)
    {
        try
        {
            _tree = tree; _frame = OnProcessFrame; tree.ProcessFrame += _frame;
            Root.TreeExiting += Dispose;
            Root.Resized += ArrangeInitialWindows;
            ArrangeInitialWindows();
            Poll();
            return true;
        }
        catch (Exception error) { GD.PrintErr($"[STS2 Platform Live UI] desk mount failed: {error}"); return false; }
    }
    private bool _arranged;
    private void ArrangeInitialWindows()
    {
        Vector2 viewport = Root.GetViewportRect().Size;
        if (viewport.X < 100 || viewport.Y < 100) return;
        if (!_arranged)
        {
            _main.Root.Position = new Vector2(Math.Max(8, viewport.X - 446), 58);
            _recording.Root.Position = new Vector2(Math.Max(8, viewport.X - 406), Math.Min(470, Math.Max(58, viewport.Y - 380)));
            _model.Root.Position = new Vector2(Math.Max(8, viewport.X - 846), 58);
            _actions.Root.Position = new Vector2(Math.Max(8, viewport.X - 966), Math.Min(330, Math.Max(58, viewport.Y - 590)));
            _diagnostics.Root.Position = new Vector2(24, 76); _arranged = true;
        }
        foreach (var window in _windows) window.Clamp(viewport);
    }
    private void Poll()
    {
        if (_disposed) return;
        if (!_autoConnectStarted) { _autoConnectStarted = true; BeginConnect(); }
        _desk.Refresh(_actions.Root.Visible && !_actions.Minimized);
        _runtimeRead ??= Task.Run(() => _diagnosticClient.ReadRuntimeAsync(_lifetime.Token));
        if (_diagnostics.Root.Visible && !_diagnostics.Minimized) PollDiagnostics();
    }
    private void BeginConnect()
    {
        if (_disposed || _open is { IsCompleted: false }) return;
        _localNotice = "正在连接已安装的本机后台…";
        _open = Task.Run(() => PlatformWorkbenchOpenClient.OpenAsync(_openHttp, _lifetime.Token));
        RenderStatus();
    }
    private void OnProcessFrame()
    {
        if (_disposed) return;
        try
        {
            _desk.OnFrame();
            if (_open is { IsCompleted: true })
            {
                try
                {
                    var result = _open.GetAwaiter().GetResult();
                    _localNotice = result.CanOpen ? "" : "后台未连接，未提交操作。请点“重新连接”。";
                }
                catch (Exception error) when (error is not OutOfMemoryException)
                { _localNotice = "后台未连接，未提交操作。请点“重新连接”。"; }
                _open = null; _desk.Refresh(_actions.Root.Visible && !_actions.Minimized); RenderStatus();
            }
            if (_runtimeRead is { IsCompleted: true })
            {
                try { _rememberedRun = _runtimeRead.GetAwaiter().GetResult().RunId; }
                catch (Exception error) when (error is not OutOfMemoryException) { /* Keep the original run for exact recovery. */ }
                _runtimeRead = null;
            }
            if (_diagnosticRead is { IsCompleted: true })
            {
                try
                {
                    var status = _diagnosticRead.GetAwaiter().GetResult();
                    _rememberedRun = status.PolicyRuntime?.RunId ?? _rememberedRun;
                    _diagnosticText.Text = $"游戏连接：{status.TransportStatus}\nRuntime：{status.PolicyRuntimeTransportStatus}\n运行：{status.PolicyRuntime?.Mode ?? "未知"}\n控制：{status.PolicyRuntime?.Controller ?? "未知"}\n回执：{status.Receipt.Status}\n版本：{PlatformLiveUiMod.CurrentArtifactIdentity().Version}";
                }
                catch (Exception error) when (error is not OutOfMemoryException)
                { _diagnosticText.Text = "诊断读取失败；保留原运行 ID，直接恢复仍可用。"; }
                _diagnosticRead = null;
            }
            if (_directRecovery is { IsCompleted: true })
            {
                try { var status = _directRecovery.GetAwaiter().GetResult(); _localNotice = $"恢复回执：{status.Mode} · {status.Controller}"; }
                catch (Exception error) when (error is not OutOfMemoryException)
                { _localNotice = "恢复结果未确认，不会自动重发。请核对诊断。"; }
                _directRecovery = null; RenderStatus();
            }
            if (_renderedRevision != _desk.Revision)
            { _renderedRevision = _desk.Revision; RenderStatus(); RenderModels(); RenderChoices(false); }
        }
        catch (Exception error) when (error is not OutOfMemoryException)
        { _localNotice = "界面更新异常；后台任务不受关闭窗口影响。请重新连接。"; GD.PrintErr($"[STS2 Platform Live UI] desk update failed: {error.GetType().Name}"); }
    }
    private void RenderStatus()
    {
        JsonElement desk = _desk.Desk ?? default;
        JsonElement recording = Object(desk, "recording") ?? default;
        JsonElement model = Object(desk, "model") ?? default;
        _lastHumanStep = Text(desk, "last_human_step_id");
        string lifecycle = Text(recording, "lifecycle") ?? "unknown";
        bool saving = _desk.Connected && Flag(recording, "saving");
        string recordingState = !_desk.Connected ? "⚠ 后台未连接 · 没在确认保存"
            : saving ? "● 录制中" : lifecycle switch {
                "armed" or "standby" => "录制待命 · 等下一局",
                "closing" => "正在停录制并保存",
                "recording" => "⚠ 没在保存 · 请查看原因",
                "paused" => "录制已暂停 · 没在保存",
                "closed" or "ready" or "idle" => "未录制 · 你的操作不保存",
                _ => "⚠ 录制状态未确认"
            };
        string count = Value(recording, "count");
        string latest = Text(recording, "last_saved_at") is string saved
            ? "最近一步：" + SavedAt(saved) : "最近一步：尚无保存确认";
        string recordingText = $"{recordingState} · 已记 {count} 步\n{latest}";
        string? recordingMessage = Text(recording, "message");
        if (!string.IsNullOrEmpty(recordingMessage)) recordingText += "\n" + recordingMessage;
        foreach (Label label in _recordingLabels) label.Text = recordingText;
        _recording.SetTitle($"{recordingState} · {count} 步");
        string mode = Text(model, "mode") ?? "unknown";
        bool running = mode is "auto" or "one_step" || Flag(model, "busy");
        string modelState = !_desk.Connected ? "模型状态未确认" : running ? "模型在走" : mode == "human" ? "你在走" : "模型已停";
        string modelLabel = Text(model, "label") ?? "未选模型";
        string modelText = $"{modelState} · {modelLabel}\n模型已走 {Value(model, "step_count")} 步";
        if (Text(model, "last_action") is string last) modelText += "\n最近：" + last;
        foreach (Label label in _modelLabels) label.Text = modelText;
        _model.SetTitle("模型 · " + modelState);
        _mainSummary.Text = $"{recordingState} · 已记 {count} 步\n{latest}\n{modelState} · {modelLabel}";
        _main.SetTitle("对局台 · " + (saving ? "● 录制中" : recordingState));
        _launcher.Text = "对局台 · " + recordingState + " · " + count + " 步";
        string notice = string.Join("\n", new[] { _localNotice, _desk.Notice, Text(desk, "message") }
            .Where(value => !string.IsNullOrWhiteSpace(value)).Distinct());
        foreach (Label label in _noticeLabels) label.Text = notice;
        JsonElement? recovery = Object(desk, "recovery");
        _recoveryAction = recovery is JsonElement repair ? Text(repair, "action") : null;
        _recoveryRow.Visible = recovery is not null;
        _recoveryButton.Text = recovery is JsonElement recoveryValue ? Text(recoveryValue, "label") ?? "查看诊断" : "需要处理";
        _recoveryButton.Disabled = _recoveryAction is not ("desk.reconnect" or "desk.model_stop" or "desk.record_stop");
        foreach (var (button, action) in _commandButtons)
        {
            bool disabled = !_desk.CanSubmit(action);
            if (action is "desk.model_auto" or "desk.model_step") disabled |= string.IsNullOrEmpty(_selectedModel) || running;
            if (action == "desk.record_start") disabled |= saving || lifecycle == "closing";
            if (action == "desk.model_stop" && !_desk.Connected) disabled = _rememberedRun is null || _directRecovery is not null;
            button.Disabled = disabled;
        }
        foreach (Button mistake in _mistakeButtons) mistake.Disabled |= _lastHumanStep is null;
        foreach (OptionButton selector in _modelSelectors) selector.Disabled = running || !_desk.Connected;
        foreach (Label recent in _recordingRecent)
        {
            if (recording.ValueKind == JsonValueKind.Object && recording.TryGetProperty("recent", out JsonElement rows)
                && rows.ValueKind == JsonValueKind.Array)
                recent.Text = "最近保存\n" + string.Join("\n", rows.EnumerateArray().TakeLast(3)
                    .Select(item => $"✓ {Value(item, "label")} · {InputLabel(Text(item, "input_method"))}"));
            else recent.Text = "最近保存：尚无已确认记录";
        }
    }
    private static string InputLabel(string? value) => value switch { "ranking" => "排序", "mouse" => "鼠标", "model" => "模型", _ => "方式未确认" };
    private static string SavedAt(string value) => DateTimeOffset.TryParse(value, out DateTimeOffset time)
        ? time.ToLocalTime().ToString("HH:mm:ss") + " 已保存" : "已保存（时间未提供）";
    private void RenderModels()
    {
        string key = string.Join("|", _desk.Models.Select(model => Value(model, "id") + ":" + Value(model, "label")));
        if (key == _modelListKey) return;
        _modelListKey = key;
        string? ownerSelection = _desk.Desk is JsonElement desk && Object(desk, "model") is JsonElement model
            ? Text(model, "selection_id") : null;
        if (!_desk.Models.Any(item => Text(item, "id") == _selectedModel))
            _selectedModel = ownerSelection is not null && _desk.Models.Any(item => Text(item, "id") == ownerSelection)
                ? ownerSelection : _desk.Models.Select(item => Text(item, "id")).FirstOrDefault();
        foreach (OptionButton selector in _modelSelectors)
        {
            selector.Clear();
            if (_desk.Models.Length == 0) { selector.AddItem("没有可用模型"); selector.SetItemMetadata(0, ""); }
            foreach (JsonElement item in _desk.Models)
            {
                string? identity = Text(item, "id"); if (identity is null) continue;
                int i = selector.ItemCount; selector.AddItem(Text(item, "label") ?? identity); selector.SetItemMetadata(i, identity);
                if (identity == _selectedModel) selector.Select(i);
            }
        }
        RenderStatus();
    }
    private void RenderChoices(bool force)
    {
        JsonElement desk = _desk.Desk ?? default;
        JsonElement model = Object(desk, "model") ?? default;
        bool modelRunning = Text(model, "mode") is "auto" or "one_step" || Flag(model, "busy");
        _choicesReady = _desk.Connected && _desk.ChoicesAvailable && Flag(desk, "choices_ready") && !modelRunning;
        string? catalogKey = Text(desk, "catalog_key");
        bool discarded = _selection.Update(catalogKey, _desk.Choices.Select(choice => Text(choice, "choice_id")).OfType<string>(), _choicesReady);
        if (discarded) _localNotice = "局面变了，请重排；未执行的排序已清空。";
        string notice = !_desk.ChoicesReadEnabled ? "准备中或尚未准备；未提交游戏动作。需要时点“准备动作列表”。"
            : _desk.ChoicesError ?? Text(desk, "choices_message") ?? "等待当前动作目录…";
        if (!_desk.Connected) notice = "后台未连接；动作不可执行。";
        else if (modelRunning) notice = "模型在走。先点“停，我来”，再自己选择。";
        _choiceMessage.Text = notice + (discarded ? "\n局面变了，请重排。" : "");
        _execute.Disabled = _selection.Submission() is null || !_desk.CanSubmit("desk.rank");
        _clear.Disabled = _selection.Ranking.Count == 0;
        string key = catalogKey + ":" + _choicesReady + ":" + string.Join(",", _selection.Ranking)
            + ":" + string.Join(",", _desk.Choices.Select(c => Value(c, "model_selected")));
        if (!force && key == _choicesKey) return;
        _choicesKey = key;
        foreach (Node child in _actionRows.GetChildren()) { _actionRows.RemoveChild(child); child.QueueFree(); }
        foreach (var group in _desk.Choices.GroupBy(choice => GroupLabel(Text(choice, "kind"))))
        {
            _actionRows.AddChild(Label(group.Key));
            foreach (JsonElement choice in group)
            {
                string? id = Text(choice, "choice_id"); if (id is null) continue;
                int rank = _selection.Ranking.ToList().IndexOf(id) + 1;
                string prefix = rank > 0 ? $"{rank}  " : "    ";
                string label = prefix + (Text(choice, "label") ?? "动作名称未提供");
                if (Flag(choice, "model_selected")) label += "  · 模型";
                string cost = Value(choice, "cost", "");
                if (cost.Length > 0) label += "  · " + cost + " 费";
                string capturedId = id;
                var button = Button(label, () => { _selection.Toggle(capturedId); RenderChoices(true); });
                button.Alignment = HorizontalAlignment.Left;
                button.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
                button.TooltipText = label;
                button.CustomMinimumSize = new Vector2(0, 40);
                button.Disabled = !_choicesReady;
                _actionRows.AddChild(button);
            }
        }
    }
    private static string GroupLabel(string? kind) => kind switch {
        "combat.play_card" => "出牌", "combat.use_potion" or "combat.discard_potion" => "药水",
        "combat.end_turn" => "结束回合", _ => "其他"
    };
    private void ExecuteRanking()
    {
        string[]? ranking = _selection.Submission();
        if (ranking is null || !_choicesReady) return;
        _localNotice = "";
        _desk.Submit("desk.rank", new { catalog_key = _selection.CatalogKey, ranking });
        _selection.Clear(); RenderChoices(true);
    }
    private void RenderWindowButtons()
    {
        foreach (var (window, button) in _windowButtons)
            button.Text = (window.Root.Visible ? "收回" : "打开") + window.Name;
        if (_tree is not null) Poll();
    }
    private void PollDiagnostics()
    {
        if (_disposed || _diagnosticRead is not null) return;
        _diagnosticRead = Task.Run(() => _diagnosticClient.ReadAsync(_lifetime.Token));
    }
    private void DirectRecovery(PlatformPolicyCommand command)
    {
        if (_disposed || _directRecovery is not null) return;
        if (_rememberedRun is not string run)
        { _localNotice = "尚无已确认的 Runtime 运行 ID，请先刷新诊断。"; PollDiagnostics(); RenderStatus(); return; }
        _directRecovery = Task.Run(() => _recoveryCommands.RunAsync(run, command,
            () => throw new InvalidOperationException("Direct preparation is recovery-only."),
            mode => _diagnosticClient.SetModeAsync(mode, run),
            () => throw new InvalidOperationException("Direct Tick is not a product entry."),
            () => _diagnosticClient.StopAsync(run), _lifetime.Token));
    }
    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        _lifetime.Cancel(); _desk.Dispose(); _recoveryCommands.InvalidatePending();
        if (_tree is not null && _frame is not null && GodotObject.IsInstanceValid(_tree)) _tree.ProcessFrame -= _frame;
        Root.Resized -= ArrangeInitialWindows;
        _openHttp.Dispose(); _diagnosticClient.Dispose();
    }
}

/// <summary>Bounded mouse-only furniture. It has no reference to any business service.</summary>
internal sealed class PlatformDeskWindow
{
    private readonly VBoxContainer _stack;
    private readonly ScrollContainer _scroll;
    private readonly Label _title;
    private readonly Button _minimize;
    private readonly Control _grip;
    private Vector2 _normalSize, _dragPointer, _dragPosition, _resizePointer, _resizeSize;
    private bool _dragging, _resizing;
    private int _fontSize = 16;
    internal PanelContainer Root { get; }
    internal VBoxContainer Body { get; } = new() { SizeFlagsHorizontal = SizeFlags.ExpandFill };
    internal VBoxContainer Footer { get; } = new() { SizeFlagsHorizontal = SizeFlags.ExpandFill };
    internal string Name { get; }
    internal bool Minimized { get; private set; }

    internal PlatformDeskWindow(string id, string name, Vector2 size)
    {
        Name = name; _normalSize = size;
        Root = new PanelContainer { Name = "DeskWindow_" + id, Size = size,
            CustomMinimumSize = new Vector2(320, 130), MouseFilter = MouseFilterEnum.Stop, ClipContents = true };
        var style = new StyleBoxFlat { BgColor = new Color("#111c2aef"), BorderColor = new Color("#4e9bb0"),
            BorderWidthLeft = 1, BorderWidthRight = 1, BorderWidthTop = 1, BorderWidthBottom = 1,
            CornerRadiusBottomLeft = 8, CornerRadiusBottomRight = 8, CornerRadiusTopLeft = 8, CornerRadiusTopRight = 8,
            ContentMarginLeft = 10, ContentMarginRight = 10, ContentMarginTop = 8, ContentMarginBottom = 8 };
        Root.AddThemeStyleboxOverride("panel", style);
        _stack = new VBoxContainer { SizeFlagsHorizontal = SizeFlags.ExpandFill, SizeFlagsVertical = SizeFlags.ExpandFill };
        _stack.AddThemeConstantOverride("separation", 7); Root.AddChild(_stack);
        var header = new HBoxContainer { MouseFilter = MouseFilterEnum.Stop, CustomMinimumSize = new Vector2(0, 32) };
        _title = new Label { Text = name, ClipText = true, SizeFlagsHorizontal = SizeFlags.ExpandFill,
            MouseFilter = MouseFilterEnum.Ignore, TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis };
        header.AddChild(_title);
        header.GuiInput += Drag;
        _minimize = SmallButton("—", ToggleMinimized, "最小化或还原；不停止后台任务");
        header.AddChild(_minimize);
        header.AddChild(SmallButton("−", () => ResizePreset(-1), "缩小窗口和文字"));
        header.AddChild(SmallButton("＋", () => ResizePreset(1), "放大窗口和文字"));
        header.AddChild(SmallButton("×", Close, "收回到主窗口；不停止录制或模型"));
        _stack.AddChild(header);
        _scroll = new ScrollContainer { SizeFlagsHorizontal = SizeFlags.ExpandFill, SizeFlagsVertical = SizeFlags.ExpandFill,
            HorizontalScrollMode = ScrollContainer.ScrollMode.Disabled, MouseFilter = MouseFilterEnum.Stop };
        Body.AddThemeConstantOverride("separation", 7); _scroll.AddChild(Body); _stack.AddChild(_scroll);
        _stack.AddChild(Footer);
        _grip = new Control { CustomMinimumSize = new Vector2(24, 14), MouseFilter = MouseFilterEnum.Stop,
            MouseDefaultCursorShape = CursorShape.Fdiagsize, SizeFlagsHorizontal = SizeFlags.ShrinkEnd };
        _grip.GuiInput += Resize;
        _stack.AddChild(_grip);
    }
    private static Button SmallButton(string text, Action click, string tooltip)
    {
        var button = new Button { Text = text, TooltipText = tooltip, FocusMode = FocusModeEnum.None,
            MouseFilter = MouseFilterEnum.Stop, CustomMinimumSize = new Vector2(30, 30) };
        button.Pressed += click; return button;
    }
    internal void SetTitle(string title) { _title.Text = title; _title.TooltipText = title; }
    internal void Open() { Root.Visible = true; Root.MoveToFront(); }
    internal void Close() { Root.Visible = false; }
    private void ToggleMinimized()
    {
        if (!Minimized) _normalSize = Root.Size;
        Minimized = !Minimized;
        _scroll.Visible = !Minimized; Footer.Visible = !Minimized; _grip.Visible = !Minimized;
        Root.CustomMinimumSize = Minimized ? new Vector2(320, 48) : new Vector2(320, 130);
        Root.Size = Minimized ? new Vector2(_normalSize.X, 48) : _normalSize;
        _minimize.Text = Minimized ? "↗" : "—";
        Clamp(Root.GetViewportRect().Size);
    }
    private void ResizePreset(int direction)
    {
        _fontSize = Math.Clamp(_fontSize + direction * 2, 14, 22);
        Root.Theme = new Theme { DefaultFontSize = _fontSize };
        _normalSize = new Vector2(Math.Clamp(_normalSize.X + direction * 48, 320, 900), Math.Clamp(_normalSize.Y + direction * 48, 130, 950));
        Root.Size = Minimized ? new Vector2(_normalSize.X, 48) : _normalSize;
        Clamp(Root.GetViewportRect().Size);
    }
    private void Drag(InputEvent input)
    {
        if (input is InputEventMouseButton { ButtonIndex: MouseButton.Left } button)
        {
            _dragging = button.Pressed;
            if (button.Pressed) { _dragPointer = button.GlobalPosition; _dragPosition = Root.GlobalPosition; Root.MoveToFront(); }
            Root.AcceptEvent();
        }
        else if (_dragging && input is InputEventMouseMotion motion)
        { Root.GlobalPosition = _dragPosition + motion.GlobalPosition - _dragPointer; Clamp(Root.GetViewportRect().Size); Root.AcceptEvent(); }
    }
    private void Resize(InputEvent input)
    {
        if (input is InputEventMouseButton { ButtonIndex: MouseButton.Left } button)
        {
            _resizing = button.Pressed;
            if (button.Pressed) { _resizePointer = button.GlobalPosition; _resizeSize = Root.Size; Root.MoveToFront(); }
            else _normalSize = Root.Size;
            Root.AcceptEvent();
        }
        else if (_resizing && input is InputEventMouseMotion motion)
        { Root.Size = _resizeSize + motion.GlobalPosition - _resizePointer; Clamp(Root.GetViewportRect().Size); Root.AcceptEvent(); }
    }
    internal void Clamp(Vector2 viewport)
    {
        if (viewport.X < 100 || viewport.Y < 100) return;
        Root.Size = new Vector2(Math.Clamp(Root.Size.X, 320, Math.Max(320, viewport.X - 16)),
            Minimized ? 48 : Math.Clamp(Root.Size.Y, 130, Math.Max(130, viewport.Y - 72)));
        Root.Position = new Vector2(Math.Clamp(Root.Position.X, 8, Math.Max(8, viewport.X - Root.Size.X - 8)),
            Math.Clamp(Root.Position.Y, 52, Math.Max(52, viewport.Y - Root.Size.Y - 8)));
    }
}
