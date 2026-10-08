using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using STS2Connector.Authority;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

internal sealed class NativeLogicalExecutor(object submissionGate,
    ConcurrentDictionary<string, string> fingerprints, NativeLogicalService owner,
    Func<MutationAuthorizationRequest, MutationAdmission>? begin = null)
{
    private readonly Func<MutationAuthorizationRequest, MutationAdmission> tryBegin = begin ?? MutationControlRuntime.TryBegin;
    private readonly ConcurrentDictionary<string, NativeLogicalResult> results = new(StringComparer.Ordinal);
    private readonly ConcurrentDictionary<string, byte> pending = new(StringComparer.Ordinal);
    internal NativeLogicalResult? Find(string id) => results.GetValueOrDefault(id);
    internal bool IsPending(string id) => pending.ContainsKey(id);
    internal static string Delivery(NativeInputResult input) => input.Delivery switch
    {
        NativeInputDelivery.RejectedBeforeInput => "rejected_before_input",
        NativeInputDelivery.Delivered => "delivered",
        NativeInputDelivery.PartiallyDelivered => "partially_delivered",
        NativeInputDelivery.Unknown => "unknown",
        _ => input.Accepted ? "delivered" : "rejected_before_input"
    };
    private static NativeLogicalResult MakeResult(PlayerEnvironmentActionRequest request,
        NativeLogicalAction? action, string delivery, string? reason,
        NativeLogicalInputStage[]? stages = null, PlayerEnvironmentAttribution? attribution = null) => new(
            PlayerEnvironmentContract.ProtocolVersion, NativeLogicalContract.ResultSchema, NativeLogicalContract.Profile,
            request.RequestId ?? "", request.ExpectedSnapshotId ?? "", action, delivery, "unknown", "unknown", "unknown",
            Array.AsReadOnly(stages ?? Array.Empty<NativeLogicalInputStage>()), reason, "never_automatic", null, attribution);

    internal sealed record Admission(string Status, NativeLogicalResult? Result);
    internal Admission Admit(PlayerEnvironmentActionRequest request)
    {
        string id = request.RequestId ?? "";
        if (request.InputProfile != NativeLogicalContract.Profile || string.IsNullOrWhiteSpace(id))
            return new("rejected", MakeResult(request, null, "not_started", "invalid_native_logical_request"));
        string fingerprint = PlayerEnvironmentService.ActionRequestFingerprint(request);
        lock (submissionGate)
        {
            if (fingerprints.TryGetValue(id, out var previous))
            {
                if (previous != fingerprint) return new("conflict", MakeResult(request, null, "not_started", "request_id_conflict"));
                if (results.TryGetValue(id, out var result)) return new("terminal", result);
                return pending.ContainsKey(id) ? new("pending", null)
                    : new("conflict", MakeResult(request, null, "not_started", "request_id_conflict"));
            }
            fingerprints[id] = fingerprint; pending[id] = 0;
            return new("admitted", null);
        }
    }
    internal NativeLogicalResult RejectQueued(PlayerEnvironmentActionRequest request, string reason)
    {
        lock (submissionGate)
        {
            string id = request.RequestId!;
            if (results.TryGetValue(id, out var existing)) return existing;
            var result = MakeResult(request, null, "not_started", reason);
            results[id] = result; pending.TryRemove(id, out _); return result;
        }
    }
    internal NativeLogicalResult Submit(PlayerEnvironmentActionRequest request)
    {
        string id = request.RequestId ?? "";
        NativeLogicalResult Result(NativeLogicalAction? action, string delivery, string? reason,
            NativeLogicalInputStage[]? stages = null, PlayerEnvironmentAttribution? attribution = null) =>
            MakeResult(request, action, delivery, reason, stages, attribution);
        if (request.InputProfile != NativeLogicalContract.Profile || string.IsNullOrWhiteSpace(id))
            return Result(null, "not_started", "invalid_native_logical_request");
        lock (submissionGate)
        {
            string fingerprint = PlayerEnvironmentService.ActionRequestFingerprint(request);
            if (fingerprints.TryGetValue(id, out var previous))
            {
                if (previous != fingerprint) return Result(null, "not_started", "request_id_conflict");
                if (results.TryGetValue(id, out var replay)) return replay;
                if (!pending.ContainsKey(id)) return Result(null, "not_started", "request_id_conflict");
            }
            fingerprints[id] = fingerprint;
            pending[id] = 0;
            NativeLogicalResult Save(NativeLogicalResult result) { results[id] = result; return result; }
            bool started = false;
            try
            {
                var leaf = owner.Revalidate(request.ExpectedSnapshotId ?? "", request.BoundActionId ?? "");
                if (leaf is null) return Save(Result(null, "not_started", "stale_snapshot_or_binding"));
                var action = new NativeLogicalAction(request.BoundActionId!, "native_input", leaf.Verb, leaf.Label,
                    leaf.SubjectReferentId, Array.AsReadOnly(leaf.Arguments.Select(a => new NativeLogicalArgument(a.Role, a.ReferentId)).ToArray()), "native_ui");
                if (!owner.ExecutionAllowed()) return Save(Result(action, "not_started", "exact_game_execution_unavailable"));
                MutationAdmission admission = tryBegin(new(request.ClientSessionId,
                    request.ControllerLeaseId, request.ControllerGeneration));
                if (!admission.Accepted) return Save(Result(action, "not_started", admission.ErrorCode ?? "controller_rejected"));
                started = true;
                var source = admission.Attribution!;
                var attribution = new PlayerEnvironmentAttribution(source.RuntimeInstanceId, source.ClientSessionId,
                    source.ClientInstanceId, source.ProductId, source.ProductName, source.ProductVersion,
                    source.ControllerLeaseId, source.ControllerGeneration);
                // Start already won the shared authority boundary. No lock is
                // held across native code; Stop may promptly revoke new starts.
                owner.Publish("connector_input_start", "input_started");
                NativeInputResult input;
                try { input = leaf.Dispatch(); }
                catch { return Save(Result(action, "unknown", "native_input_boundary_threw", attribution: attribution)); }
                var stages = (input.Stages ?? Array.Empty<NativeInputStage>()).Select(s => new NativeLogicalInputStage(
                    JsonNamingPolicy.SnakeCaseLower.ConvertName(s.Stage.ToString()), s.Delivery switch
                    {
                        NativeInputDelivery.Delivered => "delivered", NativeInputDelivery.PartiallyDelivered => "partially_delivered",
                        NativeInputDelivery.RejectedBeforeInput => "rejected_before_input", _ => "unknown"
                    }, s.Evidence)).ToArray();
                return Save(Result(action, Delivery(input), input.ErrorCode, stages, attribution));
            }
            catch (Exception) { return Save(Result(null, started ? "unknown" : "not_started", started ? "started_input_outcome_unknown" : "native_revalidation_failed")); }
            finally { pending.TryRemove(id, out _); }
        }
    }
}
