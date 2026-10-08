using Godot;
using MegaCrit.Sts2.Core.Modding;
using HttpClient = System.Net.Http.HttpClient;
using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;
using static Godot.Control;

namespace STS2PlatformLiveUi;

#if !STS2_PLATFORM_UNIFIED
[ModInitializer("Initialize")]
#endif
public static class PlatformLiveUiMod
{
    private static bool _initialized;
    private static PlatformLivePanel? _panel;

    public static void Initialize()
    {
        if (_initialized)
            return;

        _initialized = true;
        try
        {
            var layer = new CanvasLayer
            {
                Layer = 100
            };
            var tree = (SceneTree)Engine.GetMainLoop();
            var root = tree.Root;
            var panel = new PlatformLivePanel();
            GD.Print($"[STS2 Platform Live UI] identity {JsonSerializer.Serialize(RuntimeIdentity())}");
            layer.AddChild(panel.Root);
            PlatformLiveUiMount? mount = null;
            mount = new PlatformLiveUiMount(
                callback => Callable.From(callback).CallDeferred(),
                () => GodotObject.IsInstanceValid(tree) && GodotObject.IsInstanceValid(root)
                    && root.IsInsideTree(),
                () =>
                {
                    GD.Print("[STS2 Platform Live UI] adding layer to SceneTree root");
                    root.AddChild(layer);
                },
                () => layer.IsInsideTree() && panel.Root.IsInsideTree()
                    && layer.GetParent() == root && panel.Root.GetTree() == tree,
                () =>
                {
                    if (!panel.Mount(tree))
                        throw new InvalidOperationException("Live UI panel preparation failed.");
                },
                () =>
                {
                    _panel = panel;
                    GD.Print("[STS2 Platform Live UI] panel ready; input=launcher; visible=false; HUD=launcher");
                    GD.Print("[STS2 Platform Live UI] layer added; open the Platform button. Gameplay actions are not exposed directly.");
                },
                exception => GD.PrintErr($"[STS2 Platform Live UI] deferred mount failed: {exception}"),
                () =>
                {
                    if (GodotObject.IsInstanceValid(root) && mount is not null)
                        root.TreeExiting -= mount.Cancel;
                    panel.Dispose();
                    if (ReferenceEquals(_panel, panel))
                        _panel = null;
                    if (GodotObject.IsInstanceValid(layer))
                    {
                        if (layer.IsInsideTree())
                            layer.QueueFree();
                        else
                            layer.Free();
                    }
                });
            root.TreeExiting += mount.Cancel;
            mount.Begin();
        }
        catch (Exception exception)
        {
            GD.PrintErr($"[STS2 Platform Live UI] initialization failed: {exception}");
        }
    }

    private static object RuntimeIdentity()
    {
        PlatformArtifactIdentity identity = CurrentArtifactIdentity();
        return new
        {
            schema = "sts2.platform/live-ui-loaded-identity-1",
            loaded_at = DateTimeOffset.UtcNow.ToString("O"),
            artifact_sha256 = identity.ArtifactSha256,
            module_version_id = identity.ModuleVersionId,
            source_revision = identity.SourceRevision,
            source_digest_sha256 = ReadAssemblyMetadata("LiveUiSourceDigestSha256", "SourceDigestSha256"),
            version = identity.Version
        };
    }

    private static readonly Lazy<PlatformArtifactIdentity> ArtifactIdentity = new(ReadArtifactIdentity);

    internal static PlatformArtifactIdentity CurrentArtifactIdentity() => ArtifactIdentity.Value;

    private static PlatformArtifactIdentity ReadArtifactIdentity()
    {
        Assembly assembly = typeof(PlatformLiveUiMod).Assembly;
        Dictionary<string, string> metadata = assembly
            .GetCustomAttributes<AssemblyMetadataAttribute>()
            .ToDictionary(item => item.Key, item => item.Value ?? "", StringComparer.Ordinal);
        string location = assembly.Location;
        string sha256 = File.Exists(location)
            ? Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(location))).ToLowerInvariant()
            : "unavailable";
        return new PlatformArtifactIdentity(
            "STS2 Platform Live UI",
            assembly.GetName().Version?.ToString() ?? "unavailable",
            metadata.GetValueOrDefault(
                "LiveUiSourceRevision",
                metadata.GetValueOrDefault("SourceRevision", "unavailable")),
            assembly.ManifestModule.ModuleVersionId.ToString(),
            sha256);
    }

    private static string ReadAssemblyMetadata(params string[] keys)
    {
        AssemblyMetadataAttribute[] metadata = typeof(PlatformLiveUiMod).Assembly
            .GetCustomAttributes<AssemblyMetadataAttribute>()
            .ToArray();
        foreach (string key in keys)
        {
            string? value = metadata.FirstOrDefault(item =>
                    string.Equals(item.Key, key, StringComparison.Ordinal))
                ?.Value;
            if (!string.IsNullOrWhiteSpace(value))
                return value;
        }
        return "unavailable";
    }
}

internal enum PlatformCommandMode
{
    Human,
    Shadow,
    OneStep,
    Auto
}

internal sealed class PlatformLivePanel : IDisposable
{
    private readonly PlatformLiveStatusClient _statusClient = new();
    private readonly PlatformLiveLayoutState _defaultLayout = PlatformLiveLayoutState.Defaults;
    private readonly Dictionary<PlatformCommandMode, Button> _modeButtons = new();
    private readonly Dictionary<STS2HumanAnnotator.Core.RecordingCommandKind, Button> _recordingButtons = new();
    private readonly PlatformLiveActionAggregation _actionFeed = new();
    private readonly List<PlatformLiveToast> _toasts = new();
    private static readonly Color TextPrimary = new("#f3f6fb");
    private static readonly Color TextSecondary = new("#b9c5d6");
    private static readonly Color Accent = new("#62c4d8");
    private SceneTree? _tree;
    private Action? _processFrameHandler;
    private PanelContainer _workspace = null!;
    private Control _workspaceSurface = null!;
    private Button _launcher = null!;
    private Control _normalView = null!;
    private Control _compactView = null!;
    private Label _compactSummary = null!;
    private Label _compactRecent = null!;
    private Button _compactHumanButton = null!;
    private Control _resizeHandle = null!;
    private VBoxContainer _workspaceBody = null!;
    private VBoxContainer _workspaceContent = null!;
    private Control _surfaceViewport = null!;
    private ScrollContainer _agentRunPage = null!;
    private ScrollContainer _recorderPage = null!;
    private Label _agentRunSummary = null!;
    private Control _recorderDetails = null!;
    private ScrollContainer _toastViewport = null!;
    private VBoxContainer _toastStack = null!;
    private readonly List<Control> _surfaces = new();
    private Label _workspaceTitle = null!;
    private Label _recorderTitle = null!;
    private Label _recorderHealth = null!;
    private Label _recorderCountScope = null!;
    private OptionButton _recordingProfile = null!;
    private OptionButton _recordingSourceKind = null!;
    private LineEdit _recordingActor = null!;
    private Label _sourceDeclarationNotice = null!;
    private Label _lastAction = null!;
    private Control _decisionInspector = null!;
    private int _actionFeedPage;
    private string? _selectedActionIdentity;
    private Label _actionFeedPageLabel = null!;
    private VBoxContainer _actionFeedList = null!;
    private ScrollContainer _recorderScroll = null!;
    private Vector2 _dragStartPointerGlobal;
    private Vector2 _dragStartWorkspaceGlobal;
    private Vector2 _resizeStartPointerGlobal;
    private Vector2 _resizeStart;
    private bool _draggingWorkspace;
    private bool _resizingWorkspace;
    private TabBar _tabBar = null!;
    private PlatformLiveLayoutState _layout;
    private Label _connection = null!;
    private Label _command = null!;
    private Button _tickButton = null!;
    private PlatformCommandMode _mode = PlatformCommandMode.Human;
    private bool _disposed;
    private int _pollInFlight;
    private PlatformLiveStatus? _pendingStatus;
    private string? _displayedPolicyRunId;
    private Button _endTestButton = null!;
    private Button _workbenchButton = null!;
    private Button _externalWorkbenchButton = null!;
    private PlatformNativeWorkbenchPanel _nativeWorkbench = null!;
    private bool _policyCommandPending;
    private bool _nativeRuntimeRecoveryRequired;
    private readonly PlatformPolicyCommands _policyCommands = new();
    private readonly HttpClient _workbenchHttpClient = PlatformWorkbenchOpenClient.CreateHttpClient();
    private Task<PlatformWorkbenchOpenResult>? _workbenchOpenCheck;
    private readonly CancellationTokenSource _workbenchOpenLifetime = new();
    private long _policyUiIntent;
    private string? _pendingPollError;
    private long _lastRecordingEventSequence;
    private string? _actionFeedSessionId;

    internal Control Root { get; } = new()
    {
        Visible = true,
        MouseFilter = MouseFilterEnum.Ignore
    };

    internal PlatformLivePanel()
    {
        Root.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        _layout = PlatformLiveLayout.Load();
        BuildUi();

        var timer = new Godot.Timer
        {
            WaitTime = 1.0,
            Autostart = true,
            OneShot = false
        };
        timer.Timeout += OnPollTimeout;
        Root.AddChild(timer);
    }

    internal bool Mount(SceneTree tree)
    {
        try
        {
            _tree = tree;
            _processFrameHandler = OnProcessFrame;
            tree.ProcessFrame += _processFrameHandler;
            Root.TreeExiting += Dispose;
            ApplyLayout();
            Root.Resized += ApplyWorkspaceBounds;
            return true;
        }
        catch (Exception exception)
        {
            GD.PrintErr($"[STS2 Platform Live UI] panel mount failed: {exception}");
            return false;
        }
    }

