using System.Text.Json;
using STS2PlatformLiveUi;
using STS2HumanAnnotator.Core;
using STS2PlatformLiveUiTests;
using Xunit;

public sealed class PlatformNativeRecordingPresentationTests
{
    [Theory]
    [InlineData(RecordingLifecycleState.Ready, "录制未开始")]
    [InlineData(RecordingLifecycleState.Closed, "录制未开始")]
    [InlineData(RecordingLifecycleState.Closing, "封存未完成")]
    [InlineData(RecordingLifecycleState.Recording, "录制保存异常")]
    public void ArmedNeverHidesAStartOrSealFailure(RecordingLifecycleState state, string label)
    {
        var recording = new ContinuousRecording(); recording.Arm(P1NativeRecordingContract.ProfileId, "declared_human");
        var status = PlatformRecordingCommandsTests.Status(null, state) with {
            Continuous = recording.Snapshot(), RuntimeState = "session_start_failed",
            Detail = "Recording session started.",
            Health = new("not_checked", "failed", "failed", "recording_space_insufficient: available=1; required=2", DateTimeOffset.UtcNow)
        };
        Assert.StartsWith(label, PlatformNativeRecordingPresentation.QuickRecordingLabel(status));
        Assert.Contains("recording_space_insufficient", PlatformNativeRecordingPresentation.PersistenceFailure(status));
        Assert.DoesNotContain("录制待命", PlatformNativeRecordingPresentation.QuickRecordingLabel(status));
    }

    private static string[] Rows(string lifecycle, string closeout, string? error = null)
    {
        using JsonDocument document = JsonDocument.Parse(JsonSerializer.Serialize(new
        {
            capture_profile_id = "human-p1-native-decisions-1", recording_session_id = "same-session",
            recording_lifecycle = lifecycle, closeout_status = closeout, source = (object?)null,
            health = new { append_health = "healthy", disk_health = "healthy", error }
        }));
        return PlatformNativeRecordingPresentation.Details(document.RootElement).ToArray();
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
        var rows = PlatformNativeRecordingPresentation.Details(document.RootElement);
        Assert.Contains("公开观察 7 · 输入 4 · 待完成 1 · 缺口 2", rows);
        Assert.Contains("当前来源 · 未知来源 · fixture", rows);
        Assert.Contains("记账不完整，请查看录制诊断。", rows);
        Assert.DoesNotContain(rows, row => row.Contains("本人操作") || row.Contains("已封存"));
    }

    [Theory]
    [InlineData("ready", "idle", "尚未开始")]
    [InlineData("recording", "idle", "正在录制")]
    [InlineData("paused", "idle", "已暂停")]
    [InlineData("closing", "pending", "保存中，请稍等")]
    [InlineData("closed", "failed", "保存尚未完成")]
    [InlineData("closed", "closed", "已保存")]
    public void EverydayStatusKeepsDurableSaveDistinctWithoutExposingTechnicalIds(string lifecycle, string closeout, string label)
    {
        using JsonDocument document = JsonDocument.Parse(JsonSerializer.Serialize(new {
            recording_lifecycle = lifecycle, closeout_status = closeout, recording_session_id = "technical-session",
            health = new { append_health = "healthy", disk_health = "healthy", error = (string?)null }
        }));
        var rows = PlatformNativeRecordingPresentation.Rows(document.RootElement);
        Assert.Contains("录制：" + label, rows);
        Assert.DoesNotContain(rows, row => row.Contains("technical-session") || row.Contains("append=") || row.Contains("idle"));
    }

