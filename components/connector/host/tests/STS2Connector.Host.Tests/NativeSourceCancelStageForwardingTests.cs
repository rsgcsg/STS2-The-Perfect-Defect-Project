using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Combat;
using STS2Platform.NativeFoundation;

namespace STS2Connector.Host.Tests;

/// <summary>Detached forwarding fixtures only; these do not qualify the native Finished signal receipt.</summary>
[Collection("Native source carrier observer isolation")]
public sealed class NativeSourceCancelStageForwardingTests
{
    private static T Bare<T>() => (T)RuntimeHelpers.GetUninitializedObject(typeof(T));
    private static NativeSourceCardPlayCancelScope Stage(NativeSourceInputInvocation original, object anchor) =>
        new(original.Registration, anchor, Bare<NMouseCardPlay>(), Bare<NPlayerHand>(), Bare<NHandCardHolder>(), new(), original);
    private static NativeSourceInputPrefix Prefix() => new("confirm_card_play", new(), new(), new Dictionary<string, object>(),
        "NCardPlay.TryPlayCard", "PlayCardAction", typeof(PlayCardAction));

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void OriginalFinishCarriesCancelFactAndPreservesUnknownWithoutClaimingNativeCommit(bool threw)
    {
        object token = new(), anchor = new(); NativeSourceStageWitness? stage = null;
        string? delivery = null, reason = null; var carriers = new List<string>();
        using var registration = NativeSourceInputProvider.Register(_ => token,
            (_, actual, code) => { delivery = actual; reason = code; },
            (actual, _, kind) => { Assert.Same(token, actual); carriers.Add(kind); },
            () => anchor, (actual, value) => { Assert.Same(anchor, actual); stage = value; });
        var original = NativeSourceInputProvider.Begin(Prefix())!;
        original.PendingCancel = Stage(original, anchor);
        NativeSourceInputProvider.Finish(original, threw ? new InvalidOperationException("native fixture") : null);
        Assert.Equal("unknown", delivery);
        Assert.Equal(threw ? "native_source_input_boundary_threw" : "native_source_scope_ended_without_acceptance", reason);
        Assert.Equal(new[] { "card_play_canceled" }, carriers);
        Assert.Equal(threw ? null : (bool?)true, stage!.NoSubmission);
        Assert.Equal("native_finished_false", stage.Evidence);
        NativeSourceInputProvider.Finish(original); Assert.Single(carriers);
    }
    [Fact]
    public void SubmittedButNotAcceptedCarrierCannotBecomeNoSubmissionFromCancellation()
    {
        NativeSourceStageWitness? stage = null; int terminals = 0;
        using var registration = NativeSourceInputProvider.Register(_ => new(), (_, _, _) => terminals++,
            stageAnchor: () => new(), stage: (_, value) => stage = value);
        var original = NativeSourceInputProvider.Begin(Prefix())!;
        var action = Bare<PlayCardAction>(); NativeSourceInputProvider.BindSubmitted(action);
        original.PendingCancel = Stage(original, new()); NativeSourceInputProvider.Finish(original);
        Assert.Null(stage!.NoSubmission); Assert.Equal(0, terminals);
        Assert.True(NativeSourceInputProvider.ObserveAccepted(action)); Assert.Equal(1, terminals);
    }
    [Fact]
    public void DisposedOriginalRegistrationNeverRedirectsItsPendingStageToReplacementSink()
    {
        int replacement = 0;
        using var old = NativeSourceInputProvider.Register(_ => new(), (_, _, _) => { },
            stageAnchor: () => new(), stage: (_, _) => throw new InvalidOperationException("old sink must be disposed"));
        var original = NativeSourceInputProvider.Begin(Prefix())!; original.PendingCancel = Stage(original, new()); old.Dispose();
        using var next = NativeSourceInputProvider.Register(_ => new(), (_, _, _) => replacement++,
            stageAnchor: () => new(), stage: (_, _) => replacement++);
        NativeSourceInputProvider.Finish(original); Assert.Equal(0, replacement);
    }
}
