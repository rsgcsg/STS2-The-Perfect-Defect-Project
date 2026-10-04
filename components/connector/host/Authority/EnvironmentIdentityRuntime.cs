using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using Godot;
using MegaCrit.Sts2.Core.Debug;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;

namespace STS2Connector.Authority;

/// <summary>
/// Exact process, artifact, game and Modset identity for Player Environment.
/// Compatibility identity does not grant UI action authority; current native
/// actionability is rediscovered and revalidated elsewhere.
/// </summary>
internal static class EnvironmentIdentityRuntime
{
    private static readonly string RuntimeInstanceId = Guid.NewGuid().ToString("N");
    private static readonly ProcessImmutableValue<LoadedMainAssemblyIdentity>
        MainAssemblyIdentity = new(ReadLoadedMainAssemblyIdentity);
    private static readonly ProcessImmutableValue<RuntimeSealSnapshot> LoadedRuntimeSeal =
        new(() => RuntimeSealQualification.ReadInstalledPair(typeof(ConnectorMod).Assembly.Location));

    // Capture installed documents once before serving capabilities. A later
    // sidecar write cannot qualify an already running process in place.
    internal static void FreezeInstalledRuntimeSeal() => LoadedRuntimeSeal.Read();

    internal static GameBuildIdentity ReadGame()
    {
        ReleaseInfo? release = null;
        try
        {
            release = ReleaseInfoManager.Instance.ReleaseInfo;
        }
        catch
        {
            // Missing release metadata remains explicit in the public identity.
        }

        int? assemblyHash = null;
        try
        {
            assemblyHash = AssemblyHasher.GetMainAssemblyHash();
        }
        catch
        {
            // Missing game identity disables mutation but not fair observation.
        }

        LoadedMainAssemblyIdentity mainAssembly = MainAssemblyIdentity.Read();
        string? mainAssemblySha256 = mainAssembly.Sha256;
        string? mainAssemblyMvid = mainAssembly.ModuleVersionId;

        ExactGamePermission gamePermission = ExactGameCompatibility.Evaluate(
            release?.Version,
            release?.Commit,
            assemblyHash,
            mainAssemblySha256,
            mainAssemblyMvid,
            ExactGameCompatibility.CurrentPlatform(),
            ExactGameCompatibility.CurrentArchitecture(),
            System.Environment.GetEnvironmentVariable(
                ExactGameCompatibility.CanaryEnvironmentVariable));
        ExactArtifactPermission artifactPermission = ExactArtifactCompatibility.Evaluate(
            HostArtifactIdentity.SourceRevision,
            HostArtifactIdentity.LoadedAssemblySha256,
            HostArtifactIdentity.LoadedAssemblyMvid,
            System.Environment.GetEnvironmentVariable(
                ExactArtifactCompatibility.CanarySourceRevisionEnvironmentVariable));
        ModsetIdentity modset = LiveModsetIdentity.Read();
        RuntimeSealSnapshot runtimeSeal = LoadedRuntimeSeal.Read();
        if (runtimeSeal.Present)
        {
            RuntimeSealPermission sealPermission = RuntimeSealQualification.Evaluate(runtimeSeal,
                new LoadedRuntimeSealIdentity(
                    HostArtifactIdentity.LoadedImplementationId,
                    HostArtifactIdentity.LoadedPackageVersion,
                    HostArtifactIdentity.SourceRevision,
                    HostArtifactIdentity.PlatformSourceRevision,
                    HostArtifactIdentity.CompiledSourceDigestSha256,
                    HostArtifactIdentity.LoadedAssemblySha256,
                    HostArtifactIdentity.LoadedAssemblyMvid,
                    PlayerEnvironment.Protocol.PlayerEnvironmentContract.ProtocolVersion,
                    ExactGameCompatibility.CurrentPlatform(), ExactGameCompatibility.CurrentArchitecture(),
                    RuntimeSealQualification.ReadObservedHostKind(
                        () => DisplayServer.GetName(), ExactGameCompatibility.CurrentPlatform()),
                    release?.Version, release?.Commit, assemblyHash,
                    mainAssemblySha256, mainAssemblyMvid, modset.Status,
                    LiveModsetIdentity.FingerprintScope, modset.Fingerprint));
            // A present broken pair cannot fall back to a process canary or a
            // predecessor seal. Only the exact current pair owns admission.
            artifactPermission = new ExactArtifactPermission(
                sealPermission.ActionExecutionAllowed ? "supported_exact" : "artifact_unqualified",
                sealPermission.ActionExecutionAllowed, sealPermission.Detail);
        }
        bool executionIdentityComplete = gamePermission.ActionExecutionAllowed
                                         && artifactPermission.ActionExecutionAllowed
                                         && IsExactSupportedModset(modset);
        bool canaryModset = string.Equals(
            modset.Status,
            "canary_exact_observer_modset",
            StringComparison.Ordinal);
        string permissionStatus = !gamePermission.ActionExecutionAllowed
            ? gamePermission.Status
            : !artifactPermission.ActionExecutionAllowed
                ? artifactPermission.Status
                : !canaryModset
                  && string.Equals(gamePermission.Status, "supported_exact", StringComparison.Ordinal)
                  && string.Equals(artifactPermission.Status, "supported_exact", StringComparison.Ordinal)
                    ? "supported_exact"
                    : "canary_exact";
        var compatibility = new CompatibilityAssessment(
            executionIdentityComplete
                ? permissionStatus
                : gamePermission.ActionExecutionAllowed
                  && artifactPermission.ActionExecutionAllowed
                    ? "identity_incomplete"
                    : permissionStatus,
            ActionExecutionAllowed: executionIdentityComplete,
            StateObservationAllowed: true,
            ReadAllowed: true,
            executionIdentityComplete
                ? $"{gamePermission.Detail} {artifactPermission.Detail} Exact Modset identity is recorded; current native UI mechanics determine actionability."
                : gamePermission.ActionExecutionAllowed && artifactPermission.ActionExecutionAllowed
                    ? "The exact game and Host artifact are admitted, but mutation is disabled until exact Modset identity is complete."
                    : !gamePermission.ActionExecutionAllowed
                        ? gamePermission.Detail
                        : artifactPermission.Detail);
        return new GameBuildIdentity(
            release?.Version,
            release?.Commit,
            release?.Branch,
            assemblyHash,
            compatibility,
            modset)
        {
            ReleaseDeclaredMainAssemblyHash = release?.MainAssemblyHash,
            MainAssemblySha256 = mainAssemblySha256,
            MainAssemblyMvid = mainAssemblyMvid
        };
    }

