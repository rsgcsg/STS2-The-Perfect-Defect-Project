using System;
using System.Globalization;
using System.Net;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector;

internal enum SealedPublicFormat { TextMenuV2, LegacyP1C1, PublicBasis }

public static partial class ConnectorMod
{
    private static void HandleSealedObservation(string operation,
        HttpListenerRequest request, HttpListenerResponse response, SealedPublicFormat format = SealedPublicFormat.TextMenuV2)
    {
        string profile = format switch
        {
            SealedPublicFormat.LegacyP1C1 => P1CaptureContract.Profile,
            SealedPublicFormat.PublicBasis => PublicBasisContract.Profile,
            _ => TextMenuV2Contract.Profile
        };
        try
        {
            switch (operation)
            {
                case "capabilities" when request.HttpMethod == "GET":
                    if (format == SealedPublicFormat.LegacyP1C1) SendJson(response, PlayerEnvironmentService.GetP1CaptureCapabilities());
                    else SendJson(response, format == SealedPublicFormat.PublicBasis
                        ? PlayerEnvironmentService.GetPublicBasisCapabilities() : PlayerEnvironmentService.GetSealedObservationCapabilities());
                    return;
                case "current" when request.HttpMethod == "GET":
                    if (request.QueryString["input_profile"] != profile)
                    {
                        SendApiError(response, 400, "unsupported_input_profile", "This route requires the declared public capture profile.");
                        return;
                    }
                    string? expected = request.QueryString["expected_snapshot_id"];
                    if (expected != null && !IsSafeProtocolIdentifier(expected, 128))
                    {
                        SendApiError(response, 400, "invalid_snapshot_id", "Expected snapshot ID is invalid.");
                        return;
                    }
                    var task = RunOnMainThread(() => format switch
                    {
                        SealedPublicFormat.LegacyP1C1 => PlayerEnvironmentService.ReadCurrentP1Capture(expected),
                        SealedPublicFormat.PublicBasis => PlayerEnvironmentService.ReadCurrentPublicBasis(expected),
                        _ => PlayerEnvironmentService.ReadCurrentSealedObservation(expected)
                    });
                    SendJson(response, task.GetAwaiter().GetResult());
                    return;
                case "read" when request.HttpMethod == "GET":
                    string? id = request.QueryString["capture_id"];
                    string? cursor = request.QueryString["cursor"];
                    if (!IsSafeProtocolIdentifier(id, 128) || !IsSafeProtocolIdentifier(cursor, 512))
                    {
                        SendApiError(response, 400, "invalid_read_contract", "A capture ID and opaque cursor are required.");
                        return;
                    }
                    int limit = SealedObservationContract.DefaultChunkBytes;
                    if (request.QueryString["max_bytes"] is { } text
                        && !int.TryParse(text, NumberStyles.None, CultureInfo.InvariantCulture, out limit))
                    {
                        SendApiError(response, 400, "invalid_limit", "max_bytes must be an integer.");
                        return;
                    }
                    SendJson(response, format switch
                    {
                        SealedPublicFormat.LegacyP1C1 => PlayerEnvironmentService.ReadP1Capture(id!, cursor!, limit),
                        SealedPublicFormat.PublicBasis => PlayerEnvironmentService.ReadPublicBasis(id!, cursor!, limit),
                        _ => PlayerEnvironmentService.ReadSealedObservation(id!, cursor!, limit)
                    });
                    return;
                case "release" when request.HttpMethod == "POST":
                    var release = ReadBoundedJsonBody<SealedObservationReleaseRequest>(request, response,
                        1024, "sealed observation release");
                    if (release == null) return;
                    if (!IsSafeProtocolIdentifier(release.CaptureId, 128))
                    {
                        SendApiError(response, 400, "invalid_capture_id", "Capture ID is invalid.");
                        return;
                    }
                    SendJson(response, format switch
                    {
                        SealedPublicFormat.LegacyP1C1 => PlayerEnvironmentService.ReleaseP1Capture(release.CaptureId!),
                        SealedPublicFormat.PublicBasis => PlayerEnvironmentService.ReleasePublicBasis(release.CaptureId!),
                        _ => PlayerEnvironmentService.ReleaseSealedObservation(release.CaptureId!)
                    });
                    return;
                default:
                    SendApiError(response, 405, "method_not_allowed", "Unsupported sealed observation operation or method.");
                    return;
            }
        }
        catch (SealedObservationException exception)
        {
            SendApiError(response, exception.Code switch
            {
                "not_found" => 404, "expired" => 410,
                "capacity" => 429, "too_large" => 413,
                "invalid_limit" or "cursor_mismatch" => 400, _ => 409
            }, exception.Code, exception.Message);
        }
        catch (TextMenuRunContinuityChangedException)
        {
            SendApiError(response, 409, "run_continuity_changed_during_capture", "Request a fresh current capture.");
        }
        catch (Exception exception)
        {
            SendApiInternalError(response, operation == "current" ? "capture_failed" : "sealed_observation_failed", exception);
        }
    }
}
