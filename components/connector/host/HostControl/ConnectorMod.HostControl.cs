using System;
using System.Net;
using STS2Connector.HostControl;

namespace STS2Connector;

public static partial class ConnectorMod
{
    internal const int MaxHostControlBodyBytes = 4 * 1024;

    internal sealed record HostPreparationProvenance(string Status, bool Ready,
        HostNativeSpeedProvenance NativeSpeed, HostBaselineProvenance Baseline);

    private static void HandlePostHostPreparation(HttpListenerRequest request, HttpListenerResponse response)
    {
        HostPreparationRequest? preparation = ReadBoundedJsonBody<HostPreparationRequest>(
            request, response, MaxHostControlBodyBytes, "Host fixed run preparation");
        if (preparation == null) return;
        if (!IsSafeProtocolIdentifier(preparation.ExpectedRuntimeInstanceId, 128)
            || preparation.HostControlToken?.Length != 64)
        {
            SendApiError(response, 400, "invalid_host_preparation_contract",
                "An exact runtime instance and 256-bit process-local Host control token are required.");
            return;
        }
        try
        {
            HostShutdownAuthorization authorization = HostLifecycleControl.Authorize(
                new HostShutdownRequest(preparation.ExpectedRuntimeInstanceId, preparation.HostControlToken));
            if (!authorization.Allowed)
            {
                SendApiError(response, authorization.Status == "runtime_instance_changed" ? 409 : 403,
                    authorization.Status, "Host preparation failed closed.");
                return;
            }
            var task = RunOnMainThread(() =>
            {
                var baseline = HostProfileControl.PrepareBaseline(authorization.RuntimeInstanceId);
                var speed = baseline.Recipe != null && !baseline.Ready
                    ? HostNativeSpeedControl.Observe(authorization.RuntimeInstanceId)
                    : HostNativeSpeedControl.Prepare(authorization.RuntimeInstanceId);
                bool ready = (baseline.Recipe == null || baseline.Ready) && (speed.Requested == null || speed.Ready);
                return new HostPreparationProvenance(ready ? "host_preparation_ready" : "host_preparation_incomplete", ready, speed, baseline);
            });
            HostPreparationProvenance result = task.GetAwaiter().GetResult();
            if (!result.Ready) response.StatusCode = 409;
            SendJson(response, result);
        }
        catch (Exception exception) { SendApiInternalError(response, "host_preparation_failed", exception); }
    }

    private static void HandlePostHostShutdown(
        HttpListenerRequest request,
        HttpListenerResponse response)
    {
        HostShutdownRequest? shutdown = ReadBoundedJsonBody<HostShutdownRequest>(
            request,
            response,
            MaxHostControlBodyBytes,
            "Host lifecycle control");
        if (shutdown == null)
            return;
        if (!IsSafeProtocolIdentifier(shutdown.ExpectedRuntimeInstanceId, 128)
            || shutdown.HostControlToken?.Length != 64)
        {
            SendApiError(
                response,
                400,
                "invalid_host_shutdown_contract",
                "An exact runtime instance and 256-bit process-local Host control token are required.");
            return;
        }

        try
        {
            HostShutdownAuthorization authorization = HostLifecycleControl.Authorize(shutdown);
            if (!authorization.Allowed)
            {
                SendApiError(
                    response,
                    authorization.Status == "runtime_instance_changed" ? 409 : 403,
                    authorization.Status,
                    "Host lifecycle shutdown failed closed.");
                return;
            }
            var task = RunOnMainThread(() => HostLifecycleControl.ScheduleShutdown(authorization));
            SendJson(response, task.GetAwaiter().GetResult());
        }
        catch (Exception exception)
        {
            SendApiInternalError(response, "host_shutdown_failed", exception);
        }
    }

    private static void HandlePostHostProvenance(
        HttpListenerRequest request,
        HttpListenerResponse response)
    {
        HostShutdownRequest? provenance = ReadBoundedJsonBody<HostShutdownRequest>(
            request,
            response,
            MaxHostControlBodyBytes,
            "Host provenance control");
        if (provenance == null)
            return;
        if (!IsSafeProtocolIdentifier(provenance.ExpectedRuntimeInstanceId, 128)
            || provenance.HostControlToken?.Length != 64)
        {
            SendApiError(
                response,
                400,
                "invalid_host_provenance_contract",
                "An exact runtime instance and 256-bit process-local Host control token are required.");
            return;
        }

        try
        {
            HostShutdownAuthorization authorization = HostLifecycleControl.Authorize(provenance);
            if (!authorization.Allowed)
            {
                SendApiError(
                    response,
                    authorization.Status == "runtime_instance_changed" ? 409 : 403,
                    authorization.Status,
                    "Host provenance read failed closed.");
                return;
            }
            var task = RunOnMainThread(() => HostRunSeedControl.Observe(authorization.RuntimeInstanceId));
            SendJson(response, task.GetAwaiter().GetResult());
        }
        catch (Exception exception)
        {
            SendApiInternalError(response, "host_provenance_failed", exception);
        }
    }
}
