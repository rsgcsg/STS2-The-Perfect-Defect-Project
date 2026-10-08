using System;
using System.Collections.Generic;
using System.Net;
using System.Text.Json;
using System.Threading;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector;

public static partial class ConnectorMod
{
    internal sealed record NativeLogicalReadRequest(string CaptureId, string Cursor, int MaxBytes);
    internal sealed record NativeLogicalCatalogRequest(string CatalogRef, string StreamGeneration,
        JsonElement? Prefix, string? Cursor, int Limit, int MaxPageBytes);
    internal sealed record NativeLogicalResolveRequest(string CatalogRef, string StreamGeneration, JsonElement Expression);
    internal sealed record NativeLogicalEventsRequest(string ClientSessionId, string SubscriptionId,
        string ScopeId, string AfterCursor, int Limit);
    internal sealed record NativeLogicalAwaitRequest(string ClientSessionId, string SubscriptionId, string ScopeId,
        string AfterCursor, string WaitId, string Condition, int TimeoutMs, NativeLogicalControlBinding? ControlBinding);

    internal static T DecodeNativeLogicalRequest<T>(byte[] bytes) => NativeLogicalDecoder.Decode<T>(bytes);
    private static T ReadNativeLogicalRequest<T>(HttpListenerRequest request)
    {
        const int maxRequestBytes = 32 * 1024;
        if (request.ContentLength64 > maxRequestBytes)
            throw new NativeLogicalException("request_too_large", "Native logical requests are bounded to 32 KiB.");
        byte[] bytes = ReadBoundedBodyBytes(request.InputStream, maxRequestBytes)
            ?? throw new NativeLogicalException("request_too_large", "Native logical requests are bounded to 32 KiB.");
        return DecodeNativeLogicalRequest<T>(bytes);
    }
    private static void NativeId(string value, int maximum = 128)
    {
        if (!IsSafeProtocolIdentifier(value, maximum)) throw new NativeLogicalException("invalid_identifier", "A bounded exact protocol identifier is required.");
    }
    private static void NativeCursor(string value)
    {
        if (value.Length is 0 or > 1024) throw new NativeLogicalException("cursor_mismatch", "A bounded original opaque cursor is required.");
    }
    private static void SendNativeLogicalJson<T>(HttpListenerResponse response, T value, int maxBytes = 64 * 1024 * 1024)
    {
        byte[] encoded = NativeLogicalWire.EncodeBounded(value, maxBytes);
        response.ContentType = "application/json; charset=utf-8";
        response.ContentLength64 = encoded.Length;
        response.OutputStream.Write(encoded);
        response.Close();
    }
    private static void HandleNativeLogical(string operation, HttpListenerRequest request, HttpListenerResponse response)
    {
        if (operation == "capabilities")
        {
            if (request.HttpMethod != "GET") { SendApiError(response, 405, "method_not_allowed", "Capabilities requires GET."); return; }
            try
            {
                using var deadline = new CancellationTokenSource(30_000);
                SendNativeLogicalJson(response, RunOnMainThread(PlayerEnvironmentService.GetNativeLogicalCapabilities,
                    deadline.Token).GetAwaiter().GetResult(), 1024 * 1024);
            }
            catch (Exception e) { SendApiInternalError(response, "native_logical_capabilities_failed", e); }
            return;
        }
        if (request.HttpMethod != "POST") { SendApiError(response, 405, "method_not_allowed", "Native logical queries require bounded POST JSON."); return; }
        try
        {
            var owner = PlayerEnvironmentService.NativeLogical;
            switch (operation)
            {
                case "current":
                {
                    var body = ReadNativeLogicalRequest<NativeLogicalCurrentRequest>(request);
                    NativeId(body.ClientSessionId);
                    if (body.ExpectedSnapshotId is { } expected) NativeId(expected);
                    NativeLogicalProjector.ValidateScope(body.EagerScope);
                    using var deadline = new CancellationTokenSource(30_000);
                    var reply = owner.CurrentAsync(body, deadline.Token).GetAwaiter().GetResult();
                    response.StatusCode = reply.Status is "captured" or "partial" ? 200 : reply.Status == "capacity_exceeded" ? 429 : 409;
                    SendNativeLogicalJson(response, reply, 1024 * 1024); break;
                }
                case "read":
                {
                    var body = ReadNativeLogicalRequest<NativeLogicalReadRequest>(request);
                    NativeId(body.CaptureId); NativeCursor(body.Cursor);
                    SendNativeLogicalJson(response, owner.Store.Read(body.CaptureId, body.Cursor, body.MaxBytes), owner.Limits.MaxEncodedReadBytes); break;
                }
                case "catalog":
                {
                    var body = ReadNativeLogicalRequest<NativeLogicalCatalogRequest>(request);
                    NativeId(body.CatalogRef); NativeId(body.StreamGeneration);
                    if (body.Cursor is { } cursor) NativeCursor(cursor);
                    SendNativeLogicalJson(response, owner.Store.CatalogByReference(body.CatalogRef).List(body.StreamGeneration,
                        body.Prefix, body.Cursor, body.Limit, body.MaxPageBytes), owner.Limits.MaxPageBytes); break;
                }
                case "resolve":
                {
                    var body = ReadNativeLogicalRequest<NativeLogicalResolveRequest>(request);
                    NativeId(body.CatalogRef); NativeId(body.StreamGeneration);
                    SendNativeLogicalJson(response, owner.Store.CatalogByReference(body.CatalogRef).Resolve(body.StreamGeneration, body.Expression), owner.Limits.MaxPageBytes); break;
                }
                case "attach":
                {
                    var body = ReadNativeLogicalRequest<NativeLogicalAttachRequest>(request);
                    NativeId(body.ClientSessionId);
                    using var deadline = new CancellationTokenSource(30_000);
                    SendNativeLogicalJson(response, RunOnMainThread(() => owner.Attach(body), deadline.Token).GetAwaiter().GetResult(), 1024 * 1024); break;
                }
                case "events":
                {
                    var body = ReadNativeLogicalRequest<NativeLogicalEventsRequest>(request);
                    NativeId(body.ClientSessionId); NativeId(body.SubscriptionId); NativeId(body.ScopeId); NativeCursor(body.AfterCursor);
                    SendNativeLogicalJson(response, owner.Hub.Events(body.ClientSessionId, body.SubscriptionId, body.ScopeId, body.AfterCursor, body.Limit)); break;
                }
                case "await":
                {
                    var body = ReadNativeLogicalRequest<NativeLogicalAwaitRequest>(request);
                    NativeId(body.ClientSessionId); NativeId(body.SubscriptionId); NativeId(body.ScopeId); NativeCursor(body.AfterCursor);
                    if (body.ControlBinding is { } control) NativeId(control.ControllerLeaseId);
                    SendNativeLogicalJson(response, owner.Hub.AwaitAsync(body.ClientSessionId, body.SubscriptionId, body.ScopeId,
                        body.AfterCursor, body.WaitId, body.Condition, body.TimeoutMs, body.ControlBinding).GetAwaiter().GetResult(), 1024 * 1024); break;
                }
                case "cancel_wait":
                {
                    var body = ReadNativeLogicalRequest<NativeLogicalCancelWaitRequest>(request);
                    NativeId(body.ClientSessionId); NativeId(body.SubscriptionId); NativeId(body.WaitId);
                    SendNativeLogicalJson(response, owner.Hub.CancelWaitPublic(body), 1024 * 1024); break;
                }
                case "detach":
                {
                    var body = ReadNativeLogicalRequest<NativeLogicalDetachRequest>(request);
                    NativeId(body.ClientSessionId); NativeId(body.SubscriptionId);
                    SendNativeLogicalJson(response, owner.Hub.DetachPublic(body), 1024 * 1024); break;
                }
                case "renew":
                {
                    var body = ReadNativeLogicalRequest<NativeLogicalRenewRequest>(request);
                    NativeId(body.ClientSessionId); NativeId(body.SubscriptionId); NativeId(body.ScopeId); NativeCursor(body.AfterCursor);
                    SendNativeLogicalJson(response, owner.Hub.Renew(body), 1024 * 1024); break;
                }
                case "retain":
                {
                    var body = ReadNativeLogicalRequest<NativeLogicalRetainRequest>(request);
                    NativeId(body.ClientSessionId); NativeId(body.CaptureId);
                    SendNativeLogicalJson(response, owner.Retain(body), 1024 * 1024); break;
                }
                case "release":
                {
                    var body = ReadNativeLogicalRequest<NativeLogicalReleaseRequest>(request);
                    NativeId(body.ClientSessionId); NativeId(body.RetentionHandleId);
                    SendNativeLogicalJson(response, owner.Store.ReleasePublic(body), 1024 * 1024); break;
                }
                default: SendApiError(response, 404, "not_found", "Unknown native logical operation."); break;
            }
        }
        catch (NativeLogicalException e)
        {
            SendApiError(response, e.Code switch
            {
                "request_too_large" => 413, "capacity_exceeded" => 429,
                "expired" or "payload_expired" => 410,
                "invalid_wire" or "invalid_limit" or "invalid_identifier" or "invalid_expression" or "cursor_mismatch" or "unsupported_scope" => 400,
                _ => 409
            }, e.Code, e.Message);
        }
        catch (OperationCanceledException) { SendApiError(response, 408, "native_query_cancelled", "The unstarted native query was cancelled at its deadline."); }
        catch (Exception e) { SendApiInternalError(response, "native_logical_query_failed", e); }
    }
}
