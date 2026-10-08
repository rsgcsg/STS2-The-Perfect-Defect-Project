using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector.Tests;

public sealed class NativeLogicalPublicationProfileCoreTests
{
    [Fact]
    public void CanonicalProfileCannotBeInstalledFromPartialDuplicateOrWrongIdentityConfirmation()
    {
        var target = NativeLogicalPublicationProfile.RequiredCoverage;
        Assert.Equal(13, target.Count);
        Assert.Equal("publication_profile_mismatch", Assert.Throws<NativeLogicalException>(() =>
            NativeLogicalPublicationProfile.ValidateConfirmation("other-profile", NativeLogicalPublicationProfile.DefinitionSha256, target)).Code);
        Assert.Equal("publication_registration_incomplete", Assert.Throws<NativeLogicalException>(() =>
            NativeLogicalPublicationProfile.ValidateConfirmation(NativeLogicalPublicationProfile.ProfileId,
                NativeLogicalPublicationProfile.DefinitionSha256, target.Take(12).ToArray())).Code);
        var duplicate = target.ToArray(); duplicate[12] = duplicate[0];
        Assert.Equal("publication_registration_incomplete", Assert.Throws<NativeLogicalException>(() =>
            NativeLogicalPublicationProfile.ValidateConfirmation(NativeLogicalPublicationProfile.ProfileId,
                NativeLogicalPublicationProfile.DefinitionSha256, duplicate)).Code);
    }

    [Fact]
    public void ActualDeclarationOnlyChangesAfterExactConfirmationAndKeepsCanonicalTargetOrder()
    {
        var store = new NativeLogicalCaptureStore();
        using var hub = new NativeLogicalPublicationHub(store, new[]
        {
            new NativeLogicalSeamCoverage("connector_initial_observation", "1", "complete_at_seam"),
            new NativeLogicalSeamCoverage("native_owner_ready", "1", "sampled")
        });
        var before = hub.ReadDeclaration();
        Assert.Null(before.PublicationProfileId);
        Assert.Equal("coverage_insufficient", hub.Attach(new("old", NativeLogicalProjector.ScopeFields,
            NativeLogicalPublicationProfile.RequiredCoverage, "full_reference")).Status);
        hub.InstallPublicationProfile(NativeLogicalPublicationProfile.ProfileId,
            NativeLogicalPublicationProfile.DefinitionSha256, NativeLogicalPublicationProfile.RequiredCoverage.Reverse().ToArray());
        var after = hub.ReadDeclaration();
        Assert.Equal(before.StreamGeneration, after.StreamGeneration);
        Assert.Equal(NativeLogicalPublicationProfile.RequiredCoverage, after.Coverage);
        Assert.Equal("sampled", before.Coverage.Single(s => s.SourceSeam == "native_owner_ready").Coverage);
        Assert.Equal("attached", hub.Attach(new("new", NativeLogicalProjector.ScopeFields,
            NativeLogicalPublicationProfile.RequiredCoverage, "full_reference")).Status);
    }

    [Fact]
    public void AlreadyAcceptedScopeKeepsItsOriginalCoverageAfterCompositionInstallation()
    {
        var store = new NativeLogicalCaptureStore();
        using var hub = new NativeLogicalPublicationHub(store, new[] { new NativeLogicalSeamCoverage("native_owner_ready", "1", "sampled") });
        var old = hub.Attach(new("old", NativeLogicalProjector.ScopeFields,
            new[] { new NativeLogicalSeamCoverage("native_owner_ready", "1", "sampled") }, "scoped")).Subscription!;
        hub.InstallPublicationProfile(NativeLogicalPublicationProfile.ProfileId,
            NativeLogicalPublicationProfile.DefinitionSha256, NativeLogicalPublicationProfile.RequiredCoverage);
        var current = hub.Attach(new("new", NativeLogicalProjector.ScopeFields,
            NativeLogicalPublicationProfile.RequiredCoverage, "full_reference")).Subscription!;
        var reserved = hub.Reserve("native_owner_ready", "native_failed", "observation");
        Assert.True(hub.Complete(reserved, reserved.Subscriptions
            .Select(s => new NativeLogicalProjectionOutcome(s.ScopeId, null, "native_callback_failed")).ToArray()));
        Assert.Equal("sampled", Assert.Single(hub.Events("old", old.SubscriptionId, old.ScopeId, old.StartingCursor).Events).Event.Coverage);
        Assert.Equal("complete_at_seam", Assert.Single(hub.Events("new", current.SubscriptionId, current.ScopeId, current.StartingCursor).Events).Event.Coverage);
    }
}