    public void Dispose()
    {
        if (_disposed)
            return;
        _disposed = true;
        _policyCommands.InvalidatePending();
        _workbenchOpenLifetime.Cancel();
        Interlocked.Increment(ref _policyUiIntent);
        if (_tree != null && _processFrameHandler != null && GodotObject.IsInstanceValid(_tree))
            _tree.ProcessFrame -= _processFrameHandler;
        Root.Resized -= ApplyWorkspaceBounds;
        _statusClient.Dispose();
        _nativeWorkbench.Dispose();
        _workbenchHttpClient.Dispose();
        Task<PlatformWorkbenchOpenResult>? pendingOpen = _workbenchOpenCheck;
        if (pendingOpen is null)
            _workbenchOpenLifetime.Dispose();
        else
            _ = pendingOpen.ContinueWith(task =>
            {
                _ = task.Exception; // Observe a worker that raced with this panel's unload.
                _workbenchOpenLifetime.Dispose();
            }, CancellationToken.None, TaskContinuationOptions.ExecuteSynchronously, TaskScheduler.Default);
    }

    private static Shortcut WorkspaceShortcut(Key key) => new()
    {
        Events = new Godot.Collections.Array {
            new InputEventKey { Keycode = key },
            new InputEventKey { PhysicalKeycode = key }
        }
    };

    private void BuildUi()
    {
        _launcher = BuildHeaderButton("Platform", ShowPanel, "Open Human Recorder or Agent Run.");
        _launcher.Name = "PlatformLauncher";
        _launcher.Position = new Vector2(12, 12);
        _launcher.CustomMinimumSize = new Vector2(102, 32);
        Root.AddChild(_launcher);
        _workspace = new PanelContainer
        {
            Position = _layout.WorkspacePosition,
            Size = _layout.WorkspaceSize,
            CustomMinimumSize = new Vector2(640, 420),
            Visible = false,
            MouseFilter = MouseFilterEnum.Stop,
            ClipContents = true
        };
        _workspace.AddThemeStyleboxOverride("panel", MakePanelStyle(
            new Color("#111c2aef"), new Color("#4e9bb0"), 12, 2, 0));
        Root.AddChild(_workspace);

        _workspaceSurface = new Control
        {
            MouseFilter = MouseFilterEnum.Stop,
            ClipContents = true
        };
        _workspaceSurface.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        _workspaceSurface.GuiInput += OnWorkspaceInput;
        _workspace.AddChild(_workspaceSurface);

        var workspaceMargin = new MarginContainer
        {
            MouseFilter = MouseFilterEnum.Pass,
            ClipContents = true
        };
        workspaceMargin.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        workspaceMargin.AddThemeConstantOverride("margin_left", 12);
        workspaceMargin.AddThemeConstantOverride("margin_right", 12);
        workspaceMargin.AddThemeConstantOverride("margin_top", 10);
        workspaceMargin.AddThemeConstantOverride("margin_bottom", 10);
        _workspaceSurface.AddChild(workspaceMargin);
        _normalView = workspaceMargin;

        _workspaceBody = new VBoxContainer
        {
            MouseFilter = MouseFilterEnum.Pass,
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
            SizeFlagsVertical = SizeFlags.ExpandFill,
            ClipContents = true
        };
        _workspaceBody.AddThemeConstantOverride("separation", 7);
        workspaceMargin.AddChild(_workspaceBody);
        // Let empty title-bar space bubble to the bounded workspace drag handler;
        // child buttons still stop input themselves.
        var titleRow = new HBoxContainer
        {
            MouseFilter = MouseFilterEnum.Pass,
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
            ClipContents = true
        };
        titleRow.AddThemeConstantOverride("separation", 4);
        _workspaceTitle = new Label
        {
            Text = "PLATFORM",
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
            MouseFilter = MouseFilterEnum.Ignore,
            ClipText = true,
            TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis
        };
        _workspaceTitle.AddThemeFontSizeOverride("font_size", 18);
        _workspaceTitle.AddThemeColorOverride("font_color", TextPrimary);
        titleRow.AddChild(_workspaceTitle);
        _workbenchButton = BuildHeaderButton("工作台", () => SelectSurface(2), "Open the native project Workbench.");
        titleRow.AddChild(_workbenchButton);
        _externalWorkbenchButton = BuildHeaderButton("外部窗口", BeginOpenWorkbench, "Open the connected Workbench browser entry.");
        titleRow.AddChild(_externalWorkbenchButton);
        titleRow.AddChild(BuildHeaderButton("Minimize", MinimizePanel, "Keep a small live view during play."));
        titleRow.AddChild(BuildHeaderButton("Reset", ResetLayout, "Restore position, size and active surface."));
        var closeButton = BuildHeaderButton("收起", HidePanel, "Close workspace and return to gameplay.");
        closeButton.Shortcut = WorkspaceShortcut(Key.Escape);
        titleRow.AddChild(closeButton);
        _workspaceBody.AddChild(titleRow);

        _workspaceContent = new VBoxContainer
        {
            MouseFilter = MouseFilterEnum.Pass,
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
            SizeFlagsVertical = SizeFlags.ExpandFill,
            ClipContents = true
        };
        _workspaceContent.AddThemeConstantOverride("separation", 6);
        _workspaceBody.AddChild(_workspaceContent);

        _tabBar = new TabBar
        {
            MouseFilter = MouseFilterEnum.Stop,
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
            ClipTabs = true
        };
        _tabBar.AddThemeFontSizeOverride("font_size", 13);
        _tabBar.AddThemeColorOverride("font_selected_color", TextPrimary);
        _tabBar.AddThemeColorOverride("font_unselected_color", TextSecondary);
        foreach (string name in new[] { "模型实战", "真人采集", "工作台" })
            _tabBar.AddTab(name);
        _tabBar.TabClicked += OnTabClicked;
        _workspaceContent.AddChild(_tabBar);

        _surfaceViewport = new Control
        {
            MouseFilter = MouseFilterEnum.Stop,
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
            SizeFlagsVertical = SizeFlags.ExpandFill,
            CustomMinimumSize = new Vector2(0, 150),
            ClipContents = true
        };
        _workspaceContent.AddChild(_surfaceViewport);
        BuildAgentRunPage(_surfaceViewport);
        BuildRecorderPage(_surfaceViewport);
        _nativeWorkbench = new PlatformNativeWorkbenchPanel(command => _ = RunPolicyCommandAsync(command));
        _surfaceViewport.AddChild(_nativeWorkbench.Root);
        _surfaces.Add(_nativeWorkbench.Root);

        _toastStack = new VBoxContainer
        {
            MouseFilter = MouseFilterEnum.Pass,
            SizeFlagsHorizontal = SizeFlags.ExpandFill
        };
        _toastViewport = new ScrollContainer
        {
            MouseFilter = MouseFilterEnum.Stop,
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
            CustomMinimumSize = new Vector2(0, 46),
            HorizontalScrollMode = ScrollContainer.ScrollMode.Disabled,
            VerticalScrollMode = ScrollContainer.ScrollMode.Auto,
            ClipContents = true,
            Visible = false
        };
        _toastViewport.AddChild(_toastStack);
        _workspaceContent.AddChild(_toastViewport);

        var resizeHandle = new Label
        {
            Text = "↘",
            HorizontalAlignment = HorizontalAlignment.Center,
            VerticalAlignment = VerticalAlignment.Center,
            CustomMinimumSize = new Vector2(26, 26),
            Size = new Vector2(26, 26),
            MouseFilter = MouseFilterEnum.Stop,
            MouseDefaultCursorShape = CursorShape.Fdiagsize
        };
        resizeHandle.SetAnchorsAndOffsetsPreset(LayoutPreset.BottomRight, LayoutPresetMode.KeepSize);
        resizeHandle.GuiInput += OnResizeHandleInput;
        _workspaceSurface.AddChild(resizeHandle);
        _resizeHandle = resizeHandle;
        BuildCompactView();
        ApplySurfacePresentation(resizeWorkspace: false);
        ApplyLayout();
    }

    private void BuildCompactView()
    {
        var margin = new MarginContainer { MouseFilter = MouseFilterEnum.Pass, ClipContents = true };
        margin.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        foreach (string edge in new[] { "left", "right", "top", "bottom" })
            margin.AddThemeConstantOverride($"margin_{edge}", 10);
        var body = new VBoxContainer { MouseFilter = MouseFilterEnum.Pass, ClipContents = true };
        body.AddThemeConstantOverride("separation", 7);
        var header = new HBoxContainer { MouseFilter = MouseFilterEnum.Pass };
        _compactSummary = new Label {
            Text = "已录入 — / 真实失败 —", SizeFlagsHorizontal = SizeFlags.ExpandFill,
            MouseFilter = MouseFilterEnum.Ignore, ClipText = true
        };
        _compactSummary.AddThemeFontSizeOverride("font_size", 14);
        _compactSummary.AddThemeColorOverride("font_color", TextPrimary);
        header.AddChild(_compactSummary);
        Button restore = BuildHeaderButton("↗", RestorePanel, "Restore full workspace.");
        restore.CustomMinimumSize = new Vector2(32, 28);
        header.AddChild(restore);
        body.AddChild(header);
        _compactRecent = new Label {
            Text = "最新 3 条 · 尚无记录", MouseFilter = MouseFilterEnum.Ignore,
            SizeFlagsHorizontal = SizeFlags.ExpandFill, SizeFlagsVertical = SizeFlags.ExpandFill,
            ClipText = true, TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis
        };
        _compactRecent.AddThemeFontSizeOverride("font_size", 13);
        _compactRecent.AddThemeColorOverride("font_color", TextSecondary);
        body.AddChild(_compactRecent);
        _compactHumanButton = BuildCommandButton("Return to Human", () => _ = SetRuntimeModeAsync(PlatformCommandMode.Human),
            "Ask Policy Runtime to return control to Human.");
        _compactHumanButton.Disabled = true;
        body.AddChild(_compactHumanButton);
        margin.AddChild(body);
        _workspaceSurface.AddChild(margin);
        _compactView = margin;
    }

