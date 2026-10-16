using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

/// <summary>Scope is synchronous and thread-confined. A nested capture/build or
/// known native input prefix invalidates the whole acquisition, even if the
/// owner later returns to the same object. No retry and no input authority.</summary>
internal sealed class P1CaptureGuard : IDisposable
{
    [ThreadStatic] private static P1CaptureGuard? active;
    private readonly P1CaptureGuard? parent;
    private readonly int thread = Environment.CurrentManagedThreadId;
    private int snapshotBuilds;
    private bool invalid, disposed;

    internal P1CaptureGuard()
    {
        parent = active;
        if (parent != null)
        {
            InvalidateActive();
            throw new SealedObservationException("inconsistent_capture", "A synchronous public capture reentered; no nested extraction was started.");
        }
        active = this;
    }
    internal static void ObserveSnapshotBuild()
    {
        if (active is { } scope && ++scope.snapshotBuilds != 1)
        {
            InvalidateActive();
            throw new SealedObservationException("inconsistent_capture", "A synchronous public capture attempted a second Snapshot build.");
        }
    }
    internal static void InvalidateActive()
    {
        for (P1CaptureGuard? scope = active; scope != null; scope = scope.parent) scope.invalid = true;
    }
    internal void RequireStable(bool ownerAndContextUnchanged)
    {
        if (disposed || thread != Environment.CurrentManagedThreadId || !ReferenceEquals(active, this)
            || invalid || snapshotBuilds != 1 || !ownerAndContextUnchanged)
            throw new SealedObservationException("inconsistent_capture",
                "The synchronous capture reentered, or its native owner/context changed; no complete capture was published.");
    }
    public void Dispose()
    {
        if (disposed) return;
        disposed = true;
        if (ReferenceEquals(active, this)) active = parent;
        else InvalidateActive();
    }
}

internal static class P1CaptureAssembly
{
    internal static readonly string[] ScopeRequirements =
    {
        "ordinary_combat_scope", "current_public_base", "current_parent_combat_context",
        "legacy_native_action_relation", "run_deck", "combat_piles", "surface_card_instances"
    };
    // Full P1 requirements, not optional omissions from a purported full offer.
    internal static readonly string[] FullP1Missing =
    {
        "current_act_map", "p1_semantic_catalog", "p1_protocol_commitments",
        "complete_public_detail_closure", "native_capture_noninterference_qualification"
    };

    // Invoke the fixed typed formatter synchronously, then discard it. A legacy
    // generic/fallback description is never promoted to a current display fact.
    internal static P1CapturedRead CurrentCardDetail(string instanceId, JsonObject publicCard,
        string entityId, string displayContext, string? parentRead, Func<string?>? currentDescription)
    {
        JsonObject content = (JsonObject)publicCard.DeepClone();
        content["description"] = null;
        var missing = new List<string>();
        if (displayContext is not ("hand" or "deck" or "draw" or "discard" or "exhaust"))
            missing.Add("unqualified_card_display_context");
        else if (currentDescription == null) missing.Add("current_card_native_binding");
        else
        {
            try
            {
                string? description = currentDescription();
                if (string.IsNullOrWhiteSpace(description)) missing.Add("current_display_description");
                else
                {
                    content["description"] = description;
                    if (description != publicCard["description"]?.GetValue<string>())
                        missing.Add("current_display_description_changed_during_capture");
                }
            }
            catch { missing.Add("current_display_description"); }
        }
        if (string.IsNullOrWhiteSpace(content["name"]?.GetValue<string>())) missing.Add("current_display_name");
        if (content["existing_enchantment"] is JsonObject enchantment
            && (string.IsNullOrWhiteSpace(enchantment["name"]?.GetValue<string>())
                || string.IsNullOrWhiteSpace(enchantment["description"]?.GetValue<string>())))
            missing.Add("current_enchantment_description");
        return new(instanceId, "surface_card", null, entityId, displayContext, parentRead,
            missing.Count == 0 ? "available" : "unsupported",
            "sts2.player-environment/p1-current-card-display-c1-1", content, missing.ToArray());
    }

