using System.Text.Json;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2HumanAnnotator.Core;

namespace STS2PlatformLiveUi;

public sealed record PlatformLiveScore(
    string Name,
    double? Value,
    string DisplayValue,
    string Source);

public sealed record PlatformSelectedItem(
    string ReferentId,
    string Role,
    string Kind,
    string Label);

public sealed record PlatformReadView(
    string ReadId,
    string Kind,
    string Status);

public sealed record PlatformReceiptView(
    string Status,
    string Detail,
    string? RequestId);

public sealed record PlatformArtifactIdentity(
    string Product,
    string Version,
    string? SourceRevision,
    string? ModuleVersionId,
    string? ArtifactSha256);

public sealed record PlatformExactIdentity(
    PlatformArtifactIdentity? Game,
    PlatformArtifactIdentity? Connector,
    PlatformArtifactIdentity? Annotator,
    PlatformArtifactIdentity LiveUi,
    string? RuntimeInstanceId,
    string? EnvironmentFingerprint,
    string? HostKind,
    string? ModsetStatus,
    string? ModsetFingerprint,
    IReadOnlyList<string> LoadedModIds);

public sealed record PlatformLiveStatus(
    string Schema,
    DateTimeOffset ObservedAt,
    string TransportStatus,
    string? TransportDetail,
    string PolicyRuntimeTransportStatus,
    string? PolicyRuntimeTransportDetail,
    IPlatformRuntimeStatus? PolicyRuntime,
    PlayerEnvironmentCapabilitiesResponse? Capabilities,
    PlayerEnvironmentSnapshot? Snapshot,
    PlayerEnvironmentControlSnapshot? Controller,
    RecordingApplicationStatus Recording,
    IReadOnlyList<PlatformLiveScore> Scores,
    IReadOnlyList<PlatformSelectedItem> Selected,
    PlatformReceiptView Receipt,
    IReadOnlyList<PlatformReadView> Reads,
    IReadOnlyList<string> Invalidations,
    PlatformExactIdentity ExactIdentity,
    IReadOnlyList<string> Errors)
{
    public const string CurrentSchema = "sts2.ai-platform/live-status-1";
}

public static class PlatformLiveStatusProjection
{
    public static PlatformLiveStatus Build(
        IPlatformRuntimeStatus? policyRuntime,
        PlayerEnvironmentCapabilitiesResponse? capabilities,
        PlayerEnvironmentSnapshot? snapshot,
        PlayerEnvironmentControlSnapshot? controller,
        RecordingApplicationStatus recording,
        string transportStatus,
        string? transportDetail,
        string policyRuntimeTransportStatus,
        string? policyRuntimeTransportDetail,
        IReadOnlyList<string> errors)
    {
        if (capabilities is null || snapshot is null || controller is null)
        {
            if (capabilities is not null || snapshot is not null || controller is not null)
                throw new JsonException("Connector status merge requires a complete coherent response set.");
        }
        else
        {
            EnsureConnectorCoherence(capabilities, snapshot, controller);
        }

        return new PlatformLiveStatus(
            PlatformLiveStatus.CurrentSchema,
            DateTimeOffset.UtcNow,
            transportStatus,
            transportDetail,
            policyRuntimeTransportStatus,
            policyRuntimeTransportDetail,
            policyRuntime,
            capabilities,
            snapshot,
            controller,
            recording,
            ReadScores(policyRuntime),
            ReadSelected(policyRuntime),
            ReadReceipt(policyRuntime),
            ReadReads(snapshot, policyRuntime),
            ReadInvalidations(policyRuntime),
            BuildIdentity(capabilities, policyRuntime, recording),
            errors);
    }