    private void BuildAgentRunPage(Control surfaceViewport)
    {
        _agentRunPage = new ScrollContainer
        {
            Name = "AgentRun",
            MouseFilter = MouseFilterEnum.Stop,
            HorizontalScrollMode = ScrollContainer.ScrollMode.Disabled,
            VerticalScrollMode = ScrollContainer.ScrollMode.Auto,
            ClipContents = true
        };
        _agentRunPage.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        var card = new PanelContainer
        {
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
            SizeFlagsVertical = SizeFlags.ExpandFill,
            MouseFilter = MouseFilterEnum.Pass,
            ClipContents = true
        };
        card.AddThemeStyleboxOverride("panel", PageStyle("Agent Run"));
        var margin = new MarginContainer { MouseFilter = MouseFilterEnum.Ignore };
        margin.AddThemeConstantOverride("margin_left", 14);
        margin.AddThemeConstantOverride("margin_right", 14);
        margin.AddThemeConstantOverride("margin_top", 12);
        margin.AddThemeConstantOverride("margin_bottom", 12);
        var body = new VBoxContainer
        {
            MouseFilter = MouseFilterEnum.Stop,
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
            SizeFlagsVertical = SizeFlags.ExpandFill
        };
        body.AddThemeConstantOverride("separation", 8);
        margin.AddChild(body);
        card.AddChild(margin);
        _agentRunPage.AddChild(card);

        var title = new Label
        {
            Text = "AGENT RUN",
            MouseFilter = MouseFilterEnum.Ignore
        };
        title.AddThemeFontSizeOverride("font_size", 17);
        title.AddThemeColorOverride("font_color", TextPrimary);
        body.AddChild(title);

        _connection = new Label
        {
            Text = "Connector: polling...",
            MouseFilter = MouseFilterEnum.Ignore,
            AutowrapMode = TextServer.AutowrapMode.WordSmart,
            ClipText = true
        };
        _connection.AddThemeFontSizeOverride("font_size", 13);
        _connection.AddThemeColorOverride("font_color", TextSecondary);
        body.AddChild(_connection);

        _agentRunSummary = new Label
        {
            Text = "Policy Runtime: waiting for typed status...",
            MouseFilter = MouseFilterEnum.Ignore,
            AutowrapMode = TextServer.AutowrapMode.WordSmart,
            SizeFlagsHorizontal = SizeFlags.ExpandFill
        };
        _agentRunSummary.AddThemeFontSizeOverride("font_size", 13);
        _agentRunSummary.AddThemeColorOverride("font_color", TextPrimary);
        body.AddChild(_agentRunSummary);

        var modeRow = new HBoxContainer
        {
            MouseFilter = MouseFilterEnum.Pass,
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
            ClipContents = true
        };
        modeRow.AddThemeConstantOverride("separation", 4);
        modeRow.AddChild(BuildModeButton("开始测试", PlatformCommandMode.Auto));
        modeRow.AddChild(BuildModeButton("暂停并接管", PlatformCommandMode.Human));
        _endTestButton = BuildCommandButton("结束测试", () => _ = EndRuntimeAsync(), "停止模型并封存本次实战记录。");
        _endTestButton.Disabled = true;
        modeRow.AddChild(_endTestButton);
        body.AddChild(modeRow);
        var advancedModes = new HBoxContainer { Visible = false };
        advancedModes.AddChild(BuildModeButton("只评分", PlatformCommandMode.Shadow));
        advancedModes.AddChild(BuildModeButton("单步执行", PlatformCommandMode.OneStep));
        body.AddChild(BuildHeaderButton("高级控制", () => advancedModes.Visible = !advancedModes.Visible,
            "按需查看只评分与单步调试。"));
        _tickButton = BuildCommandButton(
            "Tick",
            () => _ = TickRuntimeAsync(),
            "Ask Policy Runtime for one bounded tick; action authority remains Connector/Runtime.");
        _tickButton.Disabled = true;
        advancedModes.AddChild(_tickButton);
        body.AddChild(advancedModes);

        _command = new Label
        {
            Text = "先在本机工作台选择并准备模型，再点开始测试。准备模型不会自动操作游戏。",
            MouseFilter = MouseFilterEnum.Ignore,
            AutowrapMode = TextServer.AutowrapMode.WordSmart
        };
        _command.AddThemeFontSizeOverride("font_size", 13);
        _command.AddThemeColorOverride("font_color", Accent);
        body.AddChild(_command);
        surfaceViewport.AddChild(_agentRunPage);
        _surfaces.Add(_agentRunPage);
    }

    private void BuildRecorderPage(Control surfaceViewport)
    {
        _recorderPage = new ScrollContainer
        {
            Name = "Recorder",
            MouseFilter = MouseFilterEnum.Stop,
            HorizontalScrollMode = ScrollContainer.ScrollMode.Disabled,
            VerticalScrollMode = ScrollContainer.ScrollMode.Auto,
            ClipContents = true
        };
        _recorderPage.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        _recorderScroll = _recorderPage;

        var card = new PanelContainer
        {
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
            SizeFlagsVertical = SizeFlags.ExpandFill,
            MouseFilter = MouseFilterEnum.Pass,
            ClipContents = true
        };
        card.AddThemeStyleboxOverride("panel", PageStyle("Recorder"));
        var body = new VBoxContainer { MouseFilter = MouseFilterEnum.Stop };
        body.AddThemeConstantOverride("separation", 8);
        body.SizeFlagsHorizontal = SizeFlags.ExpandFill;
        body.SizeFlagsVertical = SizeFlags.ExpandFill;
        var margin = new MarginContainer { MouseFilter = MouseFilterEnum.Ignore };
        margin.AddThemeConstantOverride("margin_left", 14);
        margin.AddThemeConstantOverride("margin_right", 14);
        margin.AddThemeConstantOverride("margin_top", 10);
        margin.AddThemeConstantOverride("margin_bottom", 10);
        margin.SizeFlagsHorizontal = SizeFlags.ExpandFill;
        margin.SizeFlagsVertical = SizeFlags.ExpandFill;
        margin.AddChild(body);
        card.AddChild(margin);
        _recorderPage.AddChild(card);
        // Recorder is a first-class Workspace tab; lifecycle buttons remain interactive.
        _recorderTitle = new Label
        {
            Text = "RECORDER",
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
            MouseFilter = MouseFilterEnum.Ignore
        };
        _recorderTitle.AddThemeFontSizeOverride("font_size", 17);
        _recorderTitle.AddThemeColorOverride("font_color", TextPrimary);
        var recorderHeader = new HBoxContainer();
        recorderHeader.AddChild(_recorderTitle);
        recorderHeader.AddChild(BuildHeaderButton("Details", () =>
            _recorderCountScope.Visible = !_recorderCountScope.Visible,
            "Show session accounting and retained-view status."));
        body.AddChild(recorderHeader);

        _recorderDetails = new VBoxContainer
        {
            MouseFilter = MouseFilterEnum.Stop,
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
            SizeFlagsVertical = SizeFlags.ExpandFill
        };
        _recorderDetails.AddThemeConstantOverride("separation", 6);
        body.AddChild(_recorderDetails);

        _recorderHealth = new Label
        {
            Text = "Session: none · health: waiting",
            AutowrapMode = TextServer.AutowrapMode.WordSmart,
            MouseFilter = MouseFilterEnum.Ignore
        };
        _recorderHealth.AddThemeFontSizeOverride("font_size", 14);
        _recorderHealth.AddThemeColorOverride("font_color", Accent);
        _recorderDetails.AddChild(_recorderHealth);

        _recorderCountScope = new Label
        {
            Text = "Session totals come from Annotator; the list below is a retained view.",
            Visible = false,
            AutowrapMode = TextServer.AutowrapMode.WordSmart,
            MouseFilter = MouseFilterEnum.Ignore
        };
        _recorderCountScope.AddThemeFontSizeOverride("font_size", 11);
        _recorderCountScope.AddThemeColorOverride("font_color", TextSecondary);
        _recorderDetails.AddChild(_recorderCountScope);

        var declaration = new HBoxContainer { MouseFilter = MouseFilterEnum.Stop };
        _recordingProfile = new OptionButton();
        _recordingProfile.AddItem("兼容语义录制", 0);
        _recordingProfile.AddItem("原生交互与观察", 1);
        _recordingProfile.Selected = 0;
        declaration.AddChild(_recordingProfile);
        _recordingSourceKind = new OptionButton();
        _recordingSourceKind.AddItem("请选择来源声明", 0);
        _recordingSourceKind.AddItem("本人操作", 1);
        _recordingSourceKind.AddItem("AI 界面操作", 2);
        _recordingSourceKind.AddItem("Agent 协议", 3);
        _recordingSourceKind.AddItem("未知来源", 4);
        declaration.AddChild(_recordingSourceKind);
        _recordingActor = new LineEdit { PlaceholderText = "操作者 ID（字母、数字、_、-、.）", MaxLength = 128,
            SizeFlagsHorizontal = SizeFlags.ExpandFill };
        declaration.AddChild(_recordingActor);
        _recorderDetails.AddChild(declaration);
        _sourceDeclarationNotice = new Label { Text = "来源由操作员明确声明；不是机器验证的真人证明。",
            AutowrapMode = TextServer.AutowrapMode.WordSmart };
        _recorderDetails.AddChild(_sourceDeclarationNotice);
        _recordingProfile.ItemSelected += _ => RefreshRecordingControls();
        _recordingSourceKind.ItemSelected += _ => RefreshRecordingControls();
        _recordingActor.TextChanged += _ => RefreshRecordingControls();

        var controls = new HBoxContainer { MouseFilter = MouseFilterEnum.Stop };
        controls.AddThemeConstantOverride("separation", 4);
        _recorderDetails.AddChild(controls);
        _recordingButtons[STS2HumanAnnotator.Core.RecordingCommandKind.StartNewSession] = BuildRecordingButton(
            controls, "开始录制", STS2HumanAnnotator.Core.RecordingCommandKind.StartNewSession);
        _recordingButtons[STS2HumanAnnotator.Core.RecordingCommandKind.Pause] = BuildRecordingButton(
            controls, "暂停", STS2HumanAnnotator.Core.RecordingCommandKind.Pause);
        _recordingButtons[STS2HumanAnnotator.Core.RecordingCommandKind.Resume] = BuildRecordingButton(
            controls, "继续", STS2HumanAnnotator.Core.RecordingCommandKind.Resume);
        _recordingButtons[STS2HumanAnnotator.Core.RecordingCommandKind.Close] = BuildRecordingButton(
            controls, "结束录制", STS2HumanAnnotator.Core.RecordingCommandKind.Close);
        _recordingButtons[STS2HumanAnnotator.Core.RecordingCommandKind.ChangeSource] = BuildRecordingButton(
            controls, "更改来源", STS2HumanAnnotator.Core.RecordingCommandKind.ChangeSource);

        _lastAction = new Label
        {
            Text = "Select a decision to inspect its recorded evidence.",
            AutowrapMode = TextServer.AutowrapMode.WordSmart,
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
            MouseFilter = MouseFilterEnum.Ignore
        };
        _lastAction.AddThemeFontSizeOverride("font_size", 13);
        _lastAction.AddThemeColorOverride("font_color", TextSecondary);
        var inspector = new VBoxContainer { Visible = false };
        var inspectorHeader = new HBoxContainer();
        var inspectorTitle = new Label { Text = "DECISION EVIDENCE", SizeFlagsHorizontal = SizeFlags.ExpandFill };
        inspectorTitle.AddThemeFontSizeOverride("font_size", 12);
        inspectorHeader.AddChild(inspectorTitle);
        inspectorHeader.AddChild(BuildHeaderButton("Hide", () => {
            _selectedActionIdentity = null;
            _decisionInspector.Visible = false;
        }, "Hide selected decision evidence."));
        inspector.AddChild(inspectorHeader);
        var detailScroll = new ScrollContainer {
            CustomMinimumSize = new Vector2(0, 150),
            HorizontalScrollMode = ScrollContainer.ScrollMode.Disabled,
            VerticalScrollMode = ScrollContainer.ScrollMode.Auto
        };
        detailScroll.AddChild(_lastAction);
        inspector.AddChild(detailScroll);
        _decisionInspector = inspector;
        _recorderDetails.AddChild(inspector);

        var feedHeading = new Label
        {
            Text = "RECENT DECISIONS",
            MouseFilter = MouseFilterEnum.Ignore
        };
        feedHeading.AddThemeFontSizeOverride("font_size", 12);
        feedHeading.AddThemeColorOverride("font_color", TextPrimary);
        _recorderDetails.AddChild(feedHeading);
        var pages = new HBoxContainer();
        pages.AddChild(BuildCommandButton("Newer", () => { _actionFeedPage = Math.Max(0, _actionFeedPage - 1); RenderActionFeed(); }));
        pages.AddChild(BuildCommandButton("Older", () => { _actionFeedPage = Math.Min(Math.Max(0, (_actionFeed.Count - 1) / PlatformLiveActionFeed.MaxEntries), _actionFeedPage + 1); RenderActionFeed(); }));
        _actionFeedPageLabel = new Label();
        pages.AddChild(_actionFeedPageLabel);
        _recorderDetails.AddChild(pages);
        _actionFeedList = new VBoxContainer
        {
            MouseFilter = MouseFilterEnum.Ignore,
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
            CustomMinimumSize = new Vector2(0, 84)
        };
        _actionFeedList.AddThemeConstantOverride("separation", 4);
        _recorderDetails.AddChild(_actionFeedList);
        surfaceViewport.AddChild(_recorderPage);
        _surfaces.Add(_recorderPage);
    }

