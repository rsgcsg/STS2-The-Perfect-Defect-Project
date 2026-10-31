using System.Text.Json;

namespace STS2PlatformLiveUi;

/// <summary>One paired application view and command owner shared by every desk window.</summary>
internal sealed class PlatformNativeWorkbenchPanel : IDisposable
{
    private readonly PlatformNativeWorkbenchClient _client = new();
    private readonly PlatformNativeWorkbenchCommands _commands = new();
    private readonly CancellationTokenSource _lifetime = new();
    private readonly List<(Task<PlatformNativeWorkbenchCommandResult> Task, string Action)> _writes = [];
    private Task<JsonElement>? _deskRead, _choicesRead;
    private PlatformNativeWorkbenchConnection? _readConnection;
    internal JsonElement? Desk { get; private set; }
    internal JsonElement[] Models { get; private set; } = [];
    internal JsonElement[] Choices { get; private set; } = [];
    internal bool Connected { get; private set; }
    internal bool ChoicesAvailable { get; private set; }
    internal bool ChoicesReadEnabled { get; private set; }
    internal string? ChoicesError { get; private set; }
    private bool _connectionNotice = true;
    internal string Notice { get; private set; } = "正在连接本机后台…";
    internal long Revision { get; private set; }
    internal Action? Reconnect { get; set; }

    internal bool CanSubmit(string action)
    {
        PlatformNativeWorkbenchConnection? connection = PlatformNativeWorkbenchConnection.Current;
        return connection is not null && _commands.CanSubmit(connection.Binding.ConfigurationId, action);
    }

    internal void Refresh(bool choicesVisible)
    {
        if (_lifetime.IsCancellationRequested) return;
        PlatformNativeWorkbenchConnection? connection = PlatformNativeWorkbenchConnection.Current;
        if (connection is null || connection.Binding.ExpiresAt <= DateTimeOffset.UtcNow.ToUnixTimeSeconds())
        {
            Unavailable("后台未连接，录制保存状态未确认。请点“重新连接”。");
            return;
        }
        _readConnection = connection;
        _deskRead ??= Read(connection, "desk");
        if (choicesVisible && ChoicesReadEnabled) _choicesRead ??= Read(connection, "choices");
    }

    private Task<JsonElement> Read(PlatformNativeWorkbenchConnection connection, string page) =>
        Task.Run(() => _client.ViewAsync(connection, page, 0, null, _lifetime.Token));

    internal void Submit(string action, object payload)
    {
        if (_lifetime.IsCancellationRequested) return;
        if (action == "desk.reconnect") { Reconnect?.Invoke(); return; }
        PlatformNativeWorkbenchConnection? connection = PlatformNativeWorkbenchConnection.Current;
        if (connection is null)
        {
            Notice = "后台未连接，未提交操作。请点“重新连接”。"; Revision++; return;
        }
        if (!CanSubmit(action))
        {
            _connectionNotice = false;
            Notice = "上一操作仍在处理或结果未确认；先点“停，我来”或“停录制并保存”，不要重发。";
            Revision++; return;
        }
        if (action == "desk.choices_open") { ChoicesReadEnabled = false; ChoicesAvailable = false; }
        _connectionNotice = false;
        Notice = action == "desk.model_stop" ? "正在停模型，请等待实际回执…"
            : action == "desk.record_stop" ? "正在停录制并保存，请等待封存回执…" : "正在处理…";
        Revision++;
        _writes.Add((_commands.RunAsync(_client, connection, action, payload, _lifetime.Token), action));
    }

