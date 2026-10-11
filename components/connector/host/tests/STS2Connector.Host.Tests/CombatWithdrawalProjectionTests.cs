using System.Text.Json.Nodes;
using System.Text.Json;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2Connector.PlayerEnvironment.NativeLogical;
using Xunit;

namespace STS2Connector;

using NativeLogicalCapture = global::STS2Connector.PlayerEnvironment.NativeLogicalCapture;

public sealed class CombatWithdrawalProjectionTests
{
    private sealed class NativeSource
    {
        internal CombatInputLifecycle Scope = new(new(), new(), new(), 1);
        internal readonly CombatInputWithdrawalWindow Window = new();
        internal bool ButtonEnabled = true;
        internal int Cards, Potions;
        internal long Now;
        internal bool Complete = true;
        internal bool Active = true;
        internal bool OpenPlay = true;
        internal bool KnownTransition = true;
        internal bool? FtueSeen = true;
        internal string Owner = "exact-native-owner";
        internal CombatWithdrawalProof? Capture()
        {
            Window.ObserveLifecycle(Scope);
            return Window.Observe(Active && FtueSeen == true ? new(Scope, Owner, Cards + Potions + (ButtonEnabled ? 1 : 0), Complete,
                !ButtonEnabled && OpenPlay, !ButtonEnabled && KnownTransition) : null, Complete, Now);
        }
        internal LiveObservation Draft()
        {
            var surface = new CombatTurnSurface("combat_turn", "room", ButtonEnabled)
            {
                PlayableCards = Enumerable.Range(0, Cards).Select(i => new VisibleCombatCommandOption("card-" + i, "Card", Array.Empty<string>())).ToArray(),
                UsablePotions = Enumerable.Range(0, Potions).Select(i => new VisibleCombatCommandOption("potion-" + i, "Potion", Array.Empty<string>())).ToArray()
            };
            return new("same-native-facts", "ready", new MenuLiveContext("menu", "fixture"), surface,
                new("contract_complete_for_immediate_combat_turn", "native", Array.Empty<string>(), Complete ? Array.Empty<string>() : new[] { "incomplete-source" }),
                new("exact", "commit", null, 1, new("exact", true, true, true, "Fixture")), Array.Empty<string>())
            { CombatWithdrawal = Capture() };
        }
    }
    private static BoundActionProjectionResult Project(LiveObservation draft)
    {
        var surface = (CombatTurnSurface)draft.Surface;
        var visible = surface.PlayableCards.Select(c => (Option: c, Role: "card"))
            .Concat(surface.UsablePotions.Select(p => (Option: p, Role: "potion")))
            .ToDictionary(x => x.Option.EntityId, x => new PlayerEnvironmentReferent(x.Option.EntityId,
                x.Role, "entity", x.Option.Name, new(true, true, false, false, "native_visible_fact"), null, null));
        return PlayerEnvironmentService.ProjectBoundActions(NativeUiActionRuntime.BuildBindings(draft), "room", visible);
    }
    private static CombatInputView Macro(LiveObservation draft) => PlayerEnvironmentService.FinalSnapshotView(draft,
        Project(draft).Projection, Array.Empty<string>(), false, false, "ready");
    private static TextMenuFrame Rich(LiveObservation draft, params TextMenuLeaf[] leaves)
    {
        var projection = Project(draft);
        var view = PlayerEnvironmentService.FinalSnapshotView(draft, projection.Projection, Array.Empty<string>(), false, true, "ready");
        var page = new PlayerEnvironmentSnapshot("1.0.0", PlayerEnvironmentContract.SnapshotSchema, "snapshot", 1, DateTimeOffset.UnixEpoch,
            view.Status, null, new("room", "combat_turn", view.Stage, null, "fixture", new(new JsonObject(), new JsonObject()),
                Array.Empty<PlayerEnvironmentInteractionCapability>()), Array.Empty<PlayerEnvironmentReferent>(), projection.Projection,
            Array.Empty<PlayerEnvironmentReadOpportunity>(), view.Completeness, new("runtime", "environment"), new("player_visible_v1", "current", false, "omit"));
        return new(page, "same-native-owner", leaves) { CardPlayCatalogComplete = true, CombatWithdrawal = draft.CombatWithdrawal };
    }
    private static TextMenuLeaf Info() => new("native-info", "root", "open_information", "Information", null,
        Array.Empty<PlayerEnvironmentBoundActionArgument>(), () => NativeInputResult.Delivered("native-info-input"));