    private Button BuildRecordingButton(
        Container body,
        string text,
        STS2HumanAnnotator.Core.RecordingCommandKind kind)
    {
        var button = BuildCommandButton(text, () => ApplyRecordingCommand(kind));
        button.SizeFlagsHorizontal = SizeFlags.ExpandFill;
        button.CustomMinimumSize = new Vector2(76, 34);
        button.AddThemeFontSizeOverride("font_size", 12);
        body.AddChild(button);
        return button;
    }

    private Button BuildModeButton(string text, PlatformCommandMode mode)
    {
        var button = new Button
        {
            Text = text,
            TooltipText = mode == PlatformCommandMode.OneStep
                ? "Run exactly one Policy Runtime decision, then return to Human."
                : $"Set Policy Runtime mode to {text}; the UI never submits a BoundAction directly.",
            FocusMode = FocusModeEnum.None,
            MouseFilter = MouseFilterEnum.Stop,
            CustomMinimumSize = new Vector2(88, 34),
            ToggleMode = true,
            Disabled = true
        };
        ApplyButtonTheme(button, mode == PlatformCommandMode.Human);
        button.Pressed += () => _ = SetRuntimeModeAsync(mode);
        _modeButtons.Add(mode, button);
        return button;
    }

    private static Button BuildCommandButton(string text, Action action, string? tooltip = null)
    {
        var button = new Button
        {
            Text = text,
            TooltipText = tooltip ?? $"Annotator recording control: {text}",
            FocusMode = FocusModeEnum.None,
            MouseFilter = MouseFilterEnum.Stop,
            CustomMinimumSize = new Vector2(96, 34)
        };
        ApplyButtonTheme(button, false);
        button.Pressed += action;
        return button;
    }

    private static Button BuildHeaderButton(string text, Action action, string? tooltip = null)
    {
        Button button = BuildCommandButton(text, action, tooltip);
        button.CustomMinimumSize = new Vector2(88, 34);
        button.AddThemeFontSizeOverride("font_size", 12);
        button.ClipText = true;
        return button;
    }

    private static StyleBoxFlat MakePanelStyle(
        Color background,
        Color border,
        int radius,
        int borderWidth,
        int padding)
    {
        var style = new StyleBoxFlat
        {
            BgColor = background,
            BorderColor = border,
            CornerRadiusTopLeft = radius,
            CornerRadiusTopRight = radius,
            CornerRadiusBottomLeft = radius,
            CornerRadiusBottomRight = radius,
            ContentMarginLeft = padding,
            ContentMarginRight = padding,
            ContentMarginTop = padding,
            ContentMarginBottom = padding
        };
        style.SetBorderWidthAll(borderWidth);
        return style;
    }

    private static void ApplyButtonTheme(Button button, bool selected)
    {
        button.AddThemeFontSizeOverride("font_size", 14);
        button.AddThemeColorOverride("font_color", TextPrimary);
        button.AddThemeColorOverride("font_hover_color", TextPrimary);
        button.AddThemeColorOverride("font_pressed_color", TextPrimary);
        button.AddThemeColorOverride("font_disabled_color", new Color("#708092"));
        button.AddThemeStyleboxOverride("normal", MakePanelStyle(
            selected ? new Color("#245b6b") : new Color("#263442"),
            selected ? Accent : new Color("#4b5c6e"), 7, 1, 8));
        button.AddThemeStyleboxOverride("hover", MakePanelStyle(
            new Color("#315267"), Accent, 7, 1, 8));
        button.AddThemeStyleboxOverride("pressed", MakePanelStyle(
            new Color("#1e819b"), new Color("#9ae8f5"), 7, 2, 8));
        button.AddThemeStyleboxOverride("disabled", MakePanelStyle(
            selected ? new Color("#233b45") : new Color("#1a232d"),
            selected ? new Color("#4d8a98") : new Color("#34404d"), 7, 1, 8));
        button.AddThemeStyleboxOverride("focus", MakePanelStyle(
            new Color("#315267"), Accent, 7, 2, 8));
    }

    private void OnWorkspaceInput(InputEvent @event)
    {
        if (@event is InputEventMouseButton mouseButton)
        {
            if (mouseButton.ButtonIndex == MouseButton.Left && mouseButton.Pressed)
            {
                if (!_layout.Compact && mouseButton.Position.X >= _workspace.Size.X - 32
                    && mouseButton.Position.Y >= _workspace.Size.Y - 32)
                {
                    _resizingWorkspace = true;
                    _resizeStartPointerGlobal = mouseButton.GlobalPosition;
                    _resizeStart = _workspace.Size;
                    _workspace.GetViewport().SetInputAsHandled();
                }
                else
                {
                    _draggingWorkspace = true;
                    _dragStartPointerGlobal = mouseButton.GlobalPosition;
                    _dragStartWorkspaceGlobal = _workspace.GlobalPosition;
                }
            }
            else if (mouseButton.ButtonIndex == MouseButton.Left && !mouseButton.Pressed)
            {
                if (_resizingWorkspace || _draggingWorkspace)
                    PersistLayout();
                _resizingWorkspace = false;
                _draggingWorkspace = false;
            }
        }
        else if (@event is InputEventMouseMotion motion && (_resizingWorkspace || _draggingWorkspace))
        {
            if (_resizingWorkspace)
            {
                Vector2 delta = motion.GlobalPosition - _resizeStartPointerGlobal;
                Rect2 clamped = PlatformLiveLayout.ClampWorkspace(
                    new Rect2(_workspace.Position, _resizeStart + delta),
                    Root.Size);
                _workspace.Position = clamped.Position;
                _workspace.Size = clamped.Size;
            }
            else
            {
                Vector2 requested = _dragStartWorkspaceGlobal
                    + motion.GlobalPosition - _dragStartPointerGlobal;
                _workspace.GlobalPosition = PlatformLiveLayout.ClampWorkspace(
                    new Rect2(requested, _workspace.Size),
                    Root.Size, _layout.Compact).Position;
            }
            _workspace.GetViewport().SetInputAsHandled();
        }
    }

