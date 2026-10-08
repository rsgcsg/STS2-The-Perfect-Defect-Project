using System.Text.Json.Nodes;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector.Tests;

public sealed class NativeLogicalSourceHubTests
{
    private sealed class Fixture : IDisposable
    {
        internal long Now;
        internal readonly NativeLogicalCaptureStore Store;
        internal readonly NativeLogicalPublicationHub Hub;
        internal readonly NativeLogicalProjector Projector = new();
        internal readonly NativeLogicalLimits Limits;
        internal Fixture(int events = 8)
        {
            Limits = new(MaxEvents: events);
            Store = new(() => Now, Limits);
            Hub = new(Store, new[] { new NativeLogicalSeamCoverage("bootstrap", "1", "complete_at_seam") }, () => Now, Limits);
        }
        internal static NativeLogicalAttachRequest Request(string client) => new(client, NativeLogicalProjector.ScopeFields,
            new[] { new NativeLogicalSeamCoverage("bootstrap", "1", "complete_at_seam") }, "full_reference");
        internal NativeLogicalSourceHubAttachment Attach() => Hub.AttachSource(Request("source"), "bootstrap");
        internal NativeLogicalSourceHubAttachment Next(string id) => Hub.AttachSourceEpoch(id, Request("source"), "bootstrap");
        internal NativeLogicalCapturedProjection Capture(NativeLogicalSourceHubAttachment source)
        {
            var frame = new NativeLogicalPublicFrame(source.Subscription.StreamGeneration, new("runtime", "environment"),
                new("owner", "occurrence", "binding", null, null), "interactive", null,
                new("interaction", "fixture", "ready", null, "fixture", new(new JsonObject(), new JsonObject()), Array.Empty<PlayerEnvironmentInteractionCapability>()),
                Array.Empty<PlayerEnvironmentReferent>(), new("fair", "entered", false, "explicit"), Array.Empty<NativeLogicalLeaf>(),
                new("complete", Array.Empty<string>()));
            return Projector.Capture(frame, NativeLogicalProjector.ScopeFields, source.Subscription.ScopeId,
                DateTimeOffset.UnixEpoch, Now + Limits.RetentionMs, () => Now, Store);
        }
        internal bool Missing(NativeLogicalPublicationReservation reservation) => Hub.Complete(reservation,
            reservation.Subscriptions.Select(s => new NativeLogicalProjectionOutcome(s.ScopeId, null, "fixture_missing")).ToArray());
        public void Dispose() => Hub.Dispose();
    }

    [Fact]
    public void AtomicSourceAttachAndAnotherReadersBootstrapShareOneOriginalSourcePosition()
    {
        using var f = new Fixture(); var source = f.Attach();
        Assert.Equal("0", source.StartingBoundary.ReservedThrough);
        Assert.Equal("1", source.InitialReservation.PublicationIndex);
        var other = f.Hub.AttachWithInitialReservation(Fixture.Request("other"), "bootstrap");
        Assert.Equal("2", other.InitialReservation!.PublicationIndex);
        Assert.Equal(2, other.InitialReservation.Subscriptions.Count);
        Assert.Contains(other.InitialReservation.Subscriptions, s => s.SubscriptionId == source.Subscription.SubscriptionId);
        Assert.True(f.Missing(source.InitialReservation)); Assert.True(f.Missing(other.InitialReservation));
        var rows = f.Hub.SourceEvents(source.RegistrationId, source.Subscription.SubscriptionId, source.Subscription.StartingCursor);
        Assert.Equal(new[] { "1", "2" }, rows.Events.Select(e => e.Event.PublicationIndex));
        Assert.Equal("2", f.Hub.ReadSourceBoundary(source.RegistrationId, source.Subscription.SubscriptionId).CompletedThrough);
    }

