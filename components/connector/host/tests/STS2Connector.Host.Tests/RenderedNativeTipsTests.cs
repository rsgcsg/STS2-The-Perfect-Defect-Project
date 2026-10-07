using System.Text.Json.Nodes;
using STS2Connector.PlayerEnvironment;
using Xunit;

namespace STS2Connector;

public sealed class RenderedNativeTipsTests
{
    [Fact]
    public void LegitimateEmptyNativeTipContainersProduceTypedEmptyContent()
    {
        JsonNode? content = RenderedNativeTips.Capture(Array.Empty<int>(), Array.Empty<int>(),
            _ => throw new InvalidOperationException("No child or card stats may be read"),
            _ => throw new InvalidOperationException("No child or card stats may be read"));
        Assert.NotNull(content);
        Assert.Empty(content!["text_tips"]!.AsArray());
        Assert.Empty(content["card_previews"]!.AsArray());
        Assert.Equal("{\"text_tips\":[],\"card_previews\":[]}", content.ToJsonString());
    }

    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public void MissingRequiredContainerIsNotAnEmptyTip(bool missingText)
    {
        IEnumerable<int>? text = missingText ? null : Array.Empty<int>();
        IEnumerable<int>? cards = missingText ? Array.Empty<int>() : null;
        Assert.Null(RenderedNativeTips.Capture(text, cards,
            _ => new JsonObject(), _ => new JsonObject()));
    }

    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public void AHiddenMalformedOrUnreadyActualChildKeepsTheSetUnresolved(bool badText)
    {
        Assert.Null(RenderedNativeTips.Capture(badText ? new[] { 1, 2 } : Array.Empty<int>(),
            badText ? Array.Empty<int>() : new[] { 1, 2 },
            child => child == 2 ? null : new JsonObject { ["description"] = "actual rendered text" },
            child => child == 2 ? null : new JsonObject { ["title"] = "actual rendered card" }));
    }

    [Fact]
    public void NonemptyRenderedChildrenRetainTheirValuesWithoutInventedContent()
    {
        var text = new JsonObject { ["title"] = "Block", ["description"] = "actual rendered description" };
        var card = new JsonObject { ["title"] = "Preview", ["cost"] = "1", ["description"] = "actual rendered preview" };
        var content = RenderedNativeTips.Capture(new[] { 1 }, new[] { 2 }, _ => text, _ => card)!;
        text["description"] = "later mutation";
        Assert.Equal("actual rendered description", content["text_tips"]![0]!["description"]!.GetValue<string>());
        Assert.Equal("actual rendered preview", content["card_previews"]![0]!["description"]!.GetValue<string>());
    }
}
