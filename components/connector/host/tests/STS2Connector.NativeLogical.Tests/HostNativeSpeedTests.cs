using STS2Connector.HostControl;
using Xunit;

namespace STS2Connector.NativeLogical.Tests;

public sealed class HostNativeSpeedTests
{
    private static HostNativeSpeedFacts Ready(string effective = "normal") => new(
        true, true, true, true, false, false, "runtime-1", "generation-1", 1, "/isolated/profile1", effective, "exact-identity");

    [Theory]
    [InlineData("normal")]
    [InlineData("fast")]
    [InlineData("instant")]
    public void RequestedModeReplacesNativePostBootPreferenceOnce(string mode)
    {
        var session = new HostNativeSpeedSession(mode);
        string actual = "normal"; // Native default / boot-clamped preference is not the request.
        int writes = 0;
        HostNativeSpeedStatus result = session.Prepare(Ready(actual), () => { actual = mode; writes++; }, () => actual);
        Assert.True(result.Ready);
        Assert.Equal("normal", result.Before);
        Assert.Equal(mode, result.Effective);
        Assert.Equal(1, writes);
        Assert.True(session.Prepare(Ready(actual), () => writes++, () => actual).Ready);
        Assert.Equal(1, writes);
        Assert.True(session.Observe(Ready(actual) with { MainMenuCurrent = false, RunActive = true }).Ready);
        Assert.Equal(1, writes); // Post-bootstrap provenance needs no main-menu owner and never writes.
    }

    [Fact]
    public void UnmanagedModeDoesNotReadOrWritePreferences()
    {
        var session = new HostNativeSpeedSession(null);
        Assert.Equal("native_speed_not_configured", session.Prepare(Ready(),
            () => throw new Exception("must not write"), () => throw new Exception("must not read")).Status);
    }

    [Theory]
    [InlineData("normal")]
    [InlineData("fast")]
    [InlineData("instant")]
    public void NativeModeChangesKeepThePreparedRunAndNeverReapplyTheLaunchPreference(string requested)
    {
        var session = new HostNativeSpeedSession(requested);
        int writes = 0;
        Assert.True(session.Prepare(Ready(), () => writes++, () => requested).Ready);
        foreach (string actual in new[] { "normal", "fast", "instant" })
        {
            var observed = session.Observe(Ready(actual) with { MainMenuCurrent = false, RunActive = true });
            Assert.True(observed.Ready);
            Assert.Equal(requested, observed.Requested);
            Assert.Equal("normal", observed.Before);
            Assert.Equal(actual, observed.Effective);
        }
        Assert.Equal(1, writes);
    }

    [Theory]
    [InlineData(0, "native_speed_identity_unadmitted")]
    [InlineData(1, "native_speed_isolation_unverified")]
    [InlineData(2, "native_speed_prefs_not_loaded")]
    [InlineData(3, "native_speed_main_menu_required")]
    [InlineData(4, "native_speed_active_run")]
    [InlineData(5, "native_speed_multiplayer_refused")]
    [InlineData(6, "native_speed_unknown_effective_mode")]
    public void InvalidNativeFactsCannotWrite(int fault, string expected)
    {
        HostNativeSpeedFacts facts = fault switch
        {
            0 => Ready() with { IdentityAdmitted = false },
            1 => Ready() with { IsolationVerified = false },
            2 => Ready() with { PrefsLoaded = false },
            3 => Ready() with { MainMenuCurrent = false },
            4 => Ready() with { RunActive = true },
            5 => Ready() with { Multiplayer = true },
            _ => Ready() with { Effective = null }
        };
        var session = new HostNativeSpeedSession("instant");
        Assert.Equal(expected, session.Prepare(facts,
            () => throw new Exception("must not write"), () => throw new Exception("must not read")).Status);
        Assert.False(session.Observe(facts).Ready);
    }

    [Fact]
    public void ClampOrUncertainSetterNeverTriggersASecondApplication()
    {
        var session = new HostNativeSpeedSession("instant");
        int writes = 0;
        Assert.Equal("native_speed_mismatch", session.Prepare(Ready("fast"), () => writes++, () => "fast").Status);
        Assert.Equal("native_speed_preparation_already_attempted", session.Prepare(Ready("fast"), () => writes++, () => "instant").Status);
        Assert.Equal(1, writes);
        var uncertain = new HostNativeSpeedSession("instant");
        Assert.Equal("native_speed_application_failed", uncertain.Prepare(Ready(), () => throw new Exception(), () => "instant").Status);
        Assert.Equal("native_speed_preparation_already_attempted", uncertain.Prepare(Ready(), () => writes++, () => "instant").Status);
        Assert.Equal(1, writes);
        var readFailure = new HostNativeSpeedSession("instant");
        string effective = "normal";
        HostNativeSpeedStatus unknown = readFailure.Prepare(Ready(), () => { effective = "instant"; writes++; },
            () => throw new Exception("readback unavailable after write"));
        Assert.Equal("instant", effective);
        Assert.Null(unknown.Effective);
        Assert.Equal("normal", unknown.Before);
        Assert.False(unknown.Ready);
        Assert.Equal("native_speed_preparation_already_attempted", readFailure.Prepare(Ready(effective),
            () => writes++, () => effective).Status);
        Assert.Equal(2, writes);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(1)]
    [InlineData(2)]
    [InlineData(3)]
    [InlineData(4)]
    [InlineData(5)]
    public void PreparedBindingAndUnknownSpeedRefuseObservationAndReapplication(int fault)
    {
        var session = new HostNativeSpeedSession("instant");
        int writes = 0;
        Assert.True(session.Prepare(Ready(), () => writes++, () => "instant").Ready);
        HostNativeSpeedFacts drift = fault switch
        {
            0 => Ready("unknown"),
            1 => Ready("instant") with { RuntimeInstanceId = "other" },
            2 => Ready("instant") with { ProfileGenerationId = "other" },
            3 => Ready("instant") with { NativeProfileId = 2 },
            4 => Ready("instant") with { NativeProfilePath = "/other" },
            _ => Ready("instant") with { IdentityKey = "other" }
        };
        Assert.False(session.Observe(drift).Ready);
        Assert.False(session.Prepare(drift, () => writes++, () => "instant").Ready);
        Assert.Equal(1, writes);
    }

    [Theory]
    [InlineData("None")]
    [InlineData("Normal")]
    [InlineData("")]
    [InlineData("turbo")]
    public void ModeNoneAndUnknownRequestsAreRejected(string mode) =>
        Assert.Throws<InvalidOperationException>(() => new HostNativeSpeedSession(mode));
}
