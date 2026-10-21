using System.Text.Json;
using STS2PlatformLiveUi;
using Xunit;

public sealed class PlatformNativeRecordingPresentationTests
{
    private static string[] Rows(string lifecycle, string closeout, string? error = null)
    {
        using JsonDocument document = JsonDocument.Parse(JsonSerializer.Serialize(new
        {
            capture_profile_id = "human-p1-native-decisions-1", recording_session_id = "same-session",
            recording_lifecycle = lifecycle, closeout_status = closeout, source = (object?)null,
            health = new { append_health = "healthy", disk_health = "healthy", error }
        }));
        return PlatformNativeRecordingPresentation.Rows(document.RootElement).ToArray();
    }

    [Theory]
    [InlineData("recording", "正在录制")]
    [InlineData("paused", "已暂停采集（游戏继续）")]
    [InlineData("closing", "正在封存，请等待")]
    public void P1ShowsTheOwnerSessionAndLifecycleWithoutLegacyDecisionCounters(string lifecycle, string label)
    {
        string[] rows = Rows(lifecycle, "pending");
        Assert.Contains("P1 原生决策录制 · " + label, rows);
        Assert.Contains("当前会话 · same-session", rows);
        Assert.Contains("封存状态 · pending", rows);
        Assert.DoesNotContain(rows, row => row.Contains("已封存") || row.Contains("本人操作") || row.Contains("真实失败"));
    }

    [Fact]
    public void ClosedIsOnlyPresentedAsSealedWhenTheOwnerCloseoutAlsoCompletes()
    {
        Assert.DoesNotContain(Rows("closed", "failed", "disk_write_failed"), row => row.Contains("已封存"));
        Assert.Contains("保存错误 · disk_write_failed", Rows("closed", "failed", "disk_write_failed"));
        Assert.Contains(Rows("closed", "closed"), row => row.Contains("已封存") && row.Contains("下一会话"));
    }

    [Fact]
    public void SourceCountersAndExplicitUnknownDeclarationRetainTheirOriginalMeaning()
    {
        using JsonDocument document = JsonDocument.Parse("""
            {"capture_profile_id":"native-logical-source-v4","recording_session_id":"source-session",
             "recording_lifecycle":"recording","closeout_status":"idle",
             "source":{"observations":7,"inputs":4,"pending_inputs":1,"gaps":2,"accounting_complete":false,
                       "declaration":{"source_kind":"unknown","actor_id":"fixture"}}}
            """);
        var rows = PlatformNativeRecordingPresentation.Rows(document.RootElement);
        Assert.Contains("公开观察 7 · 输入 4 · 待完成 1 · 缺口 2", rows);
        Assert.Contains("当前来源 · 未知来源 · fixture", rows);
        Assert.Contains("记账不完整，请查看录制诊断。", rows);
        Assert.DoesNotContain(rows, row => row.Contains("本人操作") || row.Contains("已封存"));
    }
}
