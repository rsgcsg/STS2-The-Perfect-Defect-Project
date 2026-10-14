using System;
using System.Text.Json.Serialization;
using Godot;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Multiplayer.Game;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Saves;
using MegaCrit.Sts2.Core.Settings;
using STS2Connector.Authority;
using STS2Connector.LiveHost;

namespace STS2Connector.HostControl;

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
internal sealed record HostPreparationRequest(string? ExpectedRuntimeInstanceId, string? HostControlToken);

internal sealed record HostNativeSpeedProvenance(
    string Status, bool Ready, string? Requested, string? Before, string? Effective,
    string RuntimeInstanceId, string? ProfileGenerationId,
    string? ProfileRoot, string? UserDataRoot, int? NativeProfileId, string? NativeProfilePath,
    string IdentityKey);

internal static class HostNativeSpeedControl
{
    private static HostNativeSpeedSession _session = new(null);
    private static bool _configured;

    internal static void Configure(string? mode)
    {
        string? requested = HostNativeSpeedSession.ResolveMode(mode);
        if (_configured)
        {
            if (requested != _session.Requested)
                throw new InvalidOperationException("Conflicting native speed process configuration.");
            return;
        }
        if (requested != null && (!HostLifecycleControl.Enabled || !HostProfileControl.Enabled))
            throw new InvalidOperationException("Native speed requires process-local Host control and an isolated profile binding.");
        _session = new(requested);
        _configured = true;
    }

    // Main-thread only; provenance never calls this method.
    internal static HostNativeSpeedProvenance Prepare(string runtimeInstanceId)
    {
        HostNativeSpeedFacts facts = Capture(runtimeInstanceId);
        HostNativeSpeedStatus result = _session.Prepare(facts,
            () => ApplyTypedMode(SaveManager.Instance.PrefsSave, _session.Requested!),
            () => ReadTypedMode(SaveManager.Instance.PrefsSave.FastMode));
        return Provenance(result, facts);
    }

    internal static void ApplyTypedMode(PrefsSave prefs, string mode) =>
        prefs.FastMode = mode switch
        {
            "normal" => FastModeType.Normal,
            "fast" => FastModeType.Fast,
            "instant" => FastModeType.Instant,
            _ => throw new InvalidOperationException("Unknown configured native speed.")
        };

    internal static string? ReadTypedMode(FastModeType mode) => mode switch
    {
        FastModeType.Normal => "normal", FastModeType.Fast => "fast", FastModeType.Instant => "instant", _ => null
    };

    internal static HostNativeSpeedProvenance Observe(string runtimeInstanceId)
    {
        HostNativeSpeedFacts facts = Capture(runtimeInstanceId);
        return Provenance(_session.Observe(facts), facts);
    }

    private static HostNativeSpeedProvenance Provenance(HostNativeSpeedStatus status, HostNativeSpeedFacts facts) =>
        new(status.Status, status.Ready, status.Requested, status.Before, status.Effective,
            status.RuntimeInstanceId, status.ProfileGenerationId, HostProfileControl.ProfileRoot,
            _session.Requested == null ? null : OS.GetUserDataDir(),
            status.NativeProfileId, status.NativeProfilePath, facts.IdentityKey);

    private static HostNativeSpeedFacts Capture(string runtimeInstanceId)
    {
        if (_session.Requested == null)
            return new(false, false, false, false, false, false, runtimeInstanceId, null, null, null, null, "");
        var game = EnvironmentIdentityRuntime.ReadGame();
        var host = EnvironmentIdentityRuntime.HostIdentity();
        string identityKey = string.Join("|", game.Version, game.Commit, game.MainAssemblySha256,
            game.MainAssemblyMvid, host.SourceRevision, host.ArtifactSha256, host.ModuleVersionId,
            game.Modset?.Fingerprint);
        SaveManager save = SaveManager.Instance;
        bool prefsLoaded = save.IsPrefsLoaded && save.IsProfileInitialized;
        HostNativeProfileBinding profile = HostProfileControl.Observe(save);
        ActiveSurfaceSnapshot surface = ActiveInputResolver.Capture();
        bool mainMenu = surface.MenuRoot != null && ReferenceEquals(surface.MenuRoot, NGame.Instance?.MainMenu)
            && surface.MenuSubmenu == null && surface.TopOverlay == null && !surface.MapIsOpen && surface.OpenModal == null;
        bool multiplayer = RunManager.Instance.NetService?.Type.IsMultiplayer() == true;
        return new(EnvironmentIdentityRuntime.ExecutionAvailable(game), profile.Verified, prefsLoaded,
            mainMenu, RunManager.Instance.IsInProgress || NGame.Instance?.CurrentRunNode != null
                || CombatManager.Instance.IsInProgress, multiplayer, runtimeInstanceId,
            profile.GenerationId, profile.NativeProfileId, profile.NativeProfilePath,
            prefsLoaded ? ReadTypedMode(save.PrefsSave.FastMode) : null, identityKey);
    }
}
