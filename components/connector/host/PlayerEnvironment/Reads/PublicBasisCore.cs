using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

internal static class PublicBasisAssembly
{
    // Called inside the owning synchronous native freeze, or on its already detached values.
    // Does not read the game, infer omitted facts, navigate, or grant action authority.
    internal static FrozenPublicObservation Freeze(JsonObject snapshot, IReadOnlyList<PublicBasisRead> reads,
        string? continuity, JsonSerializerOptions options)
    {
        if (snapshot["schema"]?.GetValue<string>() != PlayerEnvironmentContract.SnapshotSchema
            || snapshot["information_policy"]?["includes_hidden_information"]?.GetValue<bool>() != false)
            throw new SealedObservationException("invalid_public_basis", "A public source Snapshot is required.");
        if (reads.Count > PublicBasisContract.MaxReadInstances)
            throw new SealedObservationException("too_large", "Public Read inventory exceeds its bound.");
        var advertised = snapshot["reads"] as JsonArray
            ?? throw new SealedObservationException("invalid_public_basis", "The original Read inventory is missing.");
        var advertisedIds = advertised.Select(item => item?["read_id"]?.GetValue<string>()).ToArray();
        if (advertisedIds.Any(string.IsNullOrWhiteSpace)
            || advertisedIds.Distinct(StringComparer.Ordinal).Count() != advertised.Count
            || advertised.Count != reads.Count || reads.Select(r => r.ReadId).Distinct(StringComparer.Ordinal).Count() != reads.Count)
            throw new SealedObservationException("inconsistent_capture", "Each advertised Read must have one captured result.");
        string id = snapshot["snapshot_id"]!.GetValue<string>();
        var session = snapshot["session"]!.Deserialize<PlayerEnvironmentSessionReference>(options)!;
        var missing = new List<string>();
        foreach (JsonNode? original in advertised)
        {
            string readId = original!["read_id"]!.GetValue<string>();
            PublicBasisRead? captured = reads.SingleOrDefault(r => r.ReadId == readId);
            if (captured == null || captured.Kind != original["kind"]!.GetValue<string>())
                throw new SealedObservationException("inconsistent_capture", "A Read was omitted or changed kind.");
            if (captured.Response is not { } response)
            {
                if (string.IsNullOrWhiteSpace(captured.Error))
                    throw new SealedObservationException("inconsistent_capture", "A failed Read requires its original error.");
                missing.Add("read:" + readId + ":" + captured.Error); continue;
            }
            if (captured.Error != null || response["schema"]?.GetValue<string>() != PlayerEnvironmentContract.ReadSchema
                || response["read_id"]?.GetValue<string>() != readId || response["kind"]?.GetValue<string>() != captured.Kind
                || response["expected_snapshot_id"]?.GetValue<string>() != id || response["observed_snapshot_id"]?.GetValue<string>() != id
                || !JsonNode.DeepEquals(response["session"], snapshot["session"])
                || !JsonNode.DeepEquals(response["information_policy"], snapshot["information_policy"])
                || !JsonNode.DeepEquals(response["target_referent_id"], original["target_referent_id"])
                || !JsonNode.DeepEquals(response["content_schema"], original["content_schema"])
                || !JsonNode.DeepEquals(response["ordering_semantics"], original["ordering_semantics"])
                || !JsonNode.DeepEquals(response["visibility_basis"], original["visibility_basis"])
                || !JsonNode.DeepEquals(response["completeness"]?["hidden_by_policy"], original["hidden_by_policy"]))
                throw new SealedObservationException("inconsistent_capture", "A Read does not belong to this exact public source.");
            if (response["completeness"]?["status"]?.GetValue<string>() != "complete"
                || response["completeness"]?["missing"] is not JsonArray { Count: 0 }) missing.Add("read:" + readId + ":partial");
        }
        if (snapshot["completeness"]?["status"]?.GetValue<string>() != "complete"
            || snapshot["completeness"]?["missing"] is not JsonArray { Count: 0 }) missing.Add("snapshot:partial");
        // These are original bytes for the existing retention/storage owners. No P1 state is embedded.
        var observation = new PublicBasisObservation(PublicBasisContract.Schema, PublicBasisContract.Revision,
            PublicBasisContract.Scope, missing.Count == 0 ? "complete" : "partial", snapshot, reads, missing);
        using var stream = new BoundedObservationStream(SealedObservationContract.MaxCapsuleBytes);
        JsonSerializer.Serialize(stream, observation, options);
        return new(stream.ToArray(), id, session, continuity,
            DateTimeOffset.UtcNow, SealedObservationProfile.PublicBasis);
    }
}