    private void OnResizeHandleInput(InputEvent @event)
    {
        if (@event is InputEventMouseButton mouseButton
            && mouseButton.ButtonIndex == MouseButton.Left)
        {
            if (mouseButton.Pressed)
            {
                _resizingWorkspace = true;
                _resizeStartPointerGlobal = mouseButton.GlobalPosition;
                _resizeStart = _workspace.Size;
            }
            else
            {
                if (_resizingWorkspace)
                    PersistLayout();
                _resizingWorkspace = false;
            }
            _workspace.GetViewport().SetInputAsHandled();
        }
        else if (@event is InputEventMouseMotion motion && _resizingWorkspace)
        {
            Vector2 delta = motion.GlobalPosition - _resizeStartPointerGlobal;
            Rect2 clamped = PlatformLiveLayout.ClampWorkspace(
                new Rect2(_workspace.Position, _resizeStart + delta),
                Root.Size);
            _workspace.Position = clamped.Position;
            _workspace.Size = clamped.Size;
            _workspace.GetViewport().SetInputAsHandled();
        }
    }

    private void OnTabClicked(long tab) => SelectSurface((int)tab);

    private void SelectSurface(int surface)
    {
        string selectedSurface = surface switch { 0 => "agent_run", 1 => "human_recorder", _ => "workbench" };
        PlatformLiveLayoutState next = PlatformLiveLayout.SelectSurface(_layout, selectedSurface);
        if (next == _layout)
            return;
        _layout = next;
        ApplySurfacePresentation();
        PersistLayout();
        RefreshVisibleStatus();
    }

    private void ApplySurfacePresentation(bool resizeWorkspace = true)
    {
        int activeSurface = _layout.ActiveSurface switch { "human_recorder" => 1, "workbench" => 2, _ => 0 };
        _tabBar.CurrentTab = activeSurface;
        for (int index = 0; index < _surfaces.Count; index++)
            _surfaces[index].Visible = index == activeSurface;
        if (resizeWorkspace)
            ApplyWorkspaceBounds();
    }

    private void ResetLayout()
    {
        _layout = _defaultLayout;
        ApplyLayout();
        foreach (Control page in _surfaces)
            if (page is ScrollContainer scroll)
                scroll.ScrollVertical = 0;
        PersistLayout();
        PushToast("layout.reset", "Layout reset to defaults.");
    }

    private void ApplyLayout()
    {
        ApplyPresentationVisibility();
        ApplyWorkspaceBounds();
        ApplySurfacePresentation(resizeWorkspace: false);
    }

    private void ApplyWorkspaceBounds()
    {
        _workspace.CustomMinimumSize = _layout.Compact
            ? PlatformLiveLayout.CompactSize : PlatformLiveLayout.NormalMinimumSize;
        Vector2 requestedSize = _layout.Compact ? PlatformLiveLayout.CompactSize : _layout.WorkspaceSize;
        Rect2 workspace = PlatformLiveLayout.ClampWorkspace(
            new Rect2(_layout.WorkspacePosition, requestedSize),
            Root.Size, _layout.Compact);
        _workspace.Position = workspace.Position;
        _workspace.Size = workspace.Size;
    }

    private void ApplyPresentationVisibility()
    {
        bool workspaceVisible = _workspace.Visible;
        _launcher.Visible = !workspaceVisible;
        _normalView.Visible = !_layout.Compact;
        _compactView.Visible = _layout.Compact;
        _resizeHandle.Visible = !_layout.Compact;
        _compactHumanButton.Visible = _layout.ActiveSurface == "agent_run" || _layout.ActiveSurface == "workbench";
        _toastViewport.Visible = workspaceVisible && !_layout.Compact && _toasts.Count > 0;
    }

    private void PersistLayout()
    {
        _layout = _layout with
        {
            WorkspacePosition = _workspace.Position,
            WorkspaceSize = _layout.Compact ? _layout.WorkspaceSize : _workspace.Size
        };
        if (!PlatformLiveLayout.Save(_layout))
            PushToast("layout.persistence", "Layout could not be saved; using this session only.");
    }

    private void PushToast(string key, string message)
    {
        DateTimeOffset now = DateTimeOffset.UtcNow;
        _toasts.RemoveAll(toast => toast.Key == key);
        _toasts.Add(new PlatformLiveToast(key, message, now.AddSeconds(4)));
        while (_toasts.Count > 4)
            _toasts.RemoveAt(0);
        RenderToasts();
    }

    private void RenderToasts()
    {
        if (_toastStack == null)
            return;
        foreach (Node child in _toastStack.GetChildren())
            child.QueueFree();
        foreach (PlatformLiveToast toast in _toasts)
        {
            var item = new Button
            {
                Text = $"{toast.Message}  ×",
                TooltipText = "Dismiss notification",
                MouseFilter = MouseFilterEnum.Stop,
                FocusMode = FocusModeEnum.None,
                SizeFlagsHorizontal = SizeFlags.ExpandFill,
                ClipText = true
            };
            ApplyButtonTheme(item, false);
            item.Pressed += () =>
            {
                _toasts.RemoveAll(current => current.Key == toast.Key);
                RenderToasts();
            };
            _toastStack.AddChild(item);
        }
        ApplyPresentationVisibility();
    }

    private void ExpireToasts()
    {
        DateTimeOffset now = DateTimeOffset.UtcNow;
        if (_toasts.RemoveAll(toast => toast.ExpiresAt <= now) > 0)
            RenderToasts();
    }

    private static StyleBoxFlat PageStyle(string name)
    {
        Color background = name switch
        {
            "Recorder" => new Color("#1b3338ed"),
            "Agent Run" => new Color("#1b2d3be8"),
            _ => new Color("#222d38e8")
        };
        Color border = name switch
        {
            "Recorder" => new Color("#4d9b8c"),
            "Agent Run" => new Color("#3e7992"),
            _ => new Color("#536677")
        };
        return MakePanelStyle(background, border, 8, 1, 4);
    }

    private void ApplyRecordingCommand(STS2HumanAnnotator.Core.RecordingCommandKind kind)
    {
        try
        {
            var owner = STS2HumanAnnotator.Mod.RecordingApplicationService.Instance;
            var before = owner.QueryStatus();
            bool sourceStart = kind == STS2HumanAnnotator.Core.RecordingCommandKind.StartNewSession && _recordingProfile.Selected == 1;
            var declaration = sourceStart || kind == STS2HumanAnnotator.Core.RecordingCommandKind.ChangeSource
                ? ReadSourceDeclaration() : null;
            var command = PlatformRecordingCommands.ForStatus(before, kind, Guid.NewGuid().ToString("D"),
                sourceStart ? STS2HumanAnnotator.Core.SourceSessionContractV3.ProfileId : null, declaration);
            string runtime = STS2Connector.PlayerEnvironment.PlayerEnvironmentService.GetPlayerEnvironmentControlSnapshot().RuntimeInstanceId;
            var result = PlatformRecordingCommands.Execute(new(runtime, before.Lifecycle.SessionId, command),
                () => STS2Connector.PlayerEnvironment.PlayerEnvironmentService.GetPlayerEnvironmentControlSnapshot().RuntimeInstanceId,
                owner.QueryStatus, owner.ExecuteForSession);
            _command.Text = result.Accepted ? kind switch {
                STS2HumanAnnotator.Core.RecordingCommandKind.StartNewSession => "录制已开始。",
                STS2HumanAnnotator.Core.RecordingCommandKind.Pause => "录制已暂停。",
                STS2HumanAnnotator.Core.RecordingCommandKind.Resume => "录制已恢复。",
                STS2HumanAnnotator.Core.RecordingCommandKind.ChangeSource => "来源声明已更新。",
                _ => result.Status.RecordingLifecycle == "closed" ? "录制已结束。" : "录制正在封存；请等待实际完成。"
            } : "录制操作被拒绝：" + result.Code;
            PushToast($"recording.{kind}", _command.Text);
            var authoritative = owner.QueryStatus();
            RefreshActionFeed(authoritative);
            ApplyRecordingAvailability(authoritative);
        }
        catch (Exception error) when (error is ArgumentException or InvalidDataException or InvalidOperationException)
        { _command.Text = "录制操作未确认：" + PlatformRecordingCommands.PublicCode(error.Message, "source_declaration_required"); }
    }

    private STS2HumanAnnotator.Core.SourceDeclaration ReadSourceDeclaration()
    {
        string kind = _recordingSourceKind.Selected switch { 1 => "declared_human", 2 => "agent_native_ui",
            3 => "agent_protocol", 4 => "unknown", _ => throw new ArgumentException("source_declaration_required") };
        var declaration = new STS2HumanAnnotator.Core.SourceDeclaration(kind, _recordingActor.Text,
            Guid.NewGuid().ToString("D"), false);
        STS2HumanAnnotator.Core.SourceSessionContract.Validate(declaration);
        return declaration;
    }