    internal void OnFrame()
    {
        CompleteRead(ref _deskRead, false);
        CompleteRead(ref _choicesRead, true);
        for (int i = _writes.Count - 1; i >= 0; i--)
        {
            var write = _writes[i];
            if (!write.Task.IsCompleted) continue;
            _writes.RemoveAt(i);
            try
            {
                var result = write.Task.GetAwaiter().GetResult();
                if (write.Action == "desk.choices_open" && result.Status == "accepted") ChoicesReadEnabled = true;
                Notice = result.Status switch {
                    "accepted" => ResultMessage(result.OwnerResponse) ?? "操作已接收，以当前状态和保存回执为准。",
                    "rejected" => "未执行：" + (result.ErrorCode ?? "请求已过期，请刷新后重新选择。"),
                    _ => "结果未确认；不会重发。请先点“停，我来”处理。"
                        + (string.IsNullOrWhiteSpace(result.ErrorCode) ? "" : " 错误码：" + result.ErrorCode)
                };
            }
            catch (Exception error) when (error is not OutOfMemoryException)
            { Notice = "结果未确认；不会重发。请先点“停，我来”处理。"; }
            Revision++;
            Refresh(false);
        }
    }

    private void CompleteRead(ref Task<JsonElement>? read, bool choices)
    {
        if (read is not { IsCompleted: true }) return;
        Task<JsonElement> completed = read; read = null;
        try
        {
            JsonElement view = completed.GetAwaiter().GetResult();
            if (_readConnection is null || !PlatformNativeWorkbenchConnection.IsCurrent(_readConnection)
                || PlatformNativeWorkbenchPair.Read(view.GetProperty("binding")) != _readConnection.Binding) return;
            JsonElement? desk = view.GetProperty("cards").EnumerateArray()
                .Where(card => Text(card, "owner") == "game_desk")
                .Select(card => (JsonElement?)card.GetProperty("data").Clone()).FirstOrDefault();
            if (desk is null) throw new InvalidOperationException("game_desk_view_unavailable");
            if (!choices) { Desk = desk; Models = Items(view); }
            else { Choices = Items(view); Desk = desk; ChoicesAvailable = true; ChoicesError = null; }
            Connected = true;
            if (_connectionNotice) { Notice = ""; _connectionNotice = false; }
            Revision++;
        }
        catch (Exception error) when (error is not OutOfMemoryException)
        {
            if (choices) { ChoicesAvailable = false; ChoicesError = "动作目录暂不可用，未提交动作。可继续在游戏内手动操作。"; Revision++; }
            else Unavailable("后台未连接，没在确认保存。游戏仍可手动操作；请点“重新连接”。");
        }
    }

    private void Unavailable(string notice)
    {
        if (Connected || Notice != notice) { Connected = false; _connectionNotice = true; Notice = notice; Revision++; }
    }
    private static JsonElement[] Items(JsonElement view) => view.GetProperty("items").EnumerateArray().Select(x => x.Clone()).ToArray();
    private static string? ResultMessage(JsonElement? owner) => owner is JsonElement value
        ? Text(value, "message") : null;
    internal static JsonElement? Object(JsonElement value, string key) => value.ValueKind == JsonValueKind.Object
        && value.TryGetProperty(key, out JsonElement item) && item.ValueKind == JsonValueKind.Object ? item : null;
    internal static string? Text(JsonElement value, string key) => value.ValueKind == JsonValueKind.Object
        && value.TryGetProperty(key, out JsonElement item) && item.ValueKind == JsonValueKind.String ? item.GetString() : null;
    internal static string Value(JsonElement value, string key, string missing = "—") => value.ValueKind == JsonValueKind.Object
        && value.TryGetProperty(key, out JsonElement item) && item.ValueKind is not (JsonValueKind.Null or JsonValueKind.Undefined)
        ? item.ValueKind == JsonValueKind.String ? item.GetString() ?? missing : item.GetRawText() : missing;
    internal static bool Flag(JsonElement value, string key) => value.ValueKind == JsonValueKind.Object
        && value.TryGetProperty(key, out JsonElement item) && item.ValueKind == JsonValueKind.True;

    public void Dispose()
    {
        if (_lifetime.IsCancellationRequested) return;
        _lifetime.Cancel(); _client.Dispose();
        // Closing a window never disposes this owner. Game unload only cancels presentation waits.
    }
}
