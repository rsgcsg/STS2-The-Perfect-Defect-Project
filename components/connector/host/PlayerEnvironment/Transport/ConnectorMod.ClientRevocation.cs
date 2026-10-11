using System;
using System.Net;
using System.Text.Json;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector;

public static partial class ConnectorMod
{
    private static void HandlePostPlayerEnvironmentClientRevocation(
        HttpListenerRequest request, HttpListenerResponse response)
    {
        if (request.ContentLength64 > ClientRevocationWire.MaxRequestBytes)
        { SendApiError(response, 413, "request_too_large", "Client revocation body exceeds 1 KiB."); return; }
        byte[]? bytes = ReadBoundedBodyBytes(request.InputStream, ClientRevocationWire.MaxRequestBytes);
        if (bytes is null)
        { SendApiError(response, 413, "request_too_large", "Client revocation body exceeds 1 KiB."); return; }
        PlayerEnvironmentClientRevocationRequest original;
        try { original = ClientRevocationWire.Decode(bytes); }
        catch (JsonException)
        { SendApiError(response, 400, "invalid_client_revocation", "Exact original runtime/client identifiers are required."); return; }
        try
        {
            // No game/main-thread queue: acknowledgement itself permanently
            // closes this original Authority identity and its owned lease.
            PlayerEnvironmentClientRevocationResponse result = PlayerEnvironmentService.RevokePlayerEnvironmentClient(original);
            if (!result.Closed)
            {
                int status = result.Status == "runtime_instance_mismatch" ? 409
                    : result.Status == "client_session_not_found" ? 404 : 400;
                SendApiError(response, status, result.Status, "The original client closure was not acknowledged.");
                return;
            }
            response.StatusCode = 200;
            SendJson(response, result);
        }
        catch (Exception exception)
        { SendApiInternalError(response, "player_environment_client_revocation_failed", exception); }
    }
}