    public static void EnsureConnectorCoherence(
        PlayerEnvironmentCapabilitiesResponse capabilities,
        PlayerEnvironmentSnapshot snapshot,
        PlayerEnvironmentControlSnapshot controller)
    {
        if (capabilities.Host is null
            || snapshot.Session is null
            || capabilities.ProtocolVersion != PlayerEnvironmentContract.ProtocolVersion
            || snapshot.ProtocolVersion != PlayerEnvironmentContract.ProtocolVersion
            || controller.ProtocolVersion != PlayerEnvironmentContract.ProtocolVersion
            || capabilities.SnapshotSchema != PlayerEnvironmentContract.SnapshotSchema
            || capabilities.ControlSchema != PlayerEnvironmentContract.ControlSchema
            || snapshot.Schema != PlayerEnvironmentContract.SnapshotSchema
            || controller.Schema != PlayerEnvironmentContract.ControlSchema)
        {
            throw new JsonException("Connector protocol or schema identity is incoherent.");
        }

        string capabilityRuntime = capabilities.Host.RuntimeInstanceId;
        string snapshotRuntime = snapshot.Session.RuntimeInstanceId;
        string controllerRuntime = controller.RuntimeInstanceId;
        string capabilityEnvironment = capabilities.EnvironmentFingerprint;
        string snapshotEnvironment = snapshot.Session.EnvironmentFingerprint;
        if (string.IsNullOrWhiteSpace(capabilityRuntime)
            || string.IsNullOrWhiteSpace(snapshotRuntime)
            || string.IsNullOrWhiteSpace(controllerRuntime)
            || string.IsNullOrWhiteSpace(capabilityEnvironment)
            || string.IsNullOrWhiteSpace(snapshotEnvironment)
            || !string.Equals(capabilityRuntime, snapshotRuntime, StringComparison.Ordinal)
            || !string.Equals(capabilityRuntime, controllerRuntime, StringComparison.Ordinal)
            || !string.Equals(capabilityEnvironment, snapshotEnvironment, StringComparison.Ordinal))
        {
            throw new JsonException("Connector runtime/environment coherence check failed.");
        }
    }

    private static PlatformExactIdentity BuildIdentity(
        PlayerEnvironmentCapabilitiesResponse? capabilities,
        IPlatformRuntimeStatus? policyRuntime,
        RecordingApplicationStatus recording)
    {
        PlatformArtifactIdentity? connector = capabilities?.Host.Implementation is
            { } implementation
            ? new PlatformArtifactIdentity(
                "STS2_MCP",
                capabilities.Host.Version,
                implementation.SourceRevision,
                implementation.ModuleVersionId,
                implementation.ArtifactSha256)
            : null;
        PlatformArtifactIdentity? game = capabilities?.Game is { } gameIdentity
            ? new PlatformArtifactIdentity(
                "sts2",
                gameIdentity.Version ?? "unknown",
                gameIdentity.Commit,
                null,
                gameIdentity.MainAssemblyHash?.ToString())
            : null;
        PolicyRuntimeEnvironmentStatus? environment = policyRuntime?.Environment;
        connector ??= environment == null
            ? null
            : new PlatformArtifactIdentity(
                "STS2_MCP",
                environment.ConnectorVersion,
                environment.ConnectorSourceRevision,
                environment.ConnectorModuleVersionId,
                environment.ConnectorArtifactSha256);
        game ??= environment == null
            ? null
            : new PlatformArtifactIdentity(
                "sts2",
                environment.GameVersion ?? "unknown",
                environment.GameCommit,
                null,
                null);
        ExactArtifactIdentity? annotatorIdentity = recording.Environment?.Annotator;
        PlatformArtifactIdentity? annotator = annotatorIdentity == null
            ? null
            : new PlatformArtifactIdentity(
                annotatorIdentity.Product,
                annotatorIdentity.Version,
                annotatorIdentity.SourceRevision,
                annotatorIdentity.ModuleVersionId,
                annotatorIdentity.Sha256);
        return new PlatformExactIdentity(
            game,
            connector,
            annotator,
            PlatformLiveUiMod.CurrentArtifactIdentity(),
            capabilities?.Host.RuntimeInstanceId ?? environment?.RuntimeInstanceId,
            capabilities?.EnvironmentFingerprint ?? environment?.EnvironmentFingerprint,
            capabilities?.Host.HostKind ?? environment?.HostKind,
            capabilities?.Game.Modset.Status ?? environment?.ModsetStatus,
            capabilities?.Game.Modset.Fingerprint ?? environment?.ModsetFingerprint,
            capabilities?.Game.Modset.LoadedModIds ?? environment?.LoadedModIds ?? Array.Empty<string>());
    }

