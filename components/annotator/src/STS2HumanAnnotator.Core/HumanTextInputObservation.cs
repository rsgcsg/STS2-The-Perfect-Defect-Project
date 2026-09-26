using System.Text.Json.Nodes;

namespace STS2HumanAnnotator.Core;

/// <summary>Read-only Human input evidence. It is never a canonical transition or successor.</summary>
public static class HumanTextInputObservationContract
{
    public const int SchemaVersion = 1;
    public const string Schema = "sts2.human-annotator/human-text-input-1";
    public const string FileName = "human-text-inputs.jsonl";
    public const string AcceptedInput = "accepted_input";
    public const string NotMapped = "not_mapped";
    public const string CaptureFailed = "capture_failed";
    public const string RejectedOrCancelled = "rejected_or_cancelled";
    public const string ExactMappingBasis = "text_menu_native_reference_equality";
    public const string NativeMechanism = "begin_card_play_exact_factory_return";
    public const string ControllerConfirmedInputSignal = "controller_confirmed_input_signal";
    public const string ControllerCanceledInputSignal = "controller_canceled_input_signal";
    public const string ControllerTargetFinishInput = "controller_target_finish_input";
    public const string ControllerTargetCanceledInput = "controller_target_canceled_input";

    public static string? VerbForMechanism(string mechanism) => mechanism switch
    {
        NativeMechanism => "begin_card_play",
        ControllerConfirmedInputSignal => "confirm_card",
        ControllerCanceledInputSignal or ControllerTargetCanceledInput => "cancel_card_play",
        ControllerTargetFinishInput => "confirm_target",
        _ => null
    };
}

public sealed record HumanTextInputObservation(
    int SchemaVersion,
    string Schema,
    long Sequence,
    string RecordId,
    string SessionId,
    string TimelineId,
    string RunId,
    DateTimeOffset ObservedAt,
    DateTimeOffset RecordedAt,
    RecorderEnvironmentIdentity? Environment,
    JsonObject? Snapshot,
    string? SnapshotSha256,
    JsonObject? ChosenAction,
    string MappingStatus,
    int MatchCount,
    string MappingBasis,
    string? NativeOwnerWitnessId,
    string? NativeSubjectWitnessId,
    string? NativeCarrierWitnessId,
    string NativeMechanism,
    string Disposition,
    string? ReasonCode,
    bool ExternalControllerActive);

