using System;
using System.Linq;
using Godot;
using MegaCrit.Sts2.Core.Helpers;
using MegaCrit.Sts2.Core.Multiplayer.Game;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Saves;
using STS2Connector.Authority;

namespace STS2Connector.HostControl;

internal sealed record HostSeedApplication(
    bool Allowed,
    bool ShouldApply,
    string Status,
    string? Seed);

internal sealed record HostEpisodeProvenanceResponse(
    string Status,
    string RuntimeInstanceId,
    string? RequestedSeed,
    string? ActualSeed,
    bool? SeedMatches)
{
    public HostNativeSpeedProvenance? NativeSpeed { get; init; }
}

internal static class HostRunSeedControl
{
    private static string? _configuredSeed;
    private static bool _applied;

    internal static bool Enabled => _configuredSeed != null;

    internal static void Configure(string? seed)
    {
        _configuredSeed = ResolveConfiguredSeed(seed);
        _applied = false;
    }

    internal static string? ResolveConfiguredSeed(string? seed)
    {
        if (string.IsNullOrWhiteSpace(seed))
            return null;
        string canonical = SeedHelper.CanonicalizeSeed(seed);
        if (canonical.Length is < 1 or > 64 || canonical.Any(character => !char.IsAsciiLetterOrDigit(character)))
        {
            throw new InvalidOperationException(
                "STS2_CONNECTOR_RUN_SEED must canonicalize to 1-64 ASCII letters or digits.");
        }
        return canonical;
    }

    internal static HostSeedApplication EvaluateApplication(
        string? configuredSeed,
        string displayServer,
        string? currentOverride,
        bool alreadyApplied,
        bool isolatedNativeUi = false)
    {
        if (configuredSeed == null)
            return new HostSeedApplication(true, false, "seed_not_configured", null);
        if (!string.Equals(displayServer, "headless", StringComparison.OrdinalIgnoreCase) && !isolatedNativeUi)
            return new HostSeedApplication(false, false, "seed_requires_headless_host", configuredSeed);
        if (currentOverride != null
            && !string.Equals(currentOverride, configuredSeed, StringComparison.Ordinal))
        {
            return new HostSeedApplication(false, false, "seed_override_conflict", configuredSeed);
        }
        return new HostSeedApplication(
            true,
            !alreadyApplied && currentOverride == null,
            alreadyApplied || currentOverride != null ? "seed_already_applied" : "seed_ready_to_apply",
            configuredSeed);
    }

    internal static HostSeedApplication ApplyForEmbark()
    {
        NGame? game = NGame.Instance;
        if (game == null)
            return new HostSeedApplication(false, false, "native_game_unavailable", _configuredSeed);
        HostSeedApplication decision = EvaluateApplication(
            _configuredSeed,
            DisplayServer.GetName(),
            game.DebugSeedOverride,
            _applied,
            isolatedNativeUi: HostProfileControl.Enabled
                && HostProfileControl.Observe(SaveManager.Instance).Verified
                && EnvironmentIdentityRuntime.ExecutionAvailable(EnvironmentIdentityRuntime.ReadGame()));
        if (!decision.Allowed)
            return decision;
        if (decision.ShouldApply)
            game.DebugSeedOverride = decision.Seed;
        if (decision.Seed != null)
            _applied = true;
        return decision with { Status = decision.Seed == null ? decision.Status : "seed_applied_for_embark" };
    }

    internal static HostEpisodeProvenanceResponse Observe(string runtimeInstanceId)
    {
        string? actualSeed = RunManager.Instance.IsInProgress
            ? RunManager.Instance.DebugOnlyGetState()?.Rng.StringSeed
            : null;
        bool? matches = actualSeed == null || _configuredSeed == null
            ? null
            : string.Equals(actualSeed, _configuredSeed, StringComparison.Ordinal);
        string status = _configuredSeed == null
            ? "seed_not_configured"
            : actualSeed == null
                ? _applied ? "seed_applied_waiting_for_run" : "seed_configured"
                : matches == true ? "seed_observed" : "seed_mismatch";
        return new HostEpisodeProvenanceResponse(
            status,
            runtimeInstanceId,
            _configuredSeed,
            actualSeed,
            matches) { NativeSpeed = HostNativeSpeedControl.Observe(runtimeInstanceId) };
    }
}