    internal static LiveHostIdentity HostIdentity() => new(
        "sts2_live_player_environment_host",
        "STS2 Live Player Environment Host",
        ConnectorMod.Version,
        HostArtifactIdentity.SourceRevision ?? "unavailable",
        typeof(ConnectorMod).Assembly.ManifestModule.ModuleVersionId.ToString("D"),
        RuntimeInstanceId)
    {
        ArtifactSha256 = HostArtifactIdentity.LoadedAssemblySha256 ?? string.Empty
    };

    internal static string HostKind()
    {
        try
        {
            return HostKind(DisplayServer.GetName());
        }
        catch
        {
            return "live_ui";
        }
    }

    internal static string HostKind(string? displayServerName) =>
        string.Equals(displayServerName, "headless", StringComparison.OrdinalIgnoreCase)
            ? "headless"
            : "live_ui";

    internal static InformationPolicyInfo InformationPolicy() => new(
        "player_visible_v1",
        "Information currently presented by, or normally inspectable through, the local player's game UI.",
        IncludesHiddenInformation: false,
        UnknownFieldBehavior: "omit_and_mark_incomplete");

    internal static bool ExecutionAvailable(GameBuildIdentity game) =>
        ExecutionAvailable(
            game,
            HostArtifactIdentity.LoadedAssemblySha256,
            HostArtifactIdentity.SourceRevision,
            HostArtifactIdentity.LoadedAssemblyMvid);

    internal static bool ExecutionAvailable(
        GameBuildIdentity game,
        string? loadedAssemblySha256,
        string? sourceRevision,
        string? loadedAssemblyMvid = "test-mvid") =>
        game.Compatibility.ActionExecutionAllowed
        && ExactGameCompatibility.IsExecutionStatus(game.Compatibility.Status)
        && game.Compatibility.StateObservationAllowed
        && !string.IsNullOrWhiteSpace(game.Version)
        && !string.IsNullOrWhiteSpace(game.Commit)
        && game.MainAssemblyHash.HasValue
        && !string.IsNullOrWhiteSpace(game.MainAssemblySha256)
        && !string.IsNullOrWhiteSpace(game.MainAssemblyMvid)
        && !string.IsNullOrWhiteSpace(loadedAssemblySha256)
        && !string.IsNullOrWhiteSpace(loadedAssemblyMvid)
        && IsExactSourceRevision(sourceRevision)
        && IsExactSupportedModset(game.Modset);

    private static bool IsExactSupportedModset(ModsetIdentity? modset) =>
        string.Equals(modset?.Status, "exact_player_environment_only", StringComparison.Ordinal)
        || string.Equals(modset?.Status, "exact_platform_modset", StringComparison.Ordinal)
        || string.Equals(modset?.Status, "canary_exact_observer_modset", StringComparison.Ordinal);

    private static bool IsExactSourceRevision(string? sourceRevision) =>
        sourceRevision?.Length == 40
        && sourceRevision.All(character =>
            character is >= '0' and <= '9' or >= 'a' and <= 'f');

    private static LoadedMainAssemblyIdentity ReadLoadedMainAssemblyIdentity()
    {
        try
        {
            var assembly = typeof(ReleaseInfoManager).Assembly;
            string moduleVersionId = assembly.ManifestModule.ModuleVersionId.ToString("D");
            if (string.IsNullOrWhiteSpace(assembly.Location))
                return new LoadedMainAssemblyIdentity(null, moduleVersionId);
            using FileStream stream = File.OpenRead(assembly.Location);
            string sha256 = Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
            return new LoadedMainAssemblyIdentity(sha256, moduleVersionId);
        }
        catch
        {
            // Loaded assembly identity is immutable for this process. A missing
            // exact fingerprint remains fail-closed for the process lifetime.
            return new LoadedMainAssemblyIdentity(null, null);
        }
    }
}

internal sealed record LoadedMainAssemblyIdentity(string? Sha256, string? ModuleVersionId);

/// <summary>
/// A process-loaded assembly cannot change its bytes without a new process.
/// This cache is deliberately identity-only; native state, Modset observation,
/// actionability and execution-time revalidation remain live on every call.
/// </summary>
internal sealed class ProcessImmutableValue<T>(Func<T> factory)
{
    private readonly Lazy<T> _value = new(factory, isThreadSafe: true);

    internal T Read() => _value.Value;
}