public static class HumanTextInputObservationValidator
{
    public static IReadOnlyList<string> Validate(HumanTextInputObservation? value)
    {
        var errors = new List<string>();
        if (value is null) return new[] { "text_input_missing" };
        if (value.SchemaVersion != HumanTextInputObservationContract.SchemaVersion
            || value.Schema != HumanTextInputObservationContract.Schema)
            errors.Add("text_input_schema_invalid");
        if (value.Sequence <= 0 || string.IsNullOrWhiteSpace(value.RecordId)
            || string.IsNullOrWhiteSpace(value.SessionId)
            || string.IsNullOrWhiteSpace(value.TimelineId)
            || string.IsNullOrWhiteSpace(value.RunId)
            || value.ObservedAt == default || value.RecordedAt == default
            || value.RecordedAt < value.ObservedAt)
            errors.Add("text_input_identity_invalid");
        if (value.ExternalControllerActive)
            errors.Add("text_input_external_controller");
        string? expectedVerb = HumanTextInputObservationContract.VerbForMechanism(value.NativeMechanism);
        if (expectedVerb == null)
            errors.Add("text_input_native_mechanism_invalid");
        if (value.ChosenAction is JsonObject mappedAction
            && String(mappedAction, "verb") != expectedVerb)
            errors.Add("text_input_native_verb_mechanism_mismatch");
        if (string.IsNullOrWhiteSpace(value.NativeOwnerWitnessId)
            || (value.Disposition != HumanTextInputObservationContract.CaptureFailed
                && string.IsNullOrWhiteSpace(value.NativeSubjectWitnessId)))
            errors.Add("text_input_native_witness_invalid");

        bool accepted = value.Disposition == HumanTextInputObservationContract.AcceptedInput;
        if (!accepted && value.Disposition is not (HumanTextInputObservationContract.NotMapped
            or HumanTextInputObservationContract.CaptureFailed
            or HumanTextInputObservationContract.RejectedOrCancelled))
            errors.Add("text_input_disposition_invalid");
        if (!accepted && string.IsNullOrWhiteSpace(value.ReasonCode))
            errors.Add("text_input_failure_reason_missing");
        if (accepted && value.ReasonCode is not null)
            errors.Add("text_input_accepted_with_failure_reason");
        if (value.MatchCount < 0)
            errors.Add("text_input_match_count_invalid");
        if (accepted && (value.MappingStatus != "exact_unique" || value.MatchCount != 1
            || value.MappingBasis != HumanTextInputObservationContract.ExactMappingBasis
            || string.IsNullOrWhiteSpace(value.NativeCarrierWitnessId)))
            errors.Add("text_input_exact_mapping_missing");

        if (value.Snapshot is null)
        {
            if (accepted || value.SnapshotSha256 is not null || value.ChosenAction is not null)
                errors.Add("text_input_snapshot_missing");
            if (value.Disposition != HumanTextInputObservationContract.CaptureFailed)
                errors.Add("text_input_missing_snapshot_without_capture_failure");
            return errors;
        }
        if (value.Environment is null)
            errors.Add("text_input_environment_missing");
        else if (!ExactEnvironment(value.Environment))
            errors.Add("text_input_environment_identity_invalid");
        if (value.SnapshotSha256 != EvidenceIdentity.Sha256Json(value.Snapshot))
            errors.Add("text_input_snapshot_digest_mismatch");
        JsonObject snapshot = value.Snapshot;
        if (String(snapshot, "schema") != "sts2.player-environment/text-menu-snapshot-1"
            || String(snapshot, "input_profile") != "text-menu-v1"
            || String(snapshot, "protocol_version") != value.Environment?.PlayerEnvironmentProtocol
            || string.IsNullOrWhiteSpace(String(snapshot, "snapshot_id"))
            || snapshot["session"] is not JsonObject session
            || String(session, "runtime_instance_id") != value.Environment?.RuntimeInstanceId
            || String(session, "environment_fingerprint") != value.Environment?.EnvironmentFingerprint)
            errors.Add("text_input_snapshot_environment_mismatch");
        if (accepted)
        {
            if (String(snapshot, "status") != "interactive"
                || !Number(snapshot["sequence"], out long snapshotOrdinal)
                || snapshotOrdinal <= 0
                || !snapshot.ContainsKey("persistent")
                || !DateTimeOffset.TryParse(String(snapshot, "observed_at"), out _)
                || snapshot["interaction"] is not JsonObject interaction
                || string.IsNullOrWhiteSpace(String(interaction, "interaction_id"))
                || string.IsNullOrWhiteSpace(String(interaction, "kind"))
                || string.IsNullOrWhiteSpace(String(interaction, "content_schema"))
                || interaction["content"] is not JsonObject content
                || content["surface"] is not JsonObject surface
                || string.IsNullOrWhiteSpace(String(surface, "kind"))
                || content["context"] is not JsonObject
                || snapshot["referents"] is not JsonArray referents
                || snapshot["information_policy"] is not JsonObject policy
                || string.IsNullOrWhiteSpace(String(policy, "scope"))
                || policy["includes_hidden_information"] is not JsonValue hidden
                || !hidden.TryGetValue<bool>(out bool includesHidden)
                || includesHidden
                || snapshot["completeness"] is not JsonObject completeness
                || String(completeness, "status") != "complete"
                || snapshot["menu"] is not JsonObject menu
                || String(menu, "cursor") != "root"
                || !Number(menu["revision"], out _)
                || string.IsNullOrWhiteSpace(String(menu, "native_snapshot_id"))
                || snapshot["menu_actions"] is not JsonObject catalog
                || String(catalog, "status") != "complete"
                || String(catalog, "ordering_semantics") != "native_order_with_fixed_information_groups"
                || catalog["actions"] is not JsonArray actions
                || actions.Count == 0
                || !CountEquals(catalog, actions.Count)
                || !CompleteCatalog(actions, referents))
            {
                errors.Add("text_input_catalog_incomplete");
            }
            else if (value.ChosenAction is not JsonObject chosen
                || String(chosen, "kind") != "native_input"
                || String(chosen, "effect_domain") != "native_input"
                || String(chosen, "verb") != expectedVerb
                || string.IsNullOrWhiteSpace(String(chosen, "action_id"))
                || string.IsNullOrWhiteSpace(String(chosen, "subject_referent_id"))
                || chosen["arguments"] is not JsonArray
                || actions.OfType<JsonObject>().Count(action =>
                    EvidenceIdentity.Sha256Json(action) == EvidenceIdentity.Sha256Json(chosen)) != 1)
                errors.Add("text_input_chosen_action_not_unique");
        }
        return errors;
    }

