using System.Text.Json;
using System.Globalization;
using STS2HumanAnnotator.Core;

namespace STS2PlatformLiveUi;

/// <summary>Read-only labels from the recording owner's current status.</summary>
public static class PlatformNativeRecordingPresentation
{
    public static string QuickRecordingLabel(RecordingApplicationStatus status)
    {
        bool failed = status.Health.Append == "failed" || status.Health.Disk == "failed";
        if (failed) return status.Lifecycle.State switch {
            RecordingLifecycleState.Ready or RecordingLifecycleState.Closed => "录制未开始 · 查看原因",
            RecordingLifecycleState.Closing => "封存未完成 · 查看原因",
            _ => "录制保存异常 · 查看原因"
        };
        bool p1 = status.Session?.CaptureProfileId == P1NativeRecordingContract.ProfileId;
        return status.Lifecycle.State switch {
            RecordingLifecycleState.Recording => $"正在录制 · {status.Counters.Records} 次操作",
            RecordingLifecycleState.Paused => p1 ? "继续录制" : "查看录制",
            RecordingLifecycleState.Closing => "正在封存 · 查看",
            _ => status.Continuous?.Armed == true ? "录制待命 · 查看" : "开始本人录制"
        };
    }

    public static string? PersistenceFailure(RecordingApplicationStatus status) =>
        status.Health.Append == "failed" || status.Health.Disk == "failed"
            ? QuickRecordingLabel(status) + "：" + status.Detail : null;

    public static IReadOnlyList<string> MaintenanceRows(JsonElement status, bool details)
    {
        string? state = Text(status, "status");
        if (state == "unavailable")
            return details && Text(status, "error_code") is string unavailable
                ? ["后台保存和空间状态暂不可用，请到诊断页查看。", "后台错误 · " + unavailable]
                : ["后台保存和空间状态暂不可用，请到诊断页查看。"];
        bool enabled = Flag(status, "enabled"), remote = Flag(status, "remote_configured");
        var rows = new List<string> { "自动整理：" + (enabled ? "已启用" : "未启用") };
        string activity = state switch {
            "running" or "operation_in_progress" => "正在保存、派生或备份，请稍等。",
            "cooling" => "正在周转已验证的本地副本。",
            "up_to_date" or "completed" => "已处理完当前录制。",
            "failed" or "blocked" => "后台处理遇到问题，请到诊断页查看。",
            "excluded_by_scope" => "当前录制不在已设置的处理范围内。",
            "disabled" => "后台自动处理尚未启用。",
            _ => "等待后台检查。"
        };
        rows.Add(activity);
        rows.Add(remote ? "远端备份：已设置，已完整核验 " + Value(status, "verified_recordings") + " 份录制。"
            : "远端备份：尚未设置。");
        rows.Add("本地空间周转：" + (Flag(status, "cooling_enabled") ? "已启用" : "未启用"));
        if (Object(status, "space") is JsonElement space
            && space.TryGetProperty("free_bytes", out JsonElement free) && free.TryGetInt64(out long bytes))
        {
            rows.Add("录制磁盘剩余：" + (bytes / 1073741824d).ToString("0.0", CultureInfo.InvariantCulture) + " GiB（最近检查）");
            if (details && Text(status, "space_observed_at") is string observed) rows.Add("空间检查时间 · " + observed);
        }
        else rows.Add("录制磁盘剩余：暂不可用。");
        if (details)
        {
            rows.Add("后台状态 · " + state + " · operation=" + Text(status, "operation_status"));
            if (Text(status, "error_code") is string error) rows.Add("后台错误 · " + error);
        }
        return rows;
    }

    private static bool Flag(JsonElement value, string key) => value.TryGetProperty(key, out JsonElement item)
        && item.ValueKind == JsonValueKind.True;

    public static IReadOnlyList<string> Rows(JsonElement status)
    {
        string? lifecycle = Text(status, "recording_lifecycle"), closeout = Text(status, "closeout_status");
        string label = lifecycle switch {
            "ready" => "尚未开始", "recording" => "正在录制", "paused" => "已暂停",
            "closing" => "保存中，请稍等", "closed" when closeout == "closed" => "已保存",
            "closed" => "保存尚未完成", _ => "暂时无法读取状态"
        };
        var rows = new List<string> { "录制：" + label };
        if (lifecycle == "ready") rows.Add("选择谁在操作，然后开始录制。");
        if (lifecycle == "paused") rows.Add("暂停期间不采集，游戏可以继续。");
        if (lifecycle == "closed" && closeout == "closed") rows.Add("本次录制已保存，可以开始下一次。");
        if (Object(status, "health") is JsonElement health
            && (Text(health, "error") is not null
                || Text(health, "disk_health") is not ("healthy" or "not_open")))
            rows.Add("保存遇到问题，请打开诊断页查看原因。");
        if (Object(status, "source") is JsonElement source)
        {
            rows.Add($"已记录 {Value(source, "observations")} 次观察、{Value(source, "inputs")} 次操作。");
            if (!source.GetProperty("accounting_complete").GetBoolean()) rows.Add("录制存在缺口，请到诊断页查看。");
            if (Object(source, "declaration") is JsonElement declaration)
                rows.Add("操作者：" + Origin(Text(declaration, "source_kind")));
        }
        return rows;
    }

    public static string OwnerLabel(string owner) => owner switch {
        "workspace" => "本机资料空间", "curation" => "资料准备",
        "import" => "录制导入", "datasets" => "数据集", _ => owner
    };

    public static string StateLabel(string value) => value switch {
        "not_created" => "尚未建立", "not_applicable" => "暂不需要",
        "idle" => "尚未开始", "workspace_required" => "请先建立本机资料空间",
        "ready" => "就绪", "running" => "正在进行", "completed" => "已完成",
        "failed" => "失败", "pending" => "等待处理", "interrupted_unknown" => "结果未知",
        _ => value
    };

    public static IReadOnlyList<string>? DataRows(string owner, JsonElement data, bool workspaceMissing)
    {
        if (!workspaceMissing || Object(data, "operation") is not null || Text(data, "error_code") is not null)
            return null;
        string? status = Text(data, "status");
        if (owner == "workspace" && status == "not_created")
            return ["尚未建立本机资料空间，先建立后可导入录制、管理数据集。"];
        if ((owner == "curation" && status == "not_applicable")
            || (owner == "import" && status == "idle")
            || (owner == "datasets" && status == "idle"
                && Text(data, "availability") == "workspace_required"
                && Text(data, "reason") == "workspace_required"))
            return [];
        return null;
    }

    public static bool NeedsOperatorId(string? profile) => profile != "human-p1-native-decisions-1";

    public static string ActionLabel(string action) => action switch {
        "recording.start" => "开始录制", "recording.pause" => "暂停", "recording.resume" => "继续录制",
        "recording.close" => "结束并保存", "recording.change_source" => "更换操作者", _ => action
    };

    public static string OptionLabel(string field, string? value, string original) => (field, value) switch {
        ("source_kind", "declared_human") => "我本人操作（本人声明）",
        ("source_kind", "agent_native_ui") => "AI 操作游戏界面",
        ("source_kind", "agent_protocol") => "程序控制（高级）",
        ("source_kind", "unknown") => "不确定操作来源",
        ("capture_profile_id", "human-p1-native-decisions-1") => "日常录制（P1，默认）",
        ("capture_profile_id", "native-logical-source-v3") => "完整观察（兼容模式）",
        ("capture_profile_id", "native-logical-source-v4") => "完整观察与公共基底（高级）",
        _ => original
    };

    public static IReadOnlyList<string> Details(JsonElement status)
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
