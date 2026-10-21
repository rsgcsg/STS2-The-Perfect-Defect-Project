using System.Text.Json;

namespace STS2PlatformLiveUi;

/// <summary>Read-only labels from the recording owner's current status.</summary>
public static class PlatformNativeRecordingPresentation
{
    public static IReadOnlyList<string> Rows(JsonElement status)
    {
        string? profile = Text(status, "capture_profile_id");
        var rows = new List<string>
        {
            (profile == "human-p1-native-decisions-1" ? "P1 原生决策录制" : "原生交互与观察")
                + " · " + Lifecycle(Text(status, "recording_lifecycle")),
            "当前会话 · " + (Text(status, "recording_session_id") ?? "尚未开始"),
            "封存状态 · " + (Text(status, "closeout_status") ?? "未提供"),
            "来源由操作员明确声明；不是机器验证的真人证明。"
        };
        if (Object(status, "health") is JsonElement health)
        {
            rows.Add("保存状态 · append=" + Text(health, "append_health") + " · disk=" + Text(health, "disk_health"));
            if (Text(health, "error") is string error) rows.Add("保存错误 · " + error);
        }
        if (Object(status, "source") is JsonElement source)
        {
            rows.Add($"公开观察 {Value(source, "observations")} · 输入 {Value(source, "inputs")} · 待完成 {Value(source, "pending_inputs")} · 缺口 {Value(source, "gaps")}");
            rows.Add(source.GetProperty("accounting_complete").GetBoolean()
                ? "当前无记账失败；不代表零缺口或覆盖资格。" : "记账不完整，请查看录制诊断。");
            if (Object(source, "declaration") is JsonElement declaration)
                rows.Add("当前来源 · " + Origin(Text(declaration, "source_kind")) + " · " + Text(declaration, "actor_id"));
        }
        if (Text(status, "recording_lifecycle") == "closed" && Text(status, "closeout_status") == "closed")
            rows.Add("本会话已封存；可明确选择来源并开始下一会话。");
        return rows;
    }

    private static string Lifecycle(string? state) => state switch
    {
        "ready" => "就绪", "recording" => "正在录制", "paused" => "已暂停采集（游戏继续）",
        "closing" => "正在封存，请等待", "closed" => "已结束", _ => state ?? "未提供"
    };
    private static string Origin(string? origin) => origin switch
    {
        "declared_human" => "本人操作", "agent_native_ui" => "AI 界面操作",
        "agent_protocol" => "Agent 协议", _ => "未知来源"
    };
    private static string? Text(JsonElement value, string key) => value.TryGetProperty(key, out JsonElement item)
        && item.ValueKind == JsonValueKind.String ? item.GetString() : null;
    private static JsonElement? Object(JsonElement value, string key) => value.TryGetProperty(key, out JsonElement item)
        && item.ValueKind == JsonValueKind.Object ? item : null;
    private static string Value(JsonElement value, string key) => value.GetProperty(key).GetRawText();
}
