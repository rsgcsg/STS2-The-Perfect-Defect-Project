using System;
using System.Collections.Concurrent;
using System.Linq;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector;

public sealed class NativeLogicalPublicationProfileTests
{
    private sealed class Fixture : IDisposable
    {
        internal int Captures;
        internal readonly NativeLogicalService Owner;
        internal Fixture()
        {
            Owner = new(() => { Captures++; throw new InvalidOperationException("No native capture belongs to this missing observation."); },
                () => "run", new object(), new ConcurrentDictionary<string, string>(), executionAllowed: () => true);
            Owner.Initialize();
        }
        internal void Install() => Owner.InstallPublicationProfile(NativeLogicalPublicationProfile.ProfileId,
            NativeLogicalPublicationProfile.DefinitionSha256, NativeLogicalPublicationProfile.RequiredCoverage.Reverse().ToArray());
        internal NativeLogicalSubscription Attach(string reader) => Owner.Hub.Attach(new(reader,
            NativeLogicalProjector.ScopeFields, new[] { NativeLogicalService.Coverage[0] }, "scoped")).Subscription!;
        public void Dispose() => Owner.Dispose();
    }

    [Fact]
    public void CanonicalEmbeddedTargetHasExactThirteenCompleteSeamsAndScope()
    {
        var target = NativeLogicalPublicationProfile.RequiredCoverage;
        Assert.Equal(13, target.Count);
        Assert.All(target, seam => { Assert.Equal("1", seam.Version); Assert.Equal("complete_at_seam", seam.Coverage); });
        Assert.Equal(13, target.Select(s => s.SourceSeam).Distinct().Count());
        Assert.Equal("connector_initial_observation", target[0].SourceSeam);
        Assert.Equal("native_reward_input_availability", target[^1].SourceSeam);
    }

    [Fact]
    public void TargetAloneDoesNotUpgradeActualRegistrationOrAdmitFullReferenceProfile()
    {
        using var f = new Fixture();
        Assert.Null(f.Owner.Hub.ReadDeclaration().PublicationProfileId);
        var reply = f.Owner.Hub.Attach(new("reader", NativeLogicalProjector.ScopeFields,
            NativeLogicalPublicationProfile.RequiredCoverage, "full_reference"));
        Assert.Equal("coverage_insufficient", reply.Status); Assert.Null(reply.Subscription);
        var partial = NativeLogicalPublicationProfile.RequiredCoverage.Take(12).ToArray();
        Assert.Equal("publication_registration_incomplete", Assert.Throws<NativeLogicalException>(() =>
            f.Owner.InstallPublicationProfile(NativeLogicalPublicationProfile.ProfileId,
                NativeLogicalPublicationProfile.DefinitionSha256, partial)).Code);
        Assert.Null(f.Owner.Hub.ReadDeclaration().PublicationProfileId);
        Assert.Equal("sampled", f.Owner.Hub.ReadDeclaration().Coverage.Single(s => s.SourceSeam == "native_owner_ready").Coverage);
    }

    [Fact]
    public void ConfirmationIsAnExactSetAndInstallsCanonicalOrderInTheSingleHub()
    {
        using var f = new Fixture(); f.Install();
        var actual = f.Owner.Hub.ReadDeclaration();
        Assert.Equal(NativeLogicalPublicationProfile.ProfileId, actual.PublicationProfileId);
        Assert.Equal(NativeLogicalPublicationProfile.DefinitionSha256, actual.PublicationProfileDefinitionSha256);
        Assert.Equal(NativeLogicalPublicationProfile.RequiredCoverage, actual.Coverage.Take(13));
        Assert.Equal("unsupported", actual.Coverage.Single(s => s.SourceSeam == "other_native_exposures").Coverage);
        var reply = f.Owner.Hub.Attach(new("reader", NativeLogicalProjector.ScopeFields,
            NativeLogicalPublicationProfile.RequiredCoverage, "full_reference"));
        Assert.Equal("attached", reply.Status);
        Assert.Equal(NativeLogicalPublicationProfile.RequiredCoverage, reply.Subscription!.Coverage);
        Assert.Equal(actual.StreamGeneration, reply.Subscription.StreamGeneration);
    }

    [Fact]
    public void WrongDefinitionDuplicateSeamOrVersionCannotInstallTheTarget()
    {
        using var f = new Fixture(); var target = NativeLogicalPublicationProfile.RequiredCoverage;
        Assert.Equal("publication_profile_mismatch", Assert.Throws<NativeLogicalException>(() =>
            f.Owner.InstallPublicationProfile(NativeLogicalPublicationProfile.ProfileId, new string('0', 64), target)).Code);
        var duplicate = target.ToArray(); duplicate[12] = duplicate[0];
        Assert.Equal("publication_registration_incomplete", Assert.Throws<NativeLogicalException>(() =>
            f.Owner.InstallPublicationProfile(NativeLogicalPublicationProfile.ProfileId,
                NativeLogicalPublicationProfile.DefinitionSha256, duplicate)).Code);
        var changed = target.ToArray(); changed[0] = changed[0] with { Version = "2" };
        Assert.Equal("publication_registration_incomplete", Assert.Throws<NativeLogicalException>(() =>
            f.Owner.InstallPublicationProfile(NativeLogicalPublicationProfile.ProfileId,
                NativeLogicalPublicationProfile.DefinitionSha256, changed)).Code);
        Assert.Null(f.Owner.Hub.ReadDeclaration().PublicationProfileId);
    }

    [Fact]
    public void FailedNativeCallbackPublishesOriginalMissingOccurrenceWithoutCapturingStaleState()
    {
        using var f = new Fixture(); f.Install(); var sub = f.Attach("reader");
        f.Owner.PublishMissing("native_reward_catalog", "RefreshOptions_failed", "native_callback_failed");
        var batch = f.Owner.Hub.Events("reader", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor);
        var observed = Assert.Single(batch.Events).Event;
        Assert.Equal("1", observed.PublicationIndex); Assert.Equal("1", observed.SourceIndex);
        Assert.Equal("native_reward_catalog", observed.SourceSeam);
        Assert.Equal("RefreshOptions_failed", observed.SourcePhase);
        Assert.Equal("native_callback_failed", observed.MissingReason);
        Assert.Equal("complete_at_seam", observed.Coverage);
        Assert.Null(observed.CaptureRef); Assert.Null(observed.PayloadReference);
        Assert.Equal(0, f.Captures);
    }

    [Fact]
    public void InstallationNeverRetroactivelyUpgradesPreviouslyAcceptedScopeCoverage()
    {
        using var f = new Fixture(); var old = f.Attach("old-reader");
        var original = f.Owner.Hub.ReadDeclaration(); f.Install(); var current = f.Attach("new-reader");
        f.Owner.PublishMissing("native_owner_ready", "native_failed", "native_callback_failed");
        f.Owner.PublishMissing("native_reward_catalog", "native_failed", "native_callback_failed");
        var oldRows = f.Owner.Hub.Events("old-reader", old.SubscriptionId, old.ScopeId, old.StartingCursor).Events;
        var newRows = f.Owner.Hub.Events("new-reader", current.SubscriptionId, current.ScopeId, current.StartingCursor).Events;
        Assert.Equal("sampled", oldRows[0].Event.Coverage);
        Assert.Equal("unsupported", oldRows[1].Event.Coverage);
        Assert.All(newRows, row => Assert.Equal("complete_at_seam", row.Event.Coverage));
        Assert.Equal("sampled", original.Coverage.Single(s => s.SourceSeam == "native_owner_ready").Coverage);
        Assert.Null(original.PublicationProfileId);
    }
}