    [Fact]
    public void DeliveredNativeGestureThenCompleteMacroC0IsBoundedSettlingInsteadOfObservedReady()
    {
        var source = new NativeSource(); var offered = source.Draft();
        Assert.Equal("interactive", Macro(offered).Status);
        var delivery = NativeEndTurnInput.Deliver(() => source.ButtonEnabled = false);
        Assert.True(delivery.Accepted);
        var current = source.Draft(); Assert.Empty(Project(current).Projection.Actions);
        Assert.Equal("ready", current.Readiness); // Shared reader remains eligible for richer discovery.
        var view = Macro(current);
        Assert.Equal("settling", view.Status); Assert.Equal("settling", view.Stage);
        Assert.Equal("complete", view.Completeness.Status); Assert.Empty(view.Completeness.Missing);
    }

    [Theory]
    [InlineData("text1")]
    [InlineData("text2")]
    [InlineData("native")]
    public void TrueNoLeafFinalRelationsSettleAfterTheirRealProjection(string profile)
    {
        var source = new NativeSource { ButtonEnabled = false }; var draft = source.Draft();
        var staging = Rich(draft); Assert.Equal("interactive", staging.Page.Status);
        var final = profile == "native" ? NativeLogicalCapture.Finish(staging)
            : CombatWithdrawalProjection.Finalize(staging, semanticCards: profile == "text2");
        Assert.Equal("settling", final.Page.Status); Assert.Empty(final.Leaves);
        if (profile == "text1") Assert.Equal("settling", new TextMenuSession().Observe(final).Snapshot.Status);
        if (profile == "text2") Assert.Equal("settling", new TextMenuV2Session().Observe(final).Snapshot.Status);
    }

    [Theory]
    [InlineData(0, 1)]
    [InlineData(1, 0)]
    public void ActualRemainingCardOrPotionMacroCandidatesStayInteractive(int cards, int potions)
    {
        var source = new NativeSource { ButtonEnabled = false, Cards = cards, Potions = potions };
        var draft = source.Draft(); Assert.Null(draft.CombatWithdrawal);
        Assert.NotEmpty(NativeUiActionRuntime.BuildBindings(draft)); Assert.Equal("interactive", Macro(draft).Status);
    }

    [Theory]
    [InlineData("text1")]
    [InlineData("text2")]
    [InlineData("native")]
    public void RichNativeInformationSurvivesMacroExpiryWithoutRenewingTheClock(string profile)
    {
        var source = new NativeSource { ButtonEnabled = false }; source.Draft(); source.Now = 20_000;
        var draft = source.Draft(); Assert.Equal(CombatWithdrawalState.Expired, draft.CombatWithdrawal!.State);
        var final = profile == "native" ? NativeLogicalCapture.Finish(Rich(draft, Info()))
            : CombatWithdrawalProjection.Finalize(Rich(draft, Info()), semanticCards: profile == "text2");
        Assert.Equal("interactive", final.Page.Status); Assert.Single(final.Leaves);
        Assert.Equal("visible_unsupported", Macro(source.Draft()).Status);
    }

    [Fact]
    public void MixedProfilesFactChangesAndUiReplacementDoNotExtendExactDeadline()
    {
        var source = new NativeSource { ButtonEnabled = false }; var first = source.Draft();
        source.Now = 7_000; source.Owner = "replacement-ui-same-turn";
        var rich = CombatWithdrawalProjection.Finalize(Rich(source.Draft(), Info())); Assert.Single(rich.Leaves);
        source.Now = 19_999; Assert.Equal("settling", Macro(source.Draft() with { Signature = "changed-rich-facts" }).Status);
        source.Now = 20_000; var expired = Macro(source.Draft());
        Assert.Equal("visible_unsupported", expired.Status); Assert.Contains(CombatWithdrawalProjection.ExpiredReason, expired.Completeness.Missing);
        source.Now = 90_000; Assert.Equal("visible_unsupported", Macro(source.Draft()).Status);
    }