    private bool HasSourceDeclaration()
    {
        if (_recordingSourceKind.Selected == 0) return false;
        try { STS2HumanAnnotator.Core.SourceSessionContract.Identifier(_recordingActor.Text); return true; }
        catch (InvalidDataException) { return false; }
    }

    private static string SourceKindLabel(string? kind) => kind switch { "declared_human" => "本人操作",
        "agent_native_ui" => "AI 界面操作", "agent_protocol" => "Agent 协议", _ => "未知来源" };

    private void RefreshRecordingControls()
    {
        if (_recordingButtons.Count == 5)
            ApplyRecordingAvailability(STS2HumanAnnotator.Mod.RecordingApplicationService.Instance.QueryStatus());
    }

    private void OnPollTimeout() => RefreshVisibleStatus();

    private void RefreshVisibleStatus()
    {
        if (!_workspace.Visible || _disposed)
            return;
        if (_layout.ActiveSurface == "human_recorder")
        {
            var recording = STS2HumanAnnotator.Mod.RecordingApplicationService.Instance.QueryStatus();
            RefreshActionFeed(recording);
            ApplyRecordingAvailability(recording);
        }
        else if (_layout.ActiveSurface == "workbench")
        {
            _nativeWorkbench.Refresh(!_layout.Compact);
            _ = PollAsync(); // Direct Policy recovery stays independent of Workbench pairing.
            if (_layout.Compact)
            {
                _compactSummary.Text = "本机工作台 · 独立后台任务";
                _compactRecent.Text = "训练/上传由工作台服务拥有。\n关闭面板不会停止任务。";
            }
        }
        else
            _ = PollAsync();
    }

    private Task SetRuntimeModeAsync(PlatformCommandMode mode) => RunPolicyCommandAsync(mode switch
    {
        PlatformCommandMode.Human => PlatformPolicyCommand.Human,
        PlatformCommandMode.Shadow => PlatformPolicyCommand.Shadow,
        PlatformCommandMode.OneStep => PlatformPolicyCommand.OneStep,
        PlatformCommandMode.Auto => PlatformPolicyCommand.Auto,
        _ => throw new ArgumentOutOfRangeException(nameof(mode))
    });

    private Task EndRuntimeAsync() => RunPolicyCommandAsync(PlatformPolicyCommand.Stop);
    private Task TickRuntimeAsync() => RunPolicyCommandAsync(PlatformPolicyCommand.Tick);

    private async Task RunPolicyCommandAsync(PlatformPolicyCommand command)
    {
        bool recovery = command is PlatformPolicyCommand.Human or PlatformPolicyCommand.Stop;
        if (_policyCommandPending && !recovery) return;
        long intent = Interlocked.Increment(ref _policyUiIntent);
        _policyCommandPending = true;
        _command.Text = recovery ? "正在归还控制，请等待实际回执…" : "正在准备模型测试…";
        try
        {
            string expected = _displayedPolicyRunId
                ?? throw new InvalidOperationException("请先加载模型并读取其状态。");
            PlatformPolicyBinding? binding = null;
            IPlatformRuntimeStatus response = await _policyCommands.RunAsync(
                expected, command,
                async () => {
                    string game = STS2Connector.PlayerEnvironment.PlayerEnvironmentService.GetPlayerEnvironmentControlSnapshot().RuntimeInstanceId;
                    var recording = STS2HumanAnnotator.Mod.RecordingApplicationService.Instance.QueryStatus();
                    binding = await _statusClient.ObserveBindingAsync(expected, game);
                    var modelContext = await _statusClient.ObserveModelContextAsync(binding);
                    if (intent != Interlocked.Read(ref _policyUiIntent) || _disposed)
                        throw new PlatformPolicyCommandSupersededException();
                    var prepared = await PlatformRecordingCommands.OnMainThread(() => {
                        if (intent != Interlocked.Read(ref _policyUiIntent) || _disposed)
                            throw new PlatformPolicyCommandSupersededException();
                        if (STS2Connector.PlayerEnvironment.PlayerEnvironmentService.GetPlayerEnvironmentControlSnapshot().RuntimeInstanceId != game)
                            throw new InvalidOperationException("recording_game_instance_changed");
                        return PlatformCollectionHandoff.PrepareForModel(new(
                            PlatformRecordingCommands.ModelRequestSchemaV2, game, recording.Lifecycle.SessionId,
                            recording.SourceV2?.SegmentId ?? recording.Source?.SegmentId, Guid.NewGuid().ToString("D"), modelContext),
                            () => STS2Connector.PlayerEnvironment.PlayerEnvironmentService.GetPlayerEnvironmentControlSnapshot().RuntimeInstanceId,
                            STS2HumanAnnotator.Mod.RecordingApplicationService.Instance.QueryStatus,
                            STS2HumanAnnotator.Mod.RecordingApplicationService.Instance.ExecuteForSession);
                    }, STS2Connector.ConnectorMod.RunOnMainThread);
                    if (!prepared.ReadyForModel)
                        throw new InvalidOperationException(prepared.RecordingDisposition == "paused"
                            ? "协议录制已暂停。请明确继续或结束录制后再开始模型；模型尚未接管。"
                            : "录制正在封存或健康状态阻断准备；模型尚未接管。请查看录制状态。");
                },
                mode => _statusClient.SetModeAsync(mode, expected, binding),
                () => _statusClient.TickAsync(expected, binding ?? throw new InvalidOperationException("Game binding unavailable.")),
                () => _statusClient.StopAsync(expected));
            if (intent != Interlocked.Read(ref _policyUiIntent) || _disposed) return;
            _mode = ParseRuntimeMode(response.Mode);
            ApplyModeButtonState();
            _command.Text = command == PlatformPolicyCommand.Stop
                ? response.Lifecycle == "stopped" ? "本次测试已结束。工作台会整理实战记录。" : "正在等待停止回执。"
                : $"模型状态：{response.Mode}。";
            RefreshVisibleStatus();
        }
        catch (PlatformPolicyCommandSupersededException) { /* A newer user intent owns the UI. */ }
        catch (Exception exception)
        {
            if (intent != Interlocked.Read(ref _policyUiIntent) || _disposed) return;
            _command.Text = $"操作未确认：{exception.Message}。请查看状态，不要重复决策。";
            PushToast("policy.error", exception.Message);
            SetPolicyControlsAvailable(_displayedPolicyRunId != null);
        }
        finally
        {
            if (intent == Interlocked.Read(ref _policyUiIntent)) _policyCommandPending = false;
        }
    }

    private async Task PollAsync()
    {
        if (Interlocked.Exchange(ref _pollInFlight, 1) != 0)
            return;
        try
        {
            PlatformLiveStatus status = await _statusClient.ReadAsync();
            Interlocked.Exchange(ref _pendingStatus, status);
        }
        catch (Exception exception)
        {
            Interlocked.Exchange(ref _pendingPollError, exception.GetType().Name);
        }
        finally
        {
            Volatile.Write(ref _pollInFlight, 0);
        }
    }

    private void ApplyPendingStatus()
    {
        if (Interlocked.Exchange(ref _pendingStatus, null) is { } status)
        {
            ApplyStatus(status);
        }
    }

    private void ApplyPendingPollError()
    {
        if (Interlocked.Exchange(ref _pendingPollError, null) is { } error)
        {
            _connection.Text = $"Connector loopback: UI poll failed ({error})";
            ApplyRecordingAvailability(
                STS2HumanAnnotator.Mod.RecordingApplicationService.Instance.QueryStatus());
            PushToast("transport.error", $"Status transport unavailable: {error}");
        }
    }

    private void ApplyStatus(PlatformLiveStatus status)
    {
        // Keep the last exact target for explicit recovery while status is offline.
        // A replacement Runtime rejects that old run ID at its mutation owner.
        _displayedPolicyRunId = status.PolicyRuntime?.RunId ?? _displayedPolicyRunId;
        _connection.Text =
            $"Connector: {status.TransportStatus} | Policy Runtime: {status.PolicyRuntimeTransportStatus} | observed {status.ObservedAt:HH:mm:ss} UTC";
        string policyReason = PlatformLiveLayout.PolicyUnavailableReason(status);
        _nativeRuntimeRecoveryRequired = status.PolicyRuntime is NativeAgentRuntimeStatus native
            && (native.Tainted || native.PendingRequest is not null || native.Controller == "unknown"
                || native.Session?.AgentState == "uncertain");
        SetPolicyControlsAvailable(status.PolicyRuntime != null, policyReason);
        if (status.PolicyRuntime != null)
            _mode = ParseRuntimeMode(status.PolicyRuntime.Mode);
        ApplyModeButtonState();
        _agentRunSummary.Text = FormatAgentRun(status);
        if (_layout.ActiveSurface == "agent_run")
        {
            _compactSummary.Text = $"{status.PolicyRuntime?.Mode ?? "unavailable"} · {status.PolicyRuntime?.Controller ?? "unavailable"}";
            _compactRecent.Text = status.PolicyRuntime == null
                ? "Policy Runtime unavailable.\nHuman control remains available in the game."
                : CompactRuntimeDetails(status);
        }
        RefreshActionFeed(status.Recording);
        ApplyRecordingAvailability(status.Recording);
    }