    private static IReadOnlyList<PlatformLiveScore> ReadScores(IPlatformRuntimeStatus? policyRuntime)
    {
        if (policyRuntime is NativeAgentRuntimeStatus native)
        {
            if (native.LastDirective is not { } directive || !directive.TryGetProperty("scores", out JsonElement scores)
                || scores.ValueKind != JsonValueKind.Object || !scores.TryGetProperty("values", out JsonElement values))
                return Array.Empty<PlatformLiveScore>();
            return values.EnumerateArray().Select((value, index) => new PlatformLiveScore(
                $"catalog[{index}]", value.GetDouble(), value.GetDouble().ToString("0.##", System.Globalization.CultureInfo.InvariantCulture),
                "Agent Session status.last_directive.scores.values")).ToArray();
        }
        PolicyRuntimeDecisionStatus? decision = (policyRuntime as PolicyRuntimeStatus)?.LastDecision;
        if (decision == null)
        {
            return new[]
            {
                new PlatformLiveScore(
                    "scores",
                    null,
                    "unavailable",
                    "Policy Runtime status has no last_decision")
            };
        }

        return decision.Scores
            .Select((score, index) => new PlatformLiveScore(
                $"candidate[{index}]",
                score,
                score.ToString("0.##", System.Globalization.CultureInfo.InvariantCulture),
                "Policy Runtime status.last_decision.scores"))
            .ToArray();
    }

    private static IReadOnlyList<PlatformSelectedItem> ReadSelected(IPlatformRuntimeStatus? policyRuntime)
    {
        if (policyRuntime is NativeAgentRuntimeStatus native)
        {
            if (native.LastDirective is not { } directive || !directive.TryGetProperty("selection", out JsonElement selection)
                || !selection.TryGetProperty("kind", out JsonElement kind) || kind.GetString() != "handle"
                || !selection.TryGetProperty("action_id", out JsonElement selected))
                return Array.Empty<PlatformSelectedItem>();
            string actionId = selected.GetString() ?? throw new JsonException("Native selected action ID absent.");
            return [new PlatformSelectedItem(actionId, "agent-selected", "native-action-handle", actionId)];
        }
        PolicyRuntimeDecisionStatus? decision = (policyRuntime as PolicyRuntimeStatus)?.LastDecision;
        if (decision?.SelectedIndex is not int selectedIndex)
            return Array.Empty<PlatformSelectedItem>();

        return new[]
        {
            new PlatformSelectedItem(
                decision.BoundActionId ?? $"candidate[{selectedIndex}]",
                "policy-selected",
                "runtime-decision",
                decision.BoundActionLabel ?? $"candidate[{selectedIndex}]")
        };
    }

    private static PlatformReceiptView ReadReceipt(IPlatformRuntimeStatus? policyRuntime)
    {
        if (policyRuntime is NativeAgentRuntimeStatus native)
        {
            NativeAgentResultStatus? result = native.LastResult;
            return result is null
                ? new PlatformReceiptView("unavailable", "Agent Session has no original native result.", null)
                : new PlatformReceiptView(result.Status == "pending" ? "pending" : result.Delivery ?? result.Status,
                    $"execution={result.Execution ?? "unavailable"}; effect={result.Effect ?? "unavailable"}; reason={result.Reason ?? "none"}",
                    result.RequestId);
        }
        PolicyRuntimeReceiptStatus? receipt = (policyRuntime as PolicyRuntimeStatus)?.LastReceipt;
        if (receipt == null)
        {
            return new PlatformReceiptView(
                "unavailable",
                "Policy Runtime status has no last_receipt.",
                null);
        }

        string detail = receipt.ReasonCode
            ?? (receipt.SuccessorSnapshotId == null
                ? "No reason code supplied."
                : $"successor={receipt.SuccessorSnapshotId}");
        return new PlatformReceiptView(receipt.Delivery, detail, receipt.RequestId);
    }

    private static IReadOnlyList<PlatformReadView> ReadReads(
        PlayerEnvironmentSnapshot? snapshot,
        IPlatformRuntimeStatus? policyRuntime)
    {
        var reads = new Dictionary<string, PlatformReadView>(StringComparer.Ordinal);
        if (snapshot != null)
        {
            foreach (PlayerEnvironmentReadOpportunity read in snapshot.Reads)
            {
                reads[read.ReadId] = new PlatformReadView(
                    read.ReadId,
                    read.Kind,
                    "advertised by Connector Snapshot");
            }
        }
        if (policyRuntime is PolicyRuntimeStatus policy)
        {
            foreach (PolicyRuntimeReadStatus read in policy.Reads)
            {
                reads[read.ReadId] = new PlatformReadView(
                    read.ReadId,
                    read.Kind,
                    "materialized by Policy Runtime");
            }
        }
        return reads.Values
            .OrderBy(read => read.Kind, StringComparer.Ordinal)
            .ThenBy(read => read.ReadId, StringComparer.Ordinal)
            .ToArray();
    }

    private static IReadOnlyList<string> ReadInvalidations(IPlatformRuntimeStatus? policyRuntime) =>
        policyRuntime?.Invalidations.ToArray()
        ?? Array.Empty<string>();
}
