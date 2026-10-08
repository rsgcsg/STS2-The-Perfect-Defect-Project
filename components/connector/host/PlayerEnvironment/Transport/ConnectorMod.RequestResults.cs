using System.Net;
using STS2Connector.PlayerEnvironment;

namespace STS2Connector;

public static partial class ConnectorMod
{
    private static void SendFrozenTerminal(HttpListenerResponse response, RequestNamespace.TerminalReply terminal)
    {
        response.StatusCode = terminal.StatusCode;
        response.ContentType = "application/json; charset=utf-8";
        response.ContentLength64 = terminal.Length;
        terminal.WriteTo(response.OutputStream);
        response.Close();
    }
    private static void SendRequestLookup(HttpListenerResponse response, string status, string? reason,
        RequestNamespace.TerminalReply? terminal)
    {
        if (terminal is not null)
        { using (terminal) SendFrozenTerminal(response, terminal); return; }
        int code = status switch { "pending" => 202, "expired" => 410, "not_found" => 404,
            "capacity" => 429, "rejected" when reason is "request_capacity_exceeded" => 429,
            "rejected" when reason is "result_encoding_unsupported" => 503, _ => 409 };
        SendApiError(response, code, reason ?? status, status switch
        {
            "pending" => "The original request is queued or started; query this exact ID without resubmitting.",
            "expired" => "The original result expired. Its request ID remains spent; never retry input with this ID.",
            _ => "This request cannot acquire an original input or terminal sender."
        });
    }
}
