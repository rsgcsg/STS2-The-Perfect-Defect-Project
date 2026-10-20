using System;

namespace STS2Connector.HostControl;

internal sealed record HostNativeSpeedFacts(
    bool IdentityAdmitted, bool IsolationVerified, bool PrefsLoaded,
    bool MainMenuCurrent, bool RunActive, bool Multiplayer,
    string RuntimeInstanceId, string? ProfileGenerationId,
    int? NativeProfileId, string? NativeProfilePath,
    string? Effective, string IdentityKey);

internal sealed record HostNativeSpeedStatus(
    string Status, bool Ready, string? Requested, string? Before, string? Effective,
    string RuntimeInstanceId, string? ProfileGenerationId,
    int? NativeProfileId, string? NativeProfilePath);

// One process configuration, one preparation attempt. Native facts and the
// fixed typed preference access are supplied only by HostNativeSpeedControl.
internal sealed class HostNativeSpeedSession
{
    internal string? Requested { get; }
    private string? _before;
    private HostNativeSpeedFacts? _prepared;
    private bool _attempted;

    internal HostNativeSpeedSession(string? requested) => Requested = ResolveMode(requested);

    internal static string? ResolveMode(string? mode) => mode switch
    {
        null => null,
        "normal" or "fast" or "instant" => mode,
        _ => throw new InvalidOperationException("STS2_CONNECTOR_NATIVE_SPEED must be normal, fast or instant.")
    };

    internal HostNativeSpeedStatus Prepare(HostNativeSpeedFacts facts, Action apply, Func<string?> read)
    {
        string? refusal = CommonRefusal(facts);
        if (Requested == null) return Result("native_speed_not_configured", facts);
        if (refusal != null) return Result(refusal, facts);
        if (facts.RunActive) return Result("native_speed_active_run", facts);
        if (!facts.MainMenuCurrent) return Result("native_speed_main_menu_required", facts);
        if (_prepared != null) return Observe(facts);
        if (_attempted) return Result("native_speed_preparation_already_attempted", facts);
        _before = facts.Effective;
        _attempted = true; // A lost response or uncertain setter never causes a second write.
        try
        {
            apply();
            HostNativeSpeedFacts after = facts with { Effective = read() };
            if (after.Effective != Requested) return Result("native_speed_mismatch", after);
            _prepared = after;
            return Result("native_speed_ready", after, true);
        }
        catch
        {
            return Result("native_speed_application_failed", facts with { Effective = null });
        }
    }

    internal HostNativeSpeedStatus Observe(HostNativeSpeedFacts facts)
    {
        if (Requested == null) return Result("native_speed_not_configured", facts);
        string? refusal = CommonRefusal(facts);
        if (refusal != null) return Result(refusal, facts);
        if (_prepared == null) return Result("native_speed_not_prepared", facts);
        if (_prepared.RuntimeInstanceId != facts.RuntimeInstanceId
            || _prepared.ProfileGenerationId != facts.ProfileGenerationId
            || _prepared.NativeProfileId != facts.NativeProfileId
            || _prepared.NativeProfilePath != facts.NativeProfilePath
            || _prepared.IdentityKey != facts.IdentityKey)
            return Result("native_speed_binding_changed", facts);
        // The request is the initial preference, not a gameplay identity.
        // Native UI may change between the three known modes during the run.
        return Result("native_speed_ready", facts, true);
    }

    private string? CommonRefusal(HostNativeSpeedFacts facts) =>
        !facts.IdentityAdmitted ? "native_speed_identity_unadmitted"
        : !facts.IsolationVerified ? "native_speed_isolation_unverified"
        : !facts.PrefsLoaded ? "native_speed_prefs_not_loaded"
        : facts.Multiplayer ? "native_speed_multiplayer_refused"
        : facts.Effective is not ("normal" or "fast" or "instant") ? "native_speed_unknown_effective_mode"
        : null;

    private HostNativeSpeedStatus Result(string status, HostNativeSpeedFacts facts, bool ready = false) =>
        new(status, ready, Requested, _before, facts.Effective, facts.RuntimeInstanceId,
            facts.ProfileGenerationId, facts.NativeProfileId, facts.NativeProfilePath);
}
