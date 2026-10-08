using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.GameActions.Multiplayer;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Combat;
using STS2Connector.PlayerEnvironment.Witness;
using STS2HumanAnnotator.Core;
using STS2Platform.NativeFoundation;

namespace STS2HumanAnnotator.Mod;

internal static partial class RecorderRuntime
{
    internal static bool IsOrderedSourceProfile => _activeCaptureProfileId == SourceSessionContractV3.ProfileId;
    [ThreadStatic] private static SelectorInputHandle? sourceSelectorInput;
    private static bool SourceSelectorActive => sourceSelectorInput != null;
    private static Type? SourceCarrierType(string nativeType) => nativeType switch
    {
        nameof(PlayCardAction) => typeof(PlayCardAction), nameof(UsePotionAction) => typeof(UsePotionAction),
        nameof(EndPlayerTurnAction) => typeof(EndPlayerTurnAction), nameof(VoteForMapCoordAction) => typeof(VoteForMapCoordAction),
        nameof(PickRelicAction) => typeof(PickRelicAction), _ => null
    };
    private static bool SameNativeMethod(string first, string second) => first == second
        || first.EndsWith("." + second, StringComparison.Ordinal);
    private static bool TryEnterSourceInputScope(string mechanism, string nativeType, object? owner,
        ProcessLocalObservedAction? observed, out NativeUiScopeEntry entry, string? verb = null)
    {
        entry = default;
        // The Priority.First selector hook and its existing legacy semantic hook
        // share this exact original native invocation; they do not emit two roots.
        if (sourceSelectorInput is { } original && ReferenceEquals(original.SourceOwner, owner)
            && SameNativeMethod(original.SourceMechanism!, nativeType))
        {
            entry = new(false, false, SourceInvocation: original.SourceInvocation, SourceInvocationBorrowed: true);
            return true;
        }
        if (!IsSourceRecording) return false;
        if (IsOrderedSourceProfile)
        {
            var invocation = NativeSourceInputProvider.Begin(new(verb ?? observed?.Verb ?? "unknown", owner, observed?.Subject,
                observed?.Arguments ?? new Dictionary<string, object>(StringComparer.Ordinal), mechanism,
                nativeType, SourceCarrierType(nativeType)));
            entry = new(false, false, SourceInvocation: invocation);
        }
        return true;
    }
    private sealed class SourceCardStartScope(NativeSourceInputInvocation? invocation) : IDisposable
    {
        internal readonly NativeSourceInputInvocation? Invocation = invocation;
        public void Dispose() => NativeSourceInputProvider.Finish(Invocation);
    }
    internal static IDisposable? StageNativeCardPlay(NPlayerHand hand, NHandCardHolder holder)
    {
        if (!IsSourceRecording) return StageCardPlay(holder);
        return IsOrderedSourceProfile ? new SourceCardStartScope(NativeSourceInputProvider.Begin(new("begin_card_play",
            hand, holder.CardModel, new Dictionary<string, object>(StringComparer.Ordinal) { ["holder"] = holder },
            "NPlayerHand.StartCardPlay", "NPlayerHand.StartCardPlay"))) : null;
    }
    internal static void ObserveSourceCardStart(IDisposable? original, NPlayerHand hand, NHandCardHolder holder)
    {
        if (original is SourceCardStartScope scope)
            NativeSourceInputProvider.ObserveCreatedCardPlay(scope.Invocation, hand, holder);
    }
    internal static bool SourceInputAccepted(NativeUiScopeEntry original)
    {
        if (original.SourceInvocation == null) return false;
        NativeSourceInputProvider.Accepted(original.SourceInvocation); return true;
    }
}
