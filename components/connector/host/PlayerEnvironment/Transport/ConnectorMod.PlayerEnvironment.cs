using System;
using System.Net;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2Connector.PlayerEnvironment;

namespace STS2Connector;

public static partial class ConnectorMod
{
    private const int MaxPlayerEnvironmentActionBodyBytes = 16 * 1024;
    private const int MaxPlayerEnvironmentNativePageEvidenceBodyBytes = 4 * 1024;

    private static void HandleGetCapabilities(
        HttpListenerRequest request,
        HttpListenerResponse response)
    {
        string? inputProfile = request.QueryString["input_profile"];
        if (!PlayerEnvironmentService.IsSupportedInputProfile(inputProfile))
        {
            SendApiError(response, 400, "unsupported_input_profile", "Unsupported input profile.");
            return;
        }
        try
        {
            var task = RunOnMainThread(() => PlayerEnvironmentService.GetCapabilities(inputProfile));
            SendJson(response, task.GetAwaiter().GetResult());
        }
        catch (Exception exception)
        {
            SendApiInternalError(response, "player_environment_capabilities_failed", exception);
        }
    }

    private static void HandleGetPlayerEnvironmentSnapshot(
        HttpListenerRequest request,
        HttpListenerResponse response)
    {
        string? inputProfile = request.QueryString["input_profile"];
        if (!PlayerEnvironmentService.IsSupportedInputProfile(inputProfile))
        {
            SendApiError(response, 400, "unsupported_input_profile", "Unsupported input profile.");
            return;
        }
        try
        {
            if (inputProfile == TextMenuContract.Profile)
            {
                var menuTask = RunOnMainThread(PlayerEnvironmentService.ObserveTextMenu);
                SendJson(response, menuTask.GetAwaiter().GetResult());
                return;
            }
            var task = RunOnMainThread(() => PlayerEnvironmentService.Observe(inputProfile));
            SendJson(response, task.GetAwaiter().GetResult());
        }
        catch (Exception exception)
        {
            SendApiInternalError(response, "player_environment_observation_failed", exception);
        }
    }

    private static void HandleGetPlayerEnvironmentRead(
        string encodedReadId,
        HttpListenerRequest request,
        HttpListenerResponse response)
    {
        string readId;
        try
        {
            readId = Uri.UnescapeDataString(encodedReadId);
        }
        catch (UriFormatException)
        {
            SendApiError(response, 400, "invalid_read_id", "Read id is not valid URI data.");
            return;
        }
        string? expectedSnapshotId = request.QueryString["expected_snapshot_id"];
        if (!IsSafePlayerEnvironmentReadIdentifier(readId)
            || !IsSafeProtocolIdentifier(expectedSnapshotId, 128))
        {
            SendApiError(response, 400, "invalid_read_contract", "A current advertised read id and snapshot ID are required.");
            return;
        }
        try
        {
            var task = RunOnMainThread(() => PlayerEnvironmentService.Read(readId, expectedSnapshotId!));
            PlayerEnvironmentReadResult result = task.GetAwaiter().GetResult();
            if (result.Read != null)
            {
                SendJson(response, result.Read);
                return;
            }
            SendApiError(
                response,
                result.ErrorCode is "read_not_available" or "read_kind_not_implemented" ? 404 : 409,
                result.ErrorCode ?? "read_failed",
                result.Detail ?? "Read failed closed.");
        }
        catch (Exception exception)
        {
            SendApiInternalError(response, "player_environment_read_failed", exception);
        }
    }

    internal static bool IsSafePlayerEnvironmentReadIdentifier(string? readId)
    {
        const string prefix = "read:";
        if (readId?.StartsWith(prefix, StringComparison.Ordinal) != true
            || readId.Length > 256)
            return false;
        string[] segments = readId.Split(':');
        if (segments.Length < 2 || !string.Equals(segments[0], "read", StringComparison.Ordinal))
            return false;
        for (int index = 1; index < segments.Length; index++)
        {
            if (!IsSafeProtocolIdentifier(segments[index], 128))
                return false;
        }
        return true;
    }