    [Fact]
    public void LateOriginalGenerationEncodeRetainsItsExactMetadataAndBytesAfterOrdinaryInvalidation()
    {
        using var f = new Fixture(); var source = f.Attach(); var original = f.Capture(source);
        var bytes = f.Store.ExportFrozen(original.Capture.CaptureId).CopyCaptureBytes();
        f.Hub.ChangeGeneration();
        var initialSeal = f.Hub.ReadSourceBoundary(source.RegistrationId, source.Subscription.SubscriptionId);
        Assert.Equal("1", initialSeal.ReservedThrough); Assert.Equal("0", initialSeal.CompletedThrough);
        Assert.Throws<NativeLogicalException>(() => f.Hub.Events("source", source.Subscription.SubscriptionId,
            source.Subscription.ScopeId, source.Subscription.StartingCursor));
        Assert.True(f.Hub.Complete(source.InitialReservation,
            new[] { new NativeLogicalProjectionOutcome(source.Subscription.ScopeId, original.Capture, null) }));
        f.Store.ReleaseCapture(original.Capture.CaptureId);
        var batch = f.Hub.SourceEvents(source.RegistrationId, source.Subscription.SubscriptionId, source.Subscription.StartingCursor);
        var row = Assert.Single(batch.Events).Event;
        Assert.Equal(source.Subscription.StreamGeneration, row.StreamGeneration);
        Assert.Equal(original.Capture, row.PayloadReference);
        Assert.Equal(bytes, f.Store.ExportFrozen(row.CaptureRef!).CopyCaptureBytes());
        Assert.Equal("0", initialSeal.CompletedThrough); // Original seal object never mutates.
        Assert.Equal("1", f.Hub.ReadSourceBoundary(source.RegistrationId, source.Subscription.SubscriptionId).CompletedThrough);
        f.Hub.AcknowledgeSource(source.RegistrationId, source.Subscription.SubscriptionId, batch.NextCursor);
        Assert.Equal(0, f.Store.ChargedBytes);
        Assert.True(f.Hub.ReleaseSourceEpoch(source.RegistrationId, source.Subscription.SubscriptionId));
    }

    [Fact]
    public async Task RetiringTimeoutUsesItsOriginalDeadlineAndWakesTheSourceWithoutAnotherNativeOccurrence()
    {
        using var f = new Fixture(); var source = f.Attach(); f.Hub.ChangeGeneration();
        var wake = f.Hub.SourceProgressAsync(source.RegistrationId, CancellationToken.None);
        f.Now = 2001; f.Hub.Tick(); await wake.WaitAsync(TimeSpan.FromSeconds(1));
        var row = Assert.Single(f.Hub.SourceEvents(source.RegistrationId,
            source.Subscription.SubscriptionId, source.Subscription.StartingCursor).Events).Event;
        Assert.Equal("encoding_timeout", row.MissingReason);
        Assert.False(f.Missing(source.InitialReservation));
        Assert.Equal(source.Subscription.StreamGeneration, row.StreamGeneration);
    }

    [Fact]
    public void RetiringProjectionRejectsForeignScopeAndChangedOriginalMetadataBeforeCompletion()
    {
        using var f = new Fixture(); var source = f.Attach(); f.Hub.ChangeGeneration();
        Assert.Equal("invalid_projection", Assert.Throws<NativeLogicalException>(() => f.Hub.Complete(source.InitialReservation,
            new[] { new NativeLogicalProjectionOutcome("foreign_scope", null, "fixture_missing") })).Code);
        var forged = source.InitialReservation with { Subscriptions = new[] { source.Subscription with { ScopeId = "foreign_scope" } } };
        Assert.Equal("invalid_projection", Assert.Throws<NativeLogicalException>(() => f.Hub.Complete(forged,
            new[] { new NativeLogicalProjectionOutcome("foreign_scope", null, "fixture_missing") })).Code);
        Assert.Equal("invalid_projection", Assert.Throws<NativeLogicalException>(() => f.Missing(
            source.InitialReservation with { SourcePhase = "invented_phase" })).Code);
        Assert.Equal("0", f.Hub.ReadSourceBoundary(source.RegistrationId, source.Subscription.SubscriptionId).CompletedThrough);
        Assert.Empty(f.Hub.SourceEvents(source.RegistrationId, source.Subscription.SubscriptionId,
            source.Subscription.StartingCursor).Events);
        Assert.True(f.Missing(source.InitialReservation));
    }

