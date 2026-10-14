using STS2Connector.HostControl;

namespace STS2Connector.Host.Tests;

public sealed class HostLifecycleControlTests
{
    private const string Token =
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

    [Fact]
    public void ProcessControlIsDisabledWithoutAnExplicitToken()
    {
        Assert.Null(HostLifecycleControl.ResolveConfiguredToken(null));
        Assert.Null(HostLifecycleControl.ResolveConfiguredToken("  "));
        Assert.Throws<InvalidOperationException>(
            () => HostLifecycleControl.ResolveConfiguredToken("short"));
        Assert.Throws<InvalidOperationException>(
            () => HostLifecycleControl.ResolveConfiguredToken(Token.ToUpperInvariant()));
    }

    [Fact]
    public void ShutdownRequiresBothSecretAndCurrentRuntimeIdentity()
    {
        Assert.Equal(
            "host_control_disabled",
            HostLifecycleControl.Authorize(null, Token, "runtime-a", "runtime-a").Status);
        Assert.Equal(
            "host_control_unauthorized",
            HostLifecycleControl.Authorize(Token, new string('0', 64), "runtime-a", "runtime-a").Status);
        Assert.Equal(
            "runtime_instance_changed",
            HostLifecycleControl.Authorize(Token, Token, "runtime-old", "runtime-a").Status);

        HostShutdownAuthorization allowed =
            HostLifecycleControl.Authorize(Token, Token, "runtime-a", "runtime-a");
        Assert.True(allowed.Allowed);
        Assert.Equal("shutdown_authorized", allowed.Status);
    }

    [Fact]
    public void RunSeedCanonicalizesTheNativeSeedAlphabet()
    {
        Assert.Equal("01ABC123", HostRunSeedControl.ResolveConfiguredSeed(" oiabc123 "));
        Assert.Null(HostRunSeedControl.ResolveConfiguredSeed(null));
        Assert.Throws<InvalidOperationException>(() => HostRunSeedControl.ResolveConfiguredSeed("bad-seed"));
    }

    [Fact]
    public void RunSeedRequiresHeadlessAndRefusesAConflictingOverride()
    {
        HostSeedApplication ready = HostRunSeedControl.EvaluateApplication("ABC", "headless", null, false);
        Assert.True(ready.Allowed);
        Assert.True(ready.ShouldApply);

        Assert.False(
            HostRunSeedControl.EvaluateApplication("ABC", "windows", null, false).Allowed);
        Assert.False(
            HostRunSeedControl.EvaluateApplication("ABC", "headless", "OTHER", false).Allowed);
    }

    [Fact]
    public void IsolatedNativeUiSeedPermissionIsIndependentOfSpeedAndDoesNotAdmitUnmanagedUi()
    {
        HostSeedApplication allowed = HostRunSeedControl.EvaluateApplication("ABC", "macos", null, false,
            isolatedNativeUi: true);
        Assert.True(allowed.Allowed);
        Assert.True(allowed.ShouldApply);
        Assert.False(HostRunSeedControl.EvaluateApplication("ABC", "macos", null, false).Allowed);
        Assert.False(HostRunSeedControl.EvaluateApplication("ABC", "macos", "OTHER", false,
            isolatedNativeUi: true).Allowed);
        Assert.False(HostRunSeedControl.EvaluateApplication("ABC", "macos", "ABC", true,
            isolatedNativeUi: true).ShouldApply);
    }
}
