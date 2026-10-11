using System;
using System.Collections.Generic;

namespace STS2Connector.Authority;

internal static class MutationControlContract
{
    internal const string ProtocolVersion = "player-environment-control-1";
}

public sealed record MutationControlCapability(
    string Status,
    bool RegistrationRequiredForMutation,
    bool SingleController,
    bool ReadsRequireRegistration,
    int LeaseTtlMs,
    int RecommendedRenewalMs,
    string RuntimeEpoch,
    IReadOnlyList<string> Limitations);

public sealed record MutationClientRegistrationRequest(
    string? ClientInstanceId,
    string? ProductId,
    string? ProductName,
    string? ProductVersion);

internal sealed record MutationClientRevocationRequest(string? RuntimeInstanceId, string? ClientSessionId);
internal sealed record MutationClientRevocationResult(string RuntimeInstanceId, string ClientSessionId,
    string Status, bool Closed);

public sealed record MutationClient(
    string ClientSessionId,
    string ClientInstanceId,
    string ProductId,
    string ProductName,
    string ProductVersion,
    DateTimeOffset RegisteredAt,
    DateTimeOffset LastSeenAt);

public sealed record MutationLeaseRequest(
    string? ClientSessionId,
    string? ControllerLeaseId,
    long? ControllerGeneration);

public sealed record MutationAuthorizationRequest(
    string? ClientSessionId,
    string? ControllerLeaseId,
    long? ControllerGeneration);

public sealed record MutationLease(
    string Status,
    string ControllerLeaseId,
    long ControllerGeneration,
    string ClientSessionId,
    DateTimeOffset AcquiredAt,
    DateTimeOffset ExpiresAt);

public sealed record MutationControlSnapshot(
    string ProtocolVersion,
    string RuntimeInstanceId,
    IReadOnlyList<MutationClient> Clients,
    MutationLease? Controller);

public sealed record MutationClientRegistrationResult(
    string ProtocolVersion,
    string RuntimeInstanceId,
    MutationClient Client,
    MutationLease? Controller);

public sealed record MutationLeaseResult(
    string ProtocolVersion,
    string RuntimeInstanceId,
    string Status,
    string Detail,
    MutationClient? Client,
    MutationLease? Controller);

public sealed record MutationAttribution(
    string RuntimeInstanceId,
    string ClientSessionId,
    string ClientInstanceId,
    string ProductId,
    string ProductName,
    string ProductVersion,
    string ControllerLeaseId,
    long ControllerGeneration);

internal sealed record MutationAdmission(
    bool Accepted,
    string? ErrorCode,
    string? Detail,
    MutationAttribution? Attribution)
{
    public static MutationAdmission Allow(MutationAttribution attribution) =>
        new(true, null, null, attribution);

    public static MutationAdmission Reject(string code, string detail) =>
        new(false, code, detail, null);
}