    private static void HandlePostPlayerEnvironmentAction(
        HttpListenerRequest request,
        HttpListenerResponse response)
    {
        PlayerEnvironmentActionRequest? action =
            ReadBoundedJsonBody<PlayerEnvironmentActionRequest>(
                request,
                response,
                MaxPlayerEnvironmentActionBodyBytes,
                "Player Environment action");
        if (action == null)
            return;
        if (!IsSafeProtocolIdentifier(action.RequestId, 128)
            || !IsSafeProtocolIdentifier(action.ExpectedSnapshotId, 128)
            || !IsSafeProtocolIdentifier(action.BoundActionId, 128))
        {
            SendApiError(
                response,
                400,
                "invalid_player_environment_action",
                "Exact request, snapshot and advertised bound-action identifiers are required.");
            return;
        }
        if (!PlayerEnvironmentService.IsSupportedInputProfile(action.InputProfile))
        {
            SendApiError(response, 400, "unsupported_input_profile", "Unsupported input profile.");
            return;
        }
        try
        {
            if (action.InputProfile == TextMenuContract.Profile)
            {
                var menuTask = RunOnMainThread(() => PlayerEnvironmentService.SubmitTextMenu(action));
                TextMenuActionResult result = menuTask.GetAwaiter().GetResult();
                response.StatusCode = result.Status switch
                {
                    "applied" => 200, "unknown" => 202, _ => 409
                };
                SendJson(response, result);
                return;
            }
            var task = RunOnMainThread(() => PlayerEnvironmentService.Submit(action));
            PlayerEnvironmentActionReceipt receipt = task.GetAwaiter().GetResult();
            response.StatusCode = receipt.Delivery switch
            {
                "delivered" => 200,
                "unknown" => 202,
                _ => 409
            };
            SendJson(response, receipt);
        }
        catch (Exception exception)
        {
            SendApiInternalError(response, "player_environment_action_failed", exception);
        }
    }

    private static void HandlePostPlayerEnvironmentClientRegistration(
        HttpListenerRequest request,
        HttpListenerResponse response)
    {
        PlayerEnvironmentClientRegistrationRequest? registration =
            ReadBoundedJsonBody<PlayerEnvironmentClientRegistrationRequest>(
                request,
                response,
                MaxPlayerEnvironmentActionBodyBytes,
                "Player Environment control");
        if (registration == null)
            return;
        if (!IsSafeProtocolIdentifier(registration.ClientInstanceId, 128)
            || !IsSafeProtocolIdentifier(registration.ProductId, 64)
            || !IsSafeProtocolLabel(registration.ProductName, 128)
            || !IsSafeProtocolIdentifier(registration.ProductVersion, 64))
        {
            SendApiError(
                response,
                400,
                "invalid_client_registration",
                "Client instance, product id, product name and product version are required and bounded.");
            return;
        }

        try
        {
            PlayerEnvironmentClientRegistrationResponse result =
                PlayerEnvironmentService.RegisterPlayerEnvironmentClient(registration);
            response.StatusCode = 201;
            SendJson(response, result);
        }
        catch (Exception exception)
        {
            SendApiInternalError(response, "player_environment_client_registration_failed", exception);
        }
    }

    private static void HandleGetPlayerEnvironmentControl(HttpListenerResponse response)
    {
        try
        {
            SendJson(response, PlayerEnvironmentService.GetPlayerEnvironmentControlSnapshot());
        }
        catch (Exception exception)
        {
            SendApiInternalError(response, "player_environment_controller_status_read_failed", exception);
        }
    }