    private static string? String(JsonObject value, string key) =>
        value[key] is JsonValue node && node.TryGetValue<string>(out string? text) ? text : null;

    private static bool CountEquals(JsonObject catalog, int count) =>
        Number(catalog["materialized_count"], out long materializedCount)
        && materializedCount == count
        && Number(catalog["total_count"], out long totalCount)
        && totalCount == count;

    private static bool Number(JsonNode? node, out long number) =>
        long.TryParse(node?.ToJsonString(), out number);

    private static bool CompleteCatalog(JsonArray actions, JsonArray referents)
    {
        var referentVisibility = new Dictionary<string, bool>(StringComparer.Ordinal);
        foreach (JsonNode? node in referents)
        {
            if (node is not JsonObject referent
                || string.IsNullOrWhiteSpace(String(referent, "referent_id"))
                || string.IsNullOrWhiteSpace(String(referent, "role"))
                || String(referent, "kind") is not ("entity" or "control")
                || referent["state"] is not JsonObject state
                || state["visible"] is not JsonValue visibleNode
                || !visibleNode.TryGetValue<bool>(out bool visible)
                || !referentVisibility.TryAdd(String(referent, "referent_id")!, visible))
                return false;
        }
        var ids = new HashSet<string>(StringComparer.Ordinal);
        foreach (JsonNode? node in actions)
        {
            if (node is not JsonObject action
                || string.IsNullOrWhiteSpace(String(action, "action_id"))
                || !ids.Add(String(action, "action_id")!)
                || String(action, "kind") is not ("native_input" or "system_navigation")
                || string.IsNullOrWhiteSpace(String(action, "verb"))
                || string.IsNullOrWhiteSpace(String(action, "label"))
                || String(action, "effect_domain") !=
                    (String(action, "kind") == "native_input" ? "native_input" : "text_menu")
                || action["arguments"] is not JsonArray arguments)
                return false;
            string? subject = String(action, "subject_referent_id");
            if (subject is not null && referentVisibility.GetValueOrDefault(subject) != true) return false;
            foreach (JsonNode? argumentNode in arguments)
                if (argumentNode is not JsonObject argument
                    || string.IsNullOrWhiteSpace(String(argument, "role"))
                    || referentVisibility.GetValueOrDefault(String(argument, "referent_id") ?? "") != true)
                    return false;
        }
        return true;
    }

    private static bool ExactEnvironment(RecorderEnvironmentIdentity value)
    {
        static bool Sha(string? hash) => hash?.Length == 64 && hash.All(Uri.IsHexDigit);
        static bool Artifact(ExactArtifactIdentity value) =>
            !string.IsNullOrWhiteSpace(value.Product)
            && !string.IsNullOrWhiteSpace(value.Version)
            && value.SourceRevision?.Length == 40 && value.SourceRevision.All(Uri.IsHexDigit)
            && Sha(value.SourceDigestSha256) && Sha(value.Sha256)
            && Guid.TryParse(value.ModuleVersionId, out _);
        return value.Game != null && Sha(value.Game.MainAssemblySha256)
            && Guid.TryParse(value.Game.MainAssemblyModuleVersionId, out _)
            && value.Connector != null && Artifact(value.Connector)
            && value.Annotator != null && Artifact(value.Annotator)
            && !string.IsNullOrWhiteSpace(value.PlayerEnvironmentProtocol)
            && !string.IsNullOrWhiteSpace(value.RuntimeInstanceId)
            && !string.IsNullOrWhiteSpace(value.EnvironmentFingerprint)
            && !string.IsNullOrWhiteSpace(value.ModsetStatus)
            && !string.IsNullOrWhiteSpace(value.ModsetFingerprint);
    }
}