    internal static FrozenPublicObservation Freeze(
        JsonObject nativeBasis, IReadOnlyList<P1CapturedRead> reads,
        IReadOnlyList<P1CaptureCoverage> coverage, bool supportedScene,
        string? gameContinuityId, JsonSerializerOptions options, DateTimeOffset? timestamp = null)
    {
        if (reads.Count > P1CaptureContract.MaxReadInstances)
            throw new SealedObservationException("too_large", "The capture exceeds the announced Read-instance limit.");
        if (reads.Select(read => read.InstanceId).Distinct(StringComparer.Ordinal).Count() != reads.Count)
            throw new SealedObservationException("inconsistent_capture", "Read instance identities are not unique.");
        var snapshotId = nativeBasis["snapshot_id"]?.GetValue<string>()
            ?? throw new SealedObservationException("capture_failed", "The public native basis has no snapshot identity.");
        JsonNode session = nativeBasis["session"]
            ?? throw new SealedObservationException("capture_failed", "The public native basis has no session.");
        var reference = new PlayerEnvironmentSessionReference(
            session["runtime_instance_id"]!.GetValue<string>(),
            session["environment_fingerprint"]!.GetValue<string>());
        if (nativeBasis["schema"]?.GetValue<string>() != PlayerEnvironmentContract.SnapshotSchema
            || nativeBasis["information_policy"]?["includes_hidden_information"]?.GetValue<bool>() != false)
            throw new SealedObservationException("unsupported_input_profile", "The native basis must retain the public legacy schema.");

        // Each context occurrence is kept. Neither kind nor entity definition is
        // a uniqueness key; the same card in deck/hand has two display instances.
        P1CapturedRead[] detachedReads = reads.Select(read => read with
        {
            Content = read.Content?.DeepClone(), Missing = read.Missing.ToArray()
        }).ToArray();
        P1CaptureCoverage[] detachedCoverage = coverage.Select(item => item with
        {
            ReadInstanceIds = item.ReadInstanceIds.ToArray(), Missing = item.Missing.ToArray()
        }).Concat(ScopeRequirements.Where(required => !coverage.Any(item => item.RequirementId == required && item.Required))
            .Select(required => new P1CaptureCoverage(required, true, "unsupported", Array.Empty<string>(),
                new[] { "required_capture_scope_not_evidenced" })))
        .Concat(FullP1Missing.Select(missing => new P1CaptureCoverage(missing, true,
            "unsupported", Array.Empty<string>(), new[] { missing }))).ToArray();
        var ids = detachedReads.Select(read => read.InstanceId).ToHashSet(StringComparer.Ordinal);
        if (detachedCoverage.SelectMany(item => item.ReadInstanceIds).Any(id => !ids.Contains(id))
            || detachedReads.Any(read => read.ParentReadInstanceId != null && !ids.Contains(read.ParentReadInstanceId)))
            throw new SealedObservationException("inconsistent_capture", "Coverage does not bind the captured Read instances.");

        bool baseComplete = nativeBasis["persistent"] != null
            && nativeBasis["completeness"]?["status"]?.GetValue<string>() == "complete";
        if (!baseComplete)
            for (int index = 0; index < detachedCoverage.Length; index++)
                if (detachedCoverage[index].RequirementId == "current_public_base" && detachedCoverage[index].Availability == "available")
                    detachedCoverage[index] = detachedCoverage[index] with
                    {
                        Availability = "unknown", Missing = detachedCoverage[index].Missing
                            .Append("native_basis_incomplete").Distinct(StringComparer.Ordinal).ToArray()
                    };

        string scopeStatus = !supportedScene ? "unsupported"
            : detachedCoverage.Where(item => !FullP1Missing.Contains(item.RequirementId, StringComparer.Ordinal))
                .Any(item => item.Required && (item.Availability != "available" || item.Missing.Count != 0))
                || detachedReads.Any(read => read.Availability != "available" || read.Missing.Count != 0)
                ? "partial" : "complete";
        DateTimeOffset capturedAt = timestamp ?? DateTimeOffset.UtcNow;
        var observation = new P1CapturedObservation(P1CaptureContract.ObservationSchema,
            P1CaptureContract.Profile, P1CaptureContract.Revision, P1CaptureContract.Scope,
            scopeStatus, "unsupported", false, false, P1CaptureContract.NativeCatalogSemantics,
            "sampled_current_capture_not_native_occurrence", snapshotId, capturedAt, reference,
            (JsonObject)nativeBasis.DeepClone(), detachedReads, detachedCoverage);
        using var stream = new BoundedObservationStream(SealedObservationContract.MaxCapsuleBytes);
        JsonSerializer.Serialize(stream, observation, options);
        return new(stream.ToArray(), snapshotId, reference, gameContinuityId, capturedAt,
            SealedObservationProfile.P1Capture);
    }
}