    private static void HandlePostPlayerEnvironmentController(
        string operation,
        HttpListenerRequest request,
        HttpListenerResponse response)
    {
        PlayerEnvironmentControllerLeaseRequest? lease =
            ReadBoundedJsonBody<PlayerEnvironmentControllerLeaseRequest>(
                request,
                response,
                MaxPlayerEnvironmentActionBodyBytes,
                "Player Environment control");
        if (lease == null)
            return;
        bool acquire = string.Equals(operation, "acquire", StringComparison.Ordinal);
        if (!IsSafeProtocolIdentifier(lease.ClientSessionId, 128)
            || (!acquire && !IsSafeProtocolIdentifier(lease.ControllerLeaseId, 128))
            || (!acquire && lease.ControllerGeneration is null or <= 0))
        {
            SendApiError(
                response,
                400,
                "invalid_controller_contract",
                acquire
                    ? "client_session_id is required to acquire mutation control."
                    : "client_session_id, controller_lease_id and a positive controller_generation are required.");
            return;
        }

        try
        {
            PlayerEnvironmentControllerLeaseResponse result = operation switch
            {
                "acquire" => PlayerEnvironmentService.AcquirePlayerEnvironmentController(lease),
                "renew" => PlayerEnvironmentService.RenewPlayerEnvironmentController(lease),
                _ => PlayerEnvironmentService.ReleasePlayerEnvironmentController(lease)
            };
            response.StatusCode = result.Status switch
            {
                "controller_acquired" or "controller_already_held"
                    or "controller_renewed" or "controller_released" => 200,
                "controller_lease_held" or "controller_lease_stale" => 409,
                "client_session_not_found" => 404,
                _ => 409
            };
            SendJson(response, result);
        }
        catch (Exception exception)
        {
            SendApiInternalError(response, "player_environment_controller_operation_failed", exception);
        }
    }

    private static void HandleGetPlayerEnvironmentAction(
        string encodedRequestId,
        HttpListenerRequest request,
        HttpListenerResponse response)
    {
        string? inputProfile = request.QueryString["input_profile"];
        if (!PlayerEnvironmentService.IsSupportedInputProfile(inputProfile))
        {
            SendApiError(response, 400, "unsupported_input_profile", "Unsupported input profile.");
            return;
        }
        string requestId;
        try
        {
            requestId = Uri.UnescapeDataString(encodedRequestId);
        }
        catch (UriFormatException)
        {
            SendApiError(response, 400, "invalid_request_id", "request_id is not valid URI data.");
            return;
        }
        if (!IsSafeProtocolIdentifier(requestId, 128))
        {
            SendApiError(response, 400, "invalid_request_id", "A bounded request_id is required.");
            return;
        }
        if (inputProfile == TextMenuContract.Profile)
        {
            TextMenuActionResult? result = PlayerEnvironmentService.FindTextMenuResult(requestId);
            if (result == null)
            {
                SendApiError(response, 404, "request_not_found", "No text-menu result exists for this request ID.");
                return;
            }
            SendJson(response, result);
            return;
        }
        PlayerEnvironmentActionReceipt? receipt = PlayerEnvironmentService.FindReceipt(requestId);
        if (receipt == null)
        {
            SendApiError(response, 404, "request_not_found", "No Player Environment receipt exists for request_id.");
            return;
        }
        if (!PlayerEnvironmentService.ReceiptMatchesInputProfile(receipt, inputProfile))
        {
            SendApiError(response, 409, "input_profile_mismatch", "The receipt belongs to a different input profile.");
            return;
        }
        SendJson(response, receipt);
    }

    private static void HandlePostPlayerEnvironmentNativePageEvidenceOpen(
        HttpListenerRequest request,
        HttpListenerResponse response)
    {
        PlayerEnvironmentNativePageOpenRequest? open =
            ReadBoundedJsonBody<PlayerEnvironmentNativePageOpenRequest>(
                request,
                response,
                MaxPlayerEnvironmentNativePageEvidenceBodyBytes,
                "Player Environment native-page evidence");
        if (open == null)
            return;
        if (!IsSafeProtocolIdentifier(open.Profile, 64)
            || !IsSafeProtocolIdentifier(open.Kind, 64)
            || !IsSafeProtocolIdentifier(open.ExpectedSnapshotId, 128)
            || !IsSafeProtocolIdentifier(open.ExpectedRuntimeInstanceId, 128))
        {
            SendApiError(
                response,
                400,
                "invalid_native_page_evidence_contract",
                "Profile, fixed page kind, expected_snapshot_id and expected_runtime_instance_id are required.");
            return;
        }

        try
        {
            var task = RunOnMainThread(() =>
                PlayerEnvironmentService.OpenNativePageEvidence(open));
            SendPlayerEnvironmentNativePageEvidenceResult(
                response,
                task.GetAwaiter().GetResult(),
                201);
        }
        catch (Exception exception)
        {
            SendApiInternalError(
                response,
                "native_page_evidence_open_failed",
                exception);
        }
    }