    [Fact]
    public void ClosingAnAlreadyRetiringEpochWithoutSuccessorPreventsReopeningItsRegistration()
    {
        using var f = new Fixture(); var source = f.Attach(); f.Hub.ChangeGeneration();
        var seal = f.Hub.SealSource(source.RegistrationId, source.Subscription.SubscriptionId, close: true);
        Assert.Equal(source.Subscription.StreamGeneration, seal.StreamGeneration);
        Assert.Equal("source_closing", Assert.Throws<NativeLogicalException>(() => f.Next(source.RegistrationId)).Code);
        Assert.True(f.Missing(source.InitialReservation));
        var batch = f.Hub.SourceEvents(source.RegistrationId, source.Subscription.SubscriptionId, source.Subscription.StartingCursor);
        f.Hub.AcknowledgeSource(source.RegistrationId, source.Subscription.SubscriptionId, batch.NextCursor);
        Assert.True(f.Hub.ReleaseSourceEpoch(source.RegistrationId, source.Subscription.SubscriptionId));
        Assert.Equal("source_closing", Assert.Throws<NativeLogicalException>(() => f.Next(source.RegistrationId)).Code);
    }

    [Fact]
    public void ClosingAnOlderRetiringEpochCannotConcealAnAdmittingCurrentEpoch()
    {
        using var f = new Fixture(); var source = f.Attach(); f.Hub.ChangeGeneration(); var current = f.Next(source.RegistrationId);
        Assert.Equal("source_close_epoch_mismatch", Assert.Throws<NativeLogicalException>(() =>
            f.Hub.SealSource(source.RegistrationId, source.Subscription.SubscriptionId, close: true)).Code);
        Assert.Equal(current.Subscription.SubscriptionId,
            f.Hub.ReadSourceBoundary(source.RegistrationId, current.Subscription.SubscriptionId).SubscriptionId);
        f.Hub.SealSource(source.RegistrationId, current.Subscription.SubscriptionId, close: true);
        Assert.Equal("source_closing", Assert.Throws<NativeLogicalException>(() => f.Next(source.RegistrationId)).Code);
    }

    [Fact]
    public void CurrentCloseCanDrainAnOriginalEncodeWithoutCreatingAnotherGeneration()
    {
        using var f = new Fixture(); var source = f.Attach();
        string generation = f.Hub.StreamGeneration;
        var seal = f.Hub.SealSource(source.RegistrationId, source.Subscription.SubscriptionId, close: true);
        Assert.Equal("1", seal.ReservedThrough); Assert.Equal("0", seal.CompletedThrough);
        Assert.True(f.Missing(source.InitialReservation));
        Assert.Equal(generation, f.Hub.StreamGeneration);
        var batch = f.Hub.SourceEvents(source.RegistrationId, source.Subscription.SubscriptionId, source.Subscription.StartingCursor);
        Assert.Equal("fixture_missing", Assert.Single(batch.Events).Event.MissingReason);
        Assert.Equal("source_epoch_not_drained", Assert.Throws<NativeLogicalException>(() =>
            f.Hub.ReleaseSourceEpoch(source.RegistrationId, source.Subscription.SubscriptionId)).Code);
        f.Hub.AcknowledgeSource(source.RegistrationId, source.Subscription.SubscriptionId, batch.NextCursor);
        Assert.True(f.Hub.ReleaseSourceEpoch(source.RegistrationId, source.Subscription.SubscriptionId));
        Assert.Equal("source_closing", Assert.Throws<NativeLogicalException>(() => f.Next(source.RegistrationId)).Code);
    }

    [Fact]
    public void PendingPreAttachMetadataCannotBeSkippedToInventAnOriginalCompletedWatermark()
    {
        using var f = new Fixture();
        var earlier = f.Hub.AttachWithInitialReservation(Fixture.Request("earlier"), "bootstrap");
        var source = f.Attach(); Assert.Equal("1", source.StartingBoundary.ReservedThrough);
        f.Hub.ChangeGeneration(); Assert.True(f.Missing(source.InitialReservation));
        Assert.Equal("0", f.Hub.ReadSourceBoundary(source.RegistrationId, source.Subscription.SubscriptionId).CompletedThrough);
        Assert.True(f.Missing(earlier.InitialReservation!));
        Assert.Equal("2", f.Hub.ReadSourceBoundary(source.RegistrationId, source.Subscription.SubscriptionId).CompletedThrough);
        var row = Assert.Single(f.Hub.SourceEvents(source.RegistrationId,
            source.Subscription.SubscriptionId, source.Subscription.StartingCursor).Events).Event;
        Assert.Equal("2", row.PublicationIndex); // The earlier reader's position is outside this source attachment.
    }

