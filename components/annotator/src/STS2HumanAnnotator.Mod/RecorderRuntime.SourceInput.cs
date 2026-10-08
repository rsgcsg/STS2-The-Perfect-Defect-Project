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
    internal sealed record SelectorInputHandle(SelectorInput? Human, bool IsSource = false,
        NativeSourceInputInvocation? SourceInvocation = null, object? SourceOwner = null,
        string? SourceMechanism = null, SelectorInputHandle? PreviousSource = null,
        object? SourceControl = null, string? SourceCallback = null)
    { internal bool SourceCallbackClaimed { get; set; } }

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
    private static bool TryBeginSourceSelectorInput(object owner, string mechanism, object? subject,
        string? verb, object? control, string? callback, out SelectorInputHandle? result)
    {
        NativeSourceInputProvider.BeforeInputPrefix(); result = null;
        if (!IsSourceRecording) return false;
        var token = IsOrderedSourceProfile ? NativeSourceInputProvider.Begin(new(verb ?? "unknown", owner, subject,
            new Dictionary<string, object>(StringComparer.Ordinal), mechanism, mechanism)) : null;
        result = new(null, true, token, owner, mechanism, sourceSelectorInput, control, callback);
        sourceSelectorInput = result; return true;
    }
    private static bool TryFinishSourceSelectorInput(SelectorInputHandle? original, bool acceptanceProven)
    {
        if (original?.IsSource != true) return false;
        if (acceptanceProven) NativeSourceInputProvider.Accepted(original.SourceInvocation);
        NativeSourceInputProvider.Finish(original.SourceInvocation);
        if (ReferenceEquals(sourceSelectorInput, original)) sourceSelectorInput = original.PreviousSource;
        return true;
    }
    private static bool TryEnterSourceInputScope(string mechanism, string nativeType, object? owner,
        ProcessLocalObservedAction? observed, out NativeUiScopeEntry entry, string? verb = null, object? sourceControl = null)
    {
        NativeSourceInputProvider.BeforeInputPrefix();
        entry = default;
        // The Priority.First selector hook and its existing legacy semantic hook
        // share this exact original native invocation; they do not emit two roots.
        if (sourceSelectorInput is { } original && ReferenceEquals(original.SourceOwner, owner)
            && (original.SourceControl == null ? SameNativeMethod(original.SourceMechanism!, nativeType)
                : sourceControl != null && ReferenceEquals(original.SourceControl, sourceControl)
                    && !original.SourceCallbackClaimed && original.SourceCallback == nativeType))
        {
            if (original.SourceControl != null) original.SourceCallbackClaimed = true;
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
        NativeSourceInputProvider.BeforeInputPrefix();
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