    private static void HandleGetPlayerEnvironmentNativePageEvidence(
        string encodedSessionId,
        HttpListenerRequest request,
        HttpListenerResponse response)
    {
        string sessionId;
        try
        {
            sessionId = Uri.UnescapeDataString(encodedSessionId);
        }
        catch (UriFormatException)
        {
            SendApiError(
                response,
                400,
                "invalid_native_page_evidence_session",
                "Session id is not valid URI data.");
            return;
        }

        string? expectedRuntime = request.QueryString["expected_runtime_instance_id"];
        if (!IsSafeProtocolIdentifier(sessionId, 128)
            || !IsSafeProtocolIdentifier(expectedRuntime, 128))
        {
            SendApiError(
                response,
                400,
                "invalid_native_page_evidence_session",
                "A bounded session id and expected_runtime_instance_id are required.");
            return;
        }

        try
        {
            var task = RunOnMainThread(() =>
                PlayerEnvironmentService.ReadNativePageEvidence(
                    sessionId,
                    expectedRuntime!));
            SendPlayerEnvironmentNativePageEvidenceResult(
                response,
                task.GetAwaiter().GetResult(),
                200);
        }
        catch (Exception exception)
        {
            SendApiInternalError(
                response,
                "native_page_evidence_read_failed",
                exception);
        }
    }

    private static void HandlePostPlayerEnvironmentNativePageEvidenceReturn(
        string encodedSessionId,
        HttpListenerRequest request,
        HttpListenerResponse response)
    {
        string sessionId;
        try
        {
            sessionId = Uri.UnescapeDataString(encodedSessionId);
        }
        catch (UriFormatException)
        {
            SendApiError(
                response,
                400,
                "invalid_native_page_evidence_session",
                "Session id is not valid URI data.");
            return;
        }

        PlayerEnvironmentNativePageReturnRequest? returned =
            ReadBoundedJsonBody<PlayerEnvironmentNativePageReturnRequest>(
                request,
                response,
                MaxPlayerEnvironmentNativePageEvidenceBodyBytes,
                "Player Environment native-page evidence");
        if (returned == null)
            return;
        if (!IsSafeProtocolIdentifier(sessionId, 128)
            || !IsSafeProtocolIdentifier(returned.Profile, 64)
            || !IsSafeProtocolIdentifier(returned.ExpectedRuntimeInstanceId, 128))
        {
            SendApiError(
                response,
                400,
                "invalid_native_page_evidence_return",
                "A bounded session, profile and expected runtime identity are required.");
            return;
        }

        try
        {
            var task = RunOnMainThread(() =>
                PlayerEnvironmentService.ReturnNativePageEvidence(sessionId, returned));
            SendPlayerEnvironmentNativePageEvidenceResult(
                response,
                task.GetAwaiter().GetResult(),
                200);
        }
        catch (Exception exception)
        {
            SendApiInternalError(
                response,
                "native_page_evidence_return_failed",
                exception);
        }
    }

    private static void SendPlayerEnvironmentNativePageEvidenceResult(
        HttpListenerResponse response,
        PlayerEnvironmentNativePageOperationResult result,
        int successStatus)
    {
        if (result.Response != null)
        {
            response.StatusCode = result.Response.Phase == "recovery_required"
                ? 409
                : successStatus;
            SendJson(response, result.Response);
            return;
        }

        int statusCode = result.ErrorCode switch
        {
            "native_page_evidence_session_not_found" => 404,
            "native_page_evidence_kind_not_supported" => 400,
            _ => 409
        };
        SendApiError(
            response,
            statusCode,
            result.ErrorCode ?? "native_page_evidence_failed",
            result.Detail ?? "Native-page evidence operation failed closed.");
    }
}