    [Fact]
    public void ThirdRetirementFailsAccountingAndKeepsBothOlderViewsWithoutBlockingOrdinaryGenerationTurnover()
    {
        using var f = new Fixture(); var first = f.Attach(); f.Hub.ChangeGeneration();
        var second = f.Next(first.RegistrationId); f.Hub.ChangeGeneration();
        var third = f.Next(first.RegistrationId); string before = f.Hub.StreamGeneration;
        f.Hub.ChangeGeneration(); Assert.NotEqual(before, f.Hub.StreamGeneration);
        Assert.Equal("source_retiring_epoch_capacity", f.Hub.SourceFailure(first.RegistrationId));
        Assert.Equal("1", f.Hub.ReadSourceBoundary(first.RegistrationId, first.Subscription.SubscriptionId).ReservedThrough);
        Assert.Equal("1", f.Hub.ReadSourceBoundary(first.RegistrationId, second.Subscription.SubscriptionId).ReservedThrough);
        Assert.Equal("source_retiring_epoch_capacity", Assert.Throws<NativeLogicalException>(() => f.Next(first.RegistrationId)).Code);
        Assert.Throws<NativeLogicalException>(() => f.Hub.ReadSourceBoundary(first.RegistrationId, third.Subscription.SubscriptionId));
    }

    [Fact]
    public void TwoRetiringEpochsAndCurrentClosingEpochAllRetainTheirOriginalFiniteDrains()
    {
        using var f = new Fixture(); var first = f.Attach(); f.Hub.ChangeGeneration();
        var second = f.Next(first.RegistrationId); f.Hub.ChangeGeneration(); var current = f.Next(first.RegistrationId);
        f.Hub.SealSource(first.RegistrationId, current.Subscription.SubscriptionId, close: true);
        f.Hub.ChangeGeneration(); Assert.Null(f.Hub.SourceFailure(first.RegistrationId));
        foreach (var epoch in new[] { first, second, current })
        {
            Assert.True(f.Missing(epoch.InitialReservation));
            var batch = f.Hub.SourceEvents(first.RegistrationId, epoch.Subscription.SubscriptionId, epoch.Subscription.StartingCursor);
            Assert.Equal(epoch.Subscription.StreamGeneration, Assert.Single(batch.Events).Event.StreamGeneration);
            f.Hub.AcknowledgeSource(first.RegistrationId, epoch.Subscription.SubscriptionId, batch.NextCursor);
            Assert.True(f.Hub.ReleaseSourceEpoch(first.RegistrationId, epoch.Subscription.SubscriptionId));
        }
    }

    [Fact]
    public void DisposedSourceIdentityCannotDeliverToOrFailAReplacementRegistration()
    {
        using var f = new Fixture(); var old = f.Attach(); f.Hub.DisposeSource(old.RegistrationId);
        var current = f.Attach();
        Assert.Equal("source_attachment_expired", Assert.Throws<NativeLogicalException>(() =>
            f.Hub.SourceEvents(old.RegistrationId, old.Subscription.SubscriptionId, old.Subscription.StartingCursor)).Code);
        Assert.Null(f.Hub.SourceFailure(current.RegistrationId));
        Assert.True(f.Missing(current.InitialReservation));
    }

    [Fact]
    public async Task SourceCompletionBetweenEventsAndAwaitCannotLoseItsWakeup()
    {
        using var f = new Fixture(); var source = f.Attach();
        var before = f.Hub.SourceEvents(source.RegistrationId, source.Subscription.SubscriptionId, source.Subscription.StartingCursor);
        Assert.Empty(before.Events); Assert.True(f.Missing(source.InitialReservation));
        await f.Hub.WaitSourceProgressAsync(source.RegistrationId, source.Subscription.SubscriptionId,
            before.NextCursor, CancellationToken.None).WaitAsync(TimeSpan.FromSeconds(1));
        Assert.Single(f.Hub.SourceEvents(source.RegistrationId, source.Subscription.SubscriptionId, before.NextCursor).Events);
    }
}