    [Fact]
    public void P1HidesTheSourceOnlyOperatorFieldAndDoesNotInventAnOriginChoice()
    {
        Assert.False(PlatformNativeRecordingPresentation.NeedsOperatorId("human-p1-native-decisions-1"));
        Assert.True(PlatformNativeRecordingPresentation.NeedsOperatorId("native-logical-source-v3"));
        Assert.True(PlatformNativeRecordingPresentation.NeedsOperatorId("native-logical-source-v4"));
        Assert.Equal("请选择", PlatformNativeRecordingPresentation.OptionLabel("source_kind", "", "请选择"));
        Assert.Equal("结束并保存", PlatformNativeRecordingPresentation.ActionLabel("recording.close"));
    }
    [Fact]
    public void MissingWorkspaceIsOneInstructionAndDoesNotHideUnknownOrActiveOperations()
    {
        using var workspace = JsonDocument.Parse("""{"status":"not_created"}""");
        using var idle = JsonDocument.Parse("""{"status":"idle"}""");
        using var active = JsonDocument.Parse("""{"status":"idle","operation":{"status":"failed"}}""");
        using var unknown = JsonDocument.Parse("""{"status":"unexpected_status"}""");
        Assert.Single(PlatformNativeRecordingPresentation.DataRows("workspace", workspace.RootElement, true)!);
        Assert.Empty(PlatformNativeRecordingPresentation.DataRows("import", idle.RootElement, true)!);
        Assert.Null(PlatformNativeRecordingPresentation.DataRows("import", active.RootElement, true));
        Assert.Null(PlatformNativeRecordingPresentation.DataRows("import", unknown.RootElement, true));
        Assert.Null(PlatformNativeRecordingPresentation.DataRows("import", idle.RootElement, false));
        Assert.Equal("本机资料空间", PlatformNativeRecordingPresentation.OwnerLabel("workspace"));
        Assert.Equal("尚未开始", PlatformNativeRecordingPresentation.StateLabel("idle"));
        Assert.Equal("unexpected_status", PlatformNativeRecordingPresentation.StateLabel("unexpected_status"));
    }

    [Fact]
    public void MaintenanceShowsOnlyVerifiedBackupsAndObservedRecordingVolumeSpace()
    {
        using var status = JsonDocument.Parse("""
            {"status":"up_to_date","enabled":true,"remote_configured":true,
             "verified_recordings":2,"cooling_enabled":true,"operation_status":"completed",
             "space":{"free_bytes":2684354560,"total_bytes":10737418240},"space_observed_at":"2026-10-21T10:00:00Z"}
            """);
        var rows = PlatformNativeRecordingPresentation.MaintenanceRows(status.RootElement, false);
        Assert.Contains("自动整理：已启用", rows);
        Assert.Contains("远端备份：已设置，已完整核验 2 份录制。", rows);
        Assert.Contains("录制磁盘剩余：2.5 GiB（最近检查）", rows);
        Assert.DoesNotContain(rows, row => row.Contains("operation=") || row.Contains("2026-10"));
        Assert.Contains("空间检查时间 · 2026-10-21T10:00:00Z",
            PlatformNativeRecordingPresentation.MaintenanceRows(status.RootElement, true));
    }

    [Fact]
    public void MaintenanceFailureAndUnavailableSpaceNeverBecomeASuccessOrZeroSpace()
    {
        using var status = JsonDocument.Parse("""
            {"status":"failed","enabled":false,"remote_configured":false,
             "verified_recordings":0,"cooling_enabled":false,"operation_status":"failed",
             "error_code":"remote_readback_failed","space":null}
            """);
        var rows = PlatformNativeRecordingPresentation.MaintenanceRows(status.RootElement, false);
        Assert.Contains("后台处理遇到问题，请到诊断页查看。", rows);
        Assert.Contains("录制磁盘剩余：暂不可用。", rows);
        Assert.DoesNotContain(rows, row => row.Contains("已处理完") || row.Contains("0.0 GiB") || row.Contains("remote_readback_failed"));
        Assert.Contains("后台错误 · remote_readback_failed",
            PlatformNativeRecordingPresentation.MaintenanceRows(status.RootElement, true));
        using var unavailable = JsonDocument.Parse("""{"status":"unavailable","error_code":"owner_unavailable"}""");
        Assert.Contains("后台错误 · owner_unavailable",
            PlatformNativeRecordingPresentation.MaintenanceRows(unavailable.RootElement, true));
    }

}
