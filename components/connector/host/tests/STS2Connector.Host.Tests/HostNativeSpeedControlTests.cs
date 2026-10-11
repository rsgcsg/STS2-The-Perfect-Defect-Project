using System.Text.Json;
using MegaCrit.Sts2.Core.Saves;
using MegaCrit.Sts2.Core.Settings;
using STS2Connector.HostControl;

namespace STS2Connector.Host.Tests;

public sealed class HostNativeSpeedControlTests
{
    [Theory]
    [InlineData("normal", FastModeType.Normal)]
    [InlineData("fast", FastModeType.Fast)]
    [InlineData("instant", FastModeType.Instant)]
    public void ActualNativePreferenceSetterChangesOnlyFastMode(string mode, FastModeType expected)
    {
        var prefs = new PrefsSave();
        using JsonDocument before = JsonDocument.Parse(JsonSerializer.Serialize(prefs));
        HostNativeSpeedControl.ApplyTypedMode(prefs, mode);
        Assert.Equal(expected, prefs.FastMode);
        Assert.Equal(mode, HostNativeSpeedControl.ReadTypedMode(prefs.FastMode));
        using JsonDocument after = JsonDocument.Parse(JsonSerializer.Serialize(prefs));
        foreach (JsonProperty property in before.RootElement.EnumerateObject())
            if (property.Name != "fast_mode") Assert.Equal(property.Value.GetRawText(), after.RootElement.GetProperty(property.Name).GetRawText());
    }

    [Fact]
    public void ActualNativeNoneIsNotAUsableSpeed() => Assert.Null(HostNativeSpeedControl.ReadTypedMode(FastModeType.None));

    [Fact]
    public void PreparationUsesTheExistingBoundedBodyReader()
    {
        Assert.Equal(4096, ConnectorMod.MaxHostControlBodyBytes);
        Assert.Equal(4096, ConnectorMod.ReadBoundedBodyBytes(new MemoryStream(new byte[4096]),
            ConnectorMod.MaxHostControlBodyBytes)!.Length);
        Assert.Null(ConnectorMod.ReadBoundedBodyBytes(new MemoryStream(new byte[4097]), ConnectorMod.MaxHostControlBodyBytes));
    }

    [Fact]
    public void PrivatePreparationAcceptsOnlyExistingRuntimeAndTokenShape()
    {
        const string token = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
        var request = new HostPreparationRequest("runtime-1", token);
        string body = JsonSerializer.Serialize(request, ConnectorMod._jsonOptions);
        Assert.Equal(request, JsonSerializer.Deserialize<HostPreparationRequest>(body, ConnectorMod._jsonOptions));
        Assert.Throws<JsonException>(() => JsonSerializer.Deserialize<HostPreparationRequest>(
            body.TrimEnd('}') + ",\"mode\":\"fast\"}", ConnectorMod._jsonOptions));
        Assert.False(HostLifecycleControl.Authorize(token, new string('0', 64), "runtime-1", "runtime-1").Allowed);
        Assert.False(HostLifecycleControl.Authorize(token, token, "old-runtime", "runtime-1").Allowed);
    }
}