    private void SetPolicyControlsAvailable(bool available, string? reason = null)
    {
        bool uncertain = _policyCommands.HasUnknownCommand(_displayedPolicyRunId);
        foreach ((PlatformCommandMode mode, Button button) in _modeButtons)
        {
            button.Disabled = mode == PlatformCommandMode.Human
                ? !(available || uncertain) : !available || uncertain || _nativeRuntimeRecoveryRequired;
            button.TooltipText = available
                ? button.TooltipText
                : $"Unavailable: {reason ?? "Policy Runtime is unavailable."}";
        }
        _compactHumanButton.Disabled = !(available || uncertain);
        _endTestButton.Disabled = !(available || uncertain);
        _tickButton.Disabled = !available || uncertain || _nativeRuntimeRecoveryRequired;
        _tickButton.TooltipText = uncertain
            ? "上次操作未确认，请先暂停并接管或结束测试。"
            : available ? "Ask Policy Runtime for one bounded tick; action authority remains Connector/Runtime."
            : $"Unavailable: {reason ?? "Policy Runtime is unavailable."}";
        ApplyModeButtonState();
    }

    private void ApplyModeButtonState()
    {
        foreach ((PlatformCommandMode mode, Button button) in _modeButtons)
        {
            button.ButtonPressed = mode == _mode;
            ApplyButtonTheme(button, button.ButtonPressed);
        }
    }

    private void ApplyRecordingAvailability(
        STS2HumanAnnotator.Core.RecordingApplicationStatus recording)
    {
        if (_recorderTitle == null)
            return;
        _recorderTitle.Text = PlatformLiveActionFeed.FormatRun(recording);
        PlatformLiveActionCounts counts = _actionFeed.Counts;
        _recorderHealth.Text = PlatformLiveActionFeed.FormatCompactCounters(recording.Counters);
        _recorderCountScope.Text = PlatformLiveActionFeed.FormatCounters(recording.Counters)
            + $"\nRetained view: {counts.Diagnostics} diagnostics."
            + (counts.Exact ? "" : " Partial history; session totals above remain authoritative.");
        if (_layout.ActiveSurface == "human_recorder")
        {
            _compactSummary.Text = PlatformLiveActionFeed.FormatRun(recording);
            _compactRecent.Text = PlatformLiveActionFeed.FormatCompactRecent(_actionFeed.RecentDecisions(3));
        }
        _recorderHealth.AddThemeColorOverride("font_color", recording.Lifecycle.State switch
        {
            STS2HumanAnnotator.Core.RecordingLifecycleState.Recording => new Color("#73d39a"),
            STS2HumanAnnotator.Core.RecordingLifecycleState.Paused => new Color("#e6c36a"),
            STS2HumanAnnotator.Core.RecordingLifecycleState.Closing => new Color("#e6c36a"),
            _ => Accent
        });
        STS2HumanAnnotator.Core.RecordingLifecycleState state = recording.Lifecycle.State;
        bool inactive = state is STS2HumanAnnotator.Core.RecordingLifecycleState.Ready or STS2HumanAnnotator.Core.RecordingLifecycleState.Closed;
        bool isSource = recording.SourceV2 is not null || recording.Source is not null;
        bool sourceFields = (inactive && _recordingProfile.Selected == 1) || (!inactive && isSource);
        _recordingProfile.Disabled = !inactive;
        _recordingSourceKind.Visible = _recordingActor.Visible = _sourceDeclarationNotice.Visible = sourceFields;
        _recordingSourceKind.Disabled = !inactive && state != STS2HumanAnnotator.Core.RecordingLifecycleState.Paused;
        _recordingActor.Editable = !_recordingSourceKind.Disabled;
        var sourceDeclaration = recording.SourceV2?.Declaration ?? recording.Source?.Declaration;
        _sourceDeclarationNotice.Text = "来源仅由操作员声明，不是机器验证的真人证明。"
            + (isSource && sourceDeclaration is not null ? $" 当前：{SourceKindLabel(sourceDeclaration.SourceKind)} · {sourceDeclaration.ActorId}" : "");
        if (isSource)
        {
            long observations = recording.SourceV2?.Observations ?? recording.Source!.Observations;
            long inputs = recording.SourceV2?.Inputs ?? recording.Source!.Inputs;
            int pending = recording.SourceV2?.PendingInputs ?? recording.Source!.PendingInputs;
            long gaps = recording.SourceV2?.Gaps ?? recording.Source!.Gaps;
            bool complete = recording.SourceV2?.AccountingComplete ?? recording.Source!.AccountingComplete;
            string health = complete ? "当前记账完整" : "记账不完整";
            string summary = $"公开观察 {observations} · 输入 {inputs} · 待完成 {pending} · 缺口 {gaps}";
            _recorderTitle.Text = $"原生交互录制 · {state}";
            _recorderHealth.Text = summary + " · " + health;
            _recorderCountScope.Text = $"{health} · append={recording.Health.Append} · disk={recording.Health.Disk}\nprofile={recording.Session?.CaptureProfileId}\n不是真人起源、完整覆盖、因果后继或研究准入证明。";
            if (_layout.ActiveSurface == "human_recorder")
            {
                _compactSummary.Text = $"原生交互 · {state} · {SourceKindLabel(sourceDeclaration?.SourceKind)}";
                _compactRecent.Text = summary + "\n" + health;
            }
            _decisionInspector.Visible = false;
        }
        _actionFeedList.Visible = !isSource;
        _recordingButtons[STS2HumanAnnotator.Core.RecordingCommandKind.StartNewSession].Disabled =
            recording.Continuous?.Armed == true || !inactive
                || (_recordingProfile.Selected == 1 && !HasSourceDeclaration());
        _recordingButtons[STS2HumanAnnotator.Core.RecordingCommandKind.Pause].Disabled =
            state != STS2HumanAnnotator.Core.RecordingLifecycleState.Recording;
        _recordingButtons[STS2HumanAnnotator.Core.RecordingCommandKind.Resume].Disabled =
            state != STS2HumanAnnotator.Core.RecordingLifecycleState.Paused;
        _recordingButtons[STS2HumanAnnotator.Core.RecordingCommandKind.Close].Disabled =
            recording.Continuous?.Armed != true && state is not (STS2HumanAnnotator.Core.RecordingLifecycleState.Recording
                or STS2HumanAnnotator.Core.RecordingLifecycleState.Paused);
        _recordingButtons[STS2HumanAnnotator.Core.RecordingCommandKind.ChangeSource].Disabled =
            !isSource || state != STS2HumanAnnotator.Core.RecordingLifecycleState.Paused || !HasSourceDeclaration();
    }

    private void RefreshActionFeed(
        STS2HumanAnnotator.Core.RecordingApplicationStatus recording)
    {
        string? sessionId = recording.Session?.SessionId;
        bool feedChanged = false;
        bool sessionChanged = false;
        if (!string.Equals(_actionFeedSessionId, sessionId, StringComparison.Ordinal))
        {
            sessionChanged = true;
            _actionFeedSessionId = sessionId;
            _lastRecordingEventSequence = 0;
            _actionFeed.Reset();
            _actionFeedPage = 0;
            _selectedActionIdentity = null;
            feedChanged = true;
        }

        try
        {
            STS2HumanAnnotator.Core.RecordingEventBatch batch =
                STS2HumanAnnotator.Mod.RecordingApplicationService.Instance.QueryEvents(
                    _lastRecordingEventSequence);
            if (batch.Gap)
            {
                _actionFeed.Reset();
                _actionFeedPage = 0;
                _actionFeed.MarkSourceIncomplete();
                _lastRecordingEventSequence = Math.Max(0, batch.OldestAvailableSequence - 1);
                // A gap response has no events. Read the available retained batch
                // before advancing the cursor; this never retries a gameplay action.
                batch = STS2HumanAnnotator.Mod.RecordingApplicationService.Instance.QueryEvents(
                    _lastRecordingEventSequence);
                if (batch.Gap)
                    return;
                feedChanged = true;
            }
            foreach (STS2HumanAnnotator.Core.RecordingEvent value in batch.Events)
            {
                _lastRecordingEventSequence = Math.Max(_lastRecordingEventSequence, value.Sequence);
                if (string.Equals(value.SessionId, sessionId, StringComparison.Ordinal)
                    && value.Kind is STS2HumanAnnotator.Core.RecordingEventKind.RunStarted
                        or STS2HumanAnnotator.Core.RecordingEventKind.SessionClosed)
                    PushToast($"recording.boundary.{value.EventId}", PlatformLiveActionFeed.FormatRun(recording));
                if (sessionId == null
                    || !string.Equals(value.SessionId, sessionId, StringComparison.Ordinal)
                    || !PlatformLiveActionFeed.IsActionEvent(value.Kind))
                    continue;
                feedChanged |= _actionFeed.Apply(value);
            }
            _lastRecordingEventSequence = Math.Max(_lastRecordingEventSequence, batch.LatestSequence);
            if (feedChanged)
            {
                if (sessionChanged)
                {
                    _recorderScroll.ScrollVertical = 0;
                    _decisionInspector.Visible = false;
                }
                RenderActionFeed();
            }
        }
        catch (Exception exception)
        {
            _actionFeed.MarkSourceIncomplete();
            _lastAction.Text = "LAST ACTION\nUnavailable (canonical event projection failed).";
            PushToast("recording.feed", $"Action Feed unavailable: {exception.Message}");
        }
    }

