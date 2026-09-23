using STS2Connector.NativeUi;

namespace STS2Connector.PlayerEnvironment;

/// <summary>
/// Keeps an opted-in reward snapshot stable across unchanged legacy observations,
/// while any observed native change invalidates an earlier reward snapshot.
/// Each view has its own ID sequence, but both invalidate old tokens when a
/// different native state was observed through either view.
/// </summary>
internal sealed class RewardPageSnapshotIdentity
{
    private readonly SnapshotIdentityTracker _native = new();
    private readonly SnapshotIdentityTracker _legacy = new();
    private readonly SnapshotIdentityTracker _reward = new();

    internal long ObserveNative(string nativeSignature) =>
        _native.Observe(nativeSignature).Sequence;

    internal (string StateId, long Sequence) ObserveLegacy(
        long nativeSequence, string legacySignature) =>
        _legacy.Observe(StableIdentityHash.Object(new { nativeSequence, legacySignature }));

    internal (string StateId, long Sequence) ObserveReward(
        long nativeSequence, string rewardSignature) =>
        _reward.Observe(StableIdentityHash.Object(new { nativeSequence, rewardSignature }));
}