    [Fact]
    public void OverlayAndIncompleteCaptureNeitherStartNorResetBudgetOrPublishItsProof()
    {
        var source = new NativeSource { ButtonEnabled = false, Active = false };
        Assert.Null(source.Draft().CombatWithdrawal); source.Now = 10_000; source.Active = true;
        Assert.Equal(CombatWithdrawalState.Pending, source.Draft().CombatWithdrawal!.State);
        source.Now = 15_000; source.Active = false; Assert.Null(source.Draft().CombatWithdrawal);
        source.Now = 19_000; source.Active = true; source.Complete = false;
        Assert.Null(source.Draft().CombatWithdrawal); source.Cards = 1; Assert.Null(source.Draft().CombatWithdrawal);
        source.Cards = 0; source.Complete = true; source.Now = 30_000;
        Assert.Equal(CombatWithdrawalState.Expired, source.Draft().CombatWithdrawal!.State);
    }

    [Fact]
    public void PositiveGameplayRecoveryOrRealLifecycleReplacementRetiresStickyExpiry()
    {
        var source = new NativeSource { ButtonEnabled = false }; source.Draft(); source.Now = 20_000;
        Assert.Equal(CombatWithdrawalState.Expired, source.Draft().CombatWithdrawal!.State);
        source.Cards = 1; Assert.Null(source.Draft().CombatWithdrawal); source.Cards = 0; source.Now = 20_001;
        Assert.Equal(CombatWithdrawalState.Pending, source.Draft().CombatWithdrawal!.State);
        source.Now = 40_001; Assert.Equal(CombatWithdrawalState.Expired, source.Draft().CombatWithdrawal!.State);
        source.Scope = source.Scope with { Turn = 2 }; Assert.Equal(CombatWithdrawalState.Pending, source.Draft().CombatWithdrawal!.State);
        source.Now = 80_000; source.Scope = source.Scope with { Combat = new() }; Assert.Equal(CombatWithdrawalState.Pending, source.Draft().CombatWithdrawal!.State);
    }

    [Fact]
    public void KnownSameTurnContinuationKeepsExistingWindowButCannotInventFirstStart()
    {
        var source = new NativeSource { ButtonEnabled = false, OpenPlay = false };
        Assert.Null(source.Draft().CombatWithdrawal); source.OpenPlay = true; source.Draft();
        source.OpenPlay = false; source.Now = 20_000;
        Assert.Equal(CombatWithdrawalState.Expired, source.Draft().CombatWithdrawal!.State);
        source.KnownTransition = false; Assert.Null(source.Draft().CombatWithdrawal);
        source.KnownTransition = true; Assert.Equal(CombatWithdrawalState.Expired, source.Draft().CombatWithdrawal!.State);
    }

    [Fact]
    public void AvailabilityIdentityChangesOnlyOnDiscreteExpiryNotEveryPoll()
    {
        var source = new NativeSource { ButtonEnabled = false }; var first = source.Draft();
        string Fingerprint(LiveObservation d) => PlayerEnvironmentService.CommonNativeSignature(d.Game, d, null);
        source.Now = 19_999; Assert.Equal(Fingerprint(first), Fingerprint(source.Draft()));
        source.Now = 20_000; Assert.NotEqual(Fingerprint(first), Fingerprint(source.Draft()));
        source.Now = 99_999; var last = source.Draft(); source.Now++;
        Assert.Equal(Fingerprint(last), Fingerprint(source.Draft()));
    }