    private void RenderActionFeed()
    {
        if (_actionFeedList == null || _lastAction == null)
            return;
        if (_layout.Compact)
            return;
        foreach (Node child in _actionFeedList.GetChildren())
        {
            _actionFeedList.RemoveChild(child);
            child.QueueFree();
        }

        IReadOnlyList<PlatformLiveActionItem> recent =
            _actionFeed.Recent(PlatformLiveActionFeed.MaxEntries, _actionFeedPage * PlatformLiveActionFeed.MaxEntries);
        _actionFeedPageLabel.Text = $"Page {_actionFeedPage + 1} · {_actionFeed.Count} retained entries";
        PlatformLiveActionItem? selected = _actionFeed.Recent(PlatformLiveActionAggregation.RetainedLimit)
            .FirstOrDefault(value => value.CorrelationIdentity == _selectedActionIdentity);
        _decisionInspector.Visible = selected != null;
        if (selected != null)
            _lastAction.Text = $"DECISION DETAIL\n{PlatformLiveActionFeed.FormatDetail(selected)}";
        if (_layout.ActiveSurface == "human_recorder")
            _compactRecent.Text = PlatformLiveActionFeed.FormatCompactRecent(_actionFeed.RecentDecisions(3));

        foreach (PlatformLiveActionItem value in recent)
        {
            var item = new PanelContainer
            {
                MouseFilter = MouseFilterEnum.Stop,
                SizeFlagsHorizontal = SizeFlags.ExpandFill,
                SizeFlagsVertical = SizeFlags.ShrinkBegin,
                CustomMinimumSize = new Vector2(0, 30)
            };
            Color border = PlatformLiveActionFeed.IsFailure(value) ? new Color("#c26b69")
                : value.Kind == STS2HumanAnnotator.Core.RecordingEventKind.DecisionRecorded ? new Color("#4fa77c")
                : new Color("#4f91a6");
            item.AddThemeStyleboxOverride("panel", MakePanelStyle(
                new Color("#16222de8"), border, 6, 1, 7));
            var label = new Label
            {
                Text = PlatformLiveActionFeed.FormatEntry(value),
                AutowrapMode = TextServer.AutowrapMode.WordSmart,
                MouseFilter = MouseFilterEnum.Ignore,
                SizeFlagsHorizontal = SizeFlags.ExpandFill,
                SizeFlagsVertical = SizeFlags.ExpandFill,
                CustomMinimumSize = new Vector2(0, 24),
                VerticalAlignment = VerticalAlignment.Center,
                ClipText = true
            };
            label.AddThemeFontSizeOverride("font_size", 12);
            label.AddThemeColorOverride("font_color", TextPrimary);
            item.AddChild(label);
            item.GuiInput += input =>
            {
                if (input is InputEventMouseButton { Pressed: true, ButtonIndex: MouseButton.Left })
                {
                    _selectedActionIdentity = value.CorrelationIdentity;
                    _lastAction.Text = $"DECISION DETAIL\n{PlatformLiveActionFeed.FormatDetail(value)}";
                    _decisionInspector.Visible = true;
                }
            };
            _actionFeedList.AddChild(item);
        }
    }

    private static string FormatAgentRun(PlatformLiveStatus status) => string.Join('\n', new[]
    {
        "POLICY RUNTIME",
        $"Connector: {status.TransportStatus}",
        $"Policy Runtime: {status.PolicyRuntimeTransportStatus}",
        $"Mode: {status.PolicyRuntime?.Mode ?? "unavailable"}",
        RuntimeIdentityLabel(status.PolicyRuntime),
        $"Lifecycle: {status.PolicyRuntime?.Lifecycle ?? "unavailable"}",
        $"Last operation: {RuntimeOperationLabel(status.PolicyRuntime)}",
        $"{(status.PolicyRuntime is NativeAgentRuntimeStatus ? "Result" : "Receipt")}: {status.Receipt.Status}",
        $"Interaction: {status.Snapshot?.Interaction.Kind ?? "none"}",
        $"Selected: {(status.Selected.Count == 0 ? "none" : string.Join(", ", status.Selected.Select(item => item.Label)))}",
        status.PolicyRuntime == null
            ? $"Unavailable: {PlatformLiveLayout.PolicyUnavailableReason(status)}"
            : $"Tainted: {status.PolicyRuntime.Tainted}"
    });

    private static string RuntimeIdentityLabel(IPlatformRuntimeStatus? status) => status switch
    {
        NativeAgentRuntimeStatus native => $"Agent: {native.Agent.AgentId} {native.Agent.AgentVersion}",
        PolicyRuntimeStatus policy => $"Policy: {policy.Policy.PolicyId} {policy.Policy.PolicyVersion}",
        _ => "Agent: unavailable"
    };

    private static string RuntimeOperationLabel(IPlatformRuntimeStatus? status) => status switch
    {
        NativeAgentRuntimeStatus native => native.LastResult?.RequestId ?? native.LastObservation?.AcquisitionId ?? "none",
        PolicyRuntimeStatus policy => ShortValue(policy.LastDecision?.DecisionId, "none"),
        _ => "none"
    };

    private static string CompactRuntimeDetails(PlatformLiveStatus status) => status.PolicyRuntime switch
    {
        NativeAgentRuntimeStatus native => $"{native.Agent.AgentId}\n{RuntimeOperationLabel(native)}\nResult: {status.Receipt.Status}",
        PolicyRuntimeStatus policy => $"{policy.Policy.PolicyId}\n{policy.LastDecision?.BoundActionLabel ?? "No decision yet"}\nReceipt: {status.Receipt.Status}",
        _ => "Runtime unavailable"
    };

    private static string ShortValue(string? value, string fallback = "unavailable")
    {
        if (string.IsNullOrWhiteSpace(value))
            return fallback;
        return value.Length <= 18 ? value : $"{value[..8]}…{value[^6..]}";
    }

    private void OnProcessFrame()
    {
        CompleteWorkbenchOpenCheck();
        _nativeWorkbench.OnFrame(_workspace.Visible && _layout.ActiveSurface == "workbench" && !_layout.Compact);
        ApplyPendingStatus();
        ApplyPendingPollError();
        ExpireToasts();
    }

    private void BeginOpenWorkbench()
    {
        if (_disposed || _workbenchOpenCheck is { IsCompleted: false })
            return;
        _externalWorkbenchButton.Disabled = true;
        CancellationToken token = _workbenchOpenLifetime.Token;
        _workbenchOpenCheck = Task.Run(() => PlatformWorkbenchOpenClient.OpenAsync(
            _workbenchHttpClient, token));
        PushToast("workbench.open", "正在确认本机工作台连接…");
    }

    private void CompleteWorkbenchOpenCheck()
    {
        Task<PlatformWorkbenchOpenResult>? check = _workbenchOpenCheck;
        if (check is null || !check.IsCompleted || _disposed)
            return;

        _workbenchOpenCheck = null;
        _externalWorkbenchButton.Disabled = false;
        PlatformWorkbenchOpenResult result;
        try
        {
            result = check.GetAwaiter().GetResult();
        }
        catch (Exception)
        {
            result = new PlatformWorkbenchOpenResult(PlatformWorkbenchOpenState.Unavailable);
        }

        if (!result.CanOpen || result.Url is null)
        {
            string message = result.State switch
            {
                PlatformWorkbenchOpenState.NotRegistered => "本机工作台尚未连接。请在电脑上打开工作台后重试。",
                PlatformWorkbenchOpenState.Stale => "本机工作台连接已失效。正常退出后会自动清除；若工作台异常退出，请重启游戏后再连接。",
                PlatformWorkbenchOpenState.UnsupportedPlatform => "当前系统不支持从游戏启动本机工作台。",
                PlatformWorkbenchOpenState.LauncherMissing => "未找到已安装的本机工作台启动入口，请先完成工作台安装。",
                PlatformWorkbenchOpenState.LaunchFailed => "本机工作台启动失败，请检查安装并从电脑上打开工作台。",
                PlatformWorkbenchOpenState.LaunchTimedOut => "本机工作台启动超时，请稍后检查工作台状态。",
                _ => "本机工作台暂不可用。请在电脑上打开工作台后重试。"
            };
            PushToast("workbench.open", message);
            return;
        }

        Error opened = OS.ShellOpen(result.Url);
        PushToast("workbench.open", opened == Error.Ok
            ? "已在默认浏览器中打开本机工作台。"
            : "系统未能打开本机工作台，请从电脑上重新打开工作台后重试。");
    }

    private void ShowPanel()
    {
        _workspace.Visible = true;
        ApplyLayout();
        RefreshVisibleStatus();
        GD.Print("[STS2 Platform Live UI] toggle; input=launcher; visible=true");
    }

    private void MinimizePanel()
    {
        PersistLayout();
        _layout = _layout with { Compact = true };
        ApplyLayout();
        RefreshVisibleStatus();
        PersistLayout();
        GD.Print("[STS2 Platform Live UI] presentation; mode=compact");
    }

    private void RestorePanel()
    {
        _layout = _layout with { Compact = false };
        ApplyLayout();
        RenderActionFeed();
        RefreshVisibleStatus();
        PersistLayout();
        GD.Print("[STS2 Platform Live UI] presentation; mode=normal");
    }

    private void HidePanel()
    {
        _workspace.Visible = false;
        ApplyPresentationVisibility();
        PersistLayout();
        GD.Print("[STS2 Platform Live UI] toggle; input=close; visible=false");
    }

    private static string ToRuntimeMode(PlatformCommandMode mode) => mode switch
    {
        PlatformCommandMode.Human => "human",
        PlatformCommandMode.Shadow => "shadow",
        PlatformCommandMode.OneStep => "one_step",
        PlatformCommandMode.Auto => "auto",
        _ => throw new ArgumentOutOfRangeException(nameof(mode))
    };

    private static PlatformCommandMode ParseRuntimeMode(string mode) => mode switch
    {
        "human" => PlatformCommandMode.Human,
        "shadow" => PlatformCommandMode.Shadow,
        "one_step" => PlatformCommandMode.OneStep,
        "auto" => PlatformCommandMode.Auto,
        _ => PlatformCommandMode.Human
    };
}
