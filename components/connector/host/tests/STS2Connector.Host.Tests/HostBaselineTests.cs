using STS2Connector.HostControl;
using Xunit;

namespace STS2Connector.Tests;

public sealed class HostBaselineTests
{
    private static HostBaselineFacts Facts() => new(true, true, true, true, false, false, false,
        "runtime-1", "generation-1", 1, "/isolated/profile1", "game-mod-config");
    private static HostBaselineSnapshot Snapshot() => new("before", "after", "{}",
        new(Array.Empty<string>(), Array.Empty<string>(), 1),
        new(Array.Empty<string>(), Array.Empty<string>(), Array.Empty<string>(), Array.Empty<string>(), Array.Empty<string>()), true, true);

    [Fact]
    public void PreparationIsExplicitAndOnlyBeforeNormalSingleplayerRun()
    {
        var session = new HostProfileControl.BaselineSession(HostProfileControl.NormalBaselineRecipe);
        int writes = 0;
        foreach (var facts in new[] { Facts() with { RunActive = true }, Facts() with { MainMenuCurrent = false },
            Facts() with { TestMode = true }, Facts() with { Multiplayer = true }, Facts() with { IsolationVerified = false } })
            Assert.False(session.Prepare(facts, () => { writes++; return Snapshot(); }).Ready);
        Assert.Equal(0, writes);
        Assert.True(session.Prepare(Facts(), () => { writes++; return Snapshot(); }).Ready);
        Assert.True(session.Prepare(Facts(), () => { writes++; return Snapshot(); }).Ready);
        Assert.Equal(1, writes);
        Assert.False(new HostProfileControl.BaselineSession(null).Prepare(Facts(), Snapshot).Ready);
        Assert.Throws<InvalidOperationException>(() => new HostProfileControl.BaselineSession("console-command"));
    }

    [Fact]
    public void UncertainNativeWriteIsNotRetried()
    {
        var session = new HostProfileControl.BaselineSession(HostProfileControl.NormalBaselineRecipe);
        int writes = 0;
        Assert.Equal("baseline_application_or_readback_failed", session.Prepare(Facts(), () => {
            writes++; throw new InvalidOperationException("save failed"); }).Status);
        Assert.Equal("baseline_preparation_already_attempted", session.Prepare(Facts(), () => { writes++; return Snapshot(); }).Status);
        Assert.Equal(1, writes);
    }

    [Fact]
    public void ReadinessSurvivesNormalRunEntryButNeverRuntimeOrProfileReplacement()
    {
        var session = new HostProfileControl.BaselineSession(HostProfileControl.NormalBaselineRecipe);
        session.Prepare(Facts(), Snapshot);
        Assert.True(session.Observe(Facts() with { MainMenuCurrent = false, RunActive = true }).Ready);
        foreach (var facts in new[] { Facts() with { RuntimeInstanceId = "other" }, Facts() with { GenerationId = "other" },
            Facts() with { NativeProfileId = 2 }, Facts() with { IdentityKey = "other" } })
            Assert.Equal("baseline_binding_changed", session.Observe(facts).Status);
    }
}