    [Theory]
    [InlineData(false)]
    [InlineData(null)]
    public void FtueFailureDoesNotStartOrRenewTheQualifiedWindow(bool? seen)
    {
        var source = new NativeSource { ButtonEnabled = false, FtueSeen = seen };
        Assert.Null(source.Draft().CombatWithdrawal); source.Now = 10_000; source.FtueSeen = true; source.Draft();
        source.Now = 19_000; source.FtueSeen = seen; Assert.Null(source.Draft().CombatWithdrawal);
        source.Now = 30_000; source.FtueSeen = true;
        Assert.Equal(CombatWithdrawalState.Expired, source.Draft().CombatWithdrawal!.State);
        var failed = CombatTurnSurfaceReader.ApplyEndTurnCapturePolicy(source.Draft(), seen == false
            ? NativeEndTurnInput.FtueUnseen : NativeEndTurnInput.FtueUnknown);
        Assert.IsType<UnsupportedSurface>(failed.Surface); Assert.Null(failed.CombatWithdrawal);
    }

    [Fact]
    public void FinalMappedSnapshotAndTextIdsChangeAtExpiryBeforePublication()
    {
        var source = new NativeSource { ButtonEnabled = false }; var epoch = new RewardPageSnapshotIdentity();
        (string, string, string) Ids()
        {
            var draft = source.Draft(); var view = Macro(draft);
            string signature = PlayerEnvironmentService.FinalSnapshotSignature("same-public-facts", draft.CombatWithdrawal, view);
            var id = epoch.ObserveLegacy(epoch.ObserveNative(PlayerEnvironmentService.CommonNativeSignature(draft.Game, draft, null)), signature);
            var final = CombatWithdrawalProjection.Finalize(Rich(draft));
            return (id.StateId, new TextMenuSession().Observe(final).Snapshot.SnapshotId,
                new TextMenuV2Session().Observe(final).Snapshot.SnapshotId);
        }
        var pending = Ids(); source.Now = 19_999; Assert.Equal(pending, Ids());
        source.Now = 20_000; var expired = Ids(); Assert.NotEqual(pending, expired);
        source.Now++; Assert.Equal(expired, Ids());
    }

    [Fact]
    public void PartialUnsupportedAndUnknownOwnerCannotBePromotedByAnEmptyRelation()
    {
        var proof = new CombatWithdrawalProof("private-owner", CombatWithdrawalState.Pending);
        var partial = new PlayerEnvironmentCompleteness("partial", "partial", "unknown", new[] { "missing" }, Array.Empty<string>());
        Assert.Equal("visible_unsupported", CombatWithdrawalProjection.Apply("visible_unsupported", "unknown", partial, true, 0, proof).Status);
        var complete = partial with { Status = "complete", Missing = Array.Empty<string>() };
        Assert.Equal("observed", CombatWithdrawalProjection.Apply("observed", "ready", complete, true, 0, null).Status);
    }

    [Fact]
    public void ActualNativeCurrentPublishesCompleteEmptySettlingThenFailsClosedAtExactExpiry()
    {
        var source = new NativeSource { ButtonEnabled = false };
        using var requests = RequestTestDriver.Namespace(); var queue = new MainThreadWorkQueue();
        using var owner = new NativeLogicalService(() => NativeLogicalCapture.Finish(Rich(source.Draft())) with { GameContinuityId = "run" },
            () => "run", requests, (work, cancel) => queue.Enqueue(work, cancel), () => true, clientActive: _ => true);
        owner.Initialize();
        NativeLogicalCurrentReply Current()
        { var task = owner.CurrentAsync(new("client", NativeLogicalProjector.ScopeFields, null)); queue.Drain(1); return task.GetAwaiter().GetResult(); }
        var first = Current(); Assert.NotNull(first.Capture);
        var chunk = owner.Store.Read(first.Capture.CaptureId, first.Capture.ReadCursor, 1024 * 1024);
        using var body = JsonDocument.Parse(Convert.FromBase64String(chunk.DataBase64));
        Assert.Equal("settling", body.RootElement.GetProperty("status").GetString());
        Assert.Equal("settling", body.RootElement.GetProperty("interaction").GetProperty("stage").GetString());
        Assert.Empty(owner.Store.Catalog(first.Capture.CaptureId).Actions);
        source.Now = 20_000; var expired = Current();
        Assert.Equal("source_capture_incomplete", expired.Status);
        Assert.Equal(CombatWithdrawalProjection.ExpiredReason, expired.Reason);
    }
}
