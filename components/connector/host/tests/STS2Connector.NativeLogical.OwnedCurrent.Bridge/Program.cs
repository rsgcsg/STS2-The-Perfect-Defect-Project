using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;

// Test-only stdin transport. Links actual Store/Projector/catalog/wire sources;
// supplies synthetic public frames, never native Service, Executor, legality or game.
var limits = new NativeLogicalLimits(MaxCaptures: 4);
long now = 0; int occurrence = 1;
var store = new NativeLogicalCaptureStore(() => now, limits);
var projector = new NativeLogicalProjector(limits);
var catalogs = new Dictionary<string, string>();
var handles = new Dictionary<string, string>();
var frame = Frame(occurrence);
while (Console.ReadLine() is { } line)
{
    JsonNode response;
    try
    {
        var input = JsonNode.Parse(line)!.AsObject(); string operation = input["operation"]!.GetValue<string>();
        var body = input["body"]!.AsObject(); object value;
        switch (operation)
        {
            case "set_frame":
                // Explicit synthetic public facts for the root collector seam;
                // the linked production Projector/Store still own all encoding,
                // catalog IDs/digests, capacity and retention behavior.
                frame = JsonSerializer.Deserialize<NativeLogicalPublicFrame>(body["frame"]!.ToJsonString(), NativeLogicalWire.Options)!;
                value = new { source_fixture_replaced = true }; break;
            case "current": case "current_owned":
                var request = JsonSerializer.Deserialize<NativeLogicalCurrentRequest>(body.ToJsonString(), NativeLogicalWire.Options)!;
                var reply = operation == "current_owned"
                    ? projector.CurrentOwned(frame, request, DateTimeOffset.UtcNow, now + limits.RetentionMs, () => now, store)
                    : projector.Current(frame, request, DateTimeOffset.UtcNow, now + limits.RetentionMs, () => now, store);
                if (reply.Capture is not null)
                {
                    catalogs[store.Catalog(reply.Capture.CaptureId).Descriptor.CatalogRef] = reply.Capture.CaptureId;
                    if (reply.Retention is not null) handles[reply.Retention.RetentionHandleId] = reply.Capture.CaptureId;
                }
                value = reply; break;
            case "read":
                value = store.Read(body["capture_id"]!.GetValue<string>(), body["cursor"]!.GetValue<string>(), body["max_bytes"]!.GetValue<int>()); break;
            case "catalog":
                var catalog = store.Catalog(catalogs[body["catalog_ref"]!.GetValue<string>()]);
                JsonElement? prefix = body["prefix"] is null ? null : JsonDocument.Parse(body["prefix"]!.ToJsonString()).RootElement.Clone();
                value = catalog.List(body["stream_generation"]!.GetValue<string>(), prefix,
                    body["cursor"]?.GetValue<string>(), body["limit"]!.GetValue<int>(), body["max_page_bytes"]!.GetValue<int>()); break;
            case "retain":
                var retained = store.RetainPublic(JsonSerializer.Deserialize<NativeLogicalRetainRequest>(body.ToJsonString(), NativeLogicalWire.Options)!);
                if (retained.Retention is not null) handles[retained.Retention.RetentionHandleId] = retained.Retention.Capture.CaptureId;
                value = retained; break;
            case "release":
                var release = JsonSerializer.Deserialize<NativeLogicalReleaseRequest>(body.ToJsonString(), NativeLogicalWire.Options)!;
                value = store.ReleasePublic(release); handles.Remove(release.RetentionHandleId); break;
            case "stats":
                value = new { charged_bytes = store.ChargedBytes, charged_buffers = store.ChargedBuffers,
                    handles = handles.Count, live_captures = catalogs.Values.Distinct().Count(store.IsAvailable), now }; break;
            case "advance": occurrence++; frame = Frame(occurrence); value = new { occurrence }; break;
            case "expire": now += limits.RetentionMs; store.Sweep(); handles.Clear(); value = new { now }; break;
            default: throw new InvalidOperationException("Unknown bridge operation.");
        }
        foreach (var key in catalogs.Where(pair => !store.IsAvailable(pair.Value)).Select(pair => pair.Key).ToArray()) catalogs.Remove(key);
        response = new JsonObject { ["value"] = JsonNode.Parse(NativeLogicalWire.Encode(value)), ["error"] = null };
    }
    catch (Exception error)
    { response = new JsonObject { ["value"] = null, ["error"] = new JsonObject { ["code"] = error is NativeLogicalException n ? n.Code : "bridge_error", ["detail"] = error.Message } }; }
    Console.WriteLine(response.ToJsonString()); Console.Out.Flush();
}
static NativeLogicalPublicFrame Frame(int occurrence) => new("stream-fixture", new("runtime-fixture", "environment-fixture"),
    new("owner-fixture", occurrence.ToString(), "1", null, null), "interactive", new("persistent", new JsonObject { ["value"] = 1 }),
    new("interaction", "selector", "ready", "选择", "surface", new(new JsonObject { ["kind"] = "selector" }, new JsonObject { ["kind"] = "selector" }), Array.Empty<PlayerEnvironmentInteractionCapability>()),
    new[] { new PlayerEnvironmentReferent("public-card", "card", "card", "甲", new(true, true, false, false, "displayed"), "card", new JsonObject { ["name"] = "甲" }) },
    new("fair", "current", false, "explicit"), new[] { new NativeLogicalLeaf("pick", "甲", "public-card", Array.Empty<NativeLogicalArgument>(), "native") { BindingKey = "fixture-only" } },
    new("complete", Array.Empty<string>()));
