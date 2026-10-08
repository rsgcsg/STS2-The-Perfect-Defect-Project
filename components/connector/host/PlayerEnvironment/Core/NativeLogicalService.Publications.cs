using System;
using System.Collections.Generic;
using System.Linq;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

internal static partial class PlayerEnvironmentService
{
    // Unified composition is in the same assembly. Passive consumers cannot assert native hook registration.
    internal static void InstallNativeLogicalPublicationProfile(string profileId,
        string definitionSha256, IReadOnlyList<NativeLogicalSeamCoverage> confirmedCoverage) =>
        NativeLogical.InstallPublicationProfile(profileId, definitionSha256, confirmedCoverage);
}

internal sealed partial class NativeLogicalService
{
    internal void InstallPublicationProfile(string profileId, string definitionSha256,
        IReadOnlyList<NativeLogicalSeamCoverage> confirmedCoverage)
    {
        AssertMainThread();
        // Initialize installed the intrinsic bootstrap/executor owner and subscribed to the native ready producer.
        // The composition confirms exact hook registrations only after they succeeded, including Foundation ready hooks.
        var canonical = NativeLogicalPublicationProfile.ValidateConfirmation(profileId, definitionSha256, confirmedCoverage);
        Hub.InstallPublicationProfile(profileId, definitionSha256, canonical);
    }

    // A failed typed native callback still reserves its original source occurrence. Never recapture a stale full frame.
    internal void PublishMissing(string seam, string phase, string reason)
    {
        try
        {
            SynchronizeRun();
            NativeLogicalWire.Text(reason, Limits.MaxMetadataFieldBytes);
            var reservation = Hub.Reserve(seam, phase, "observation");
            Hub.Complete(reservation, reservation.Subscriptions
                .Select(s => new NativeLogicalProjectionOutcome(s.ScopeId, null, reason)).ToArray());
        }
        catch (Exception) { /* Native observer failure cannot alter gameplay or delivery. */ }
    }
}
