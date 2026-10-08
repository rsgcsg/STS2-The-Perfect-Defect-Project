using STS2Connector.NativeUi;

namespace STS2Connector.Tests;

public sealed class NativeEntityRegistryTests
{
    [Fact]
    public void AliasSuffixUsesOnlyEntropyRegardlessOfPriorHiddenEnumeration()
    {
        byte[] targetEntropy = Enumerable.Repeat((byte)0xee, 24).ToArray();
        var baseline = new NativeEntityRegistry(() => targetEntropy);
        int allocation = 0;
        var afterHidden = new NativeEntityRegistry(() => ++allocation <= 17
            ? Enumerable.Repeat((byte)allocation, 24).ToArray() : targetEntropy);
        object[] hidden = Enumerable.Range(0, 17).Select(_ => new object()).ToArray();
        foreach (object item in hidden.Reverse()) afterHidden.GetId(item, "card");

        string expected = "card_" + Convert.ToHexString(targetEntropy).ToLowerInvariant();
        Assert.Equal(expected, baseline.GetId(new object(), "card"));
        Assert.Equal(expected, afterHidden.GetId(new object(), "card"));
        GC.KeepAlive(hidden);
    }

    [Fact]
    public void ReorderedEnumerationKeepsExactAliasesAndDoesNotEncodeFirstAllocationOrder()
    {
        byte[][] entropy = { Enumerable.Repeat((byte)0xff, 24).ToArray(), new byte[24],
            Enumerable.Repeat((byte)0x80, 24).ToArray() };
        int next = 0;
        var registry = new NativeEntityRegistry(() => entropy[next++]);
        object[] cards = { new(), new(), new() };
        string[] before = cards.Select(card => registry.GetId(card, "card")).ToArray();
        string[] reverse = cards.Reverse().Select(card => registry.GetId(card, "card")).ToArray();
        Assert.Equal(before.Reverse(), reverse);
        Assert.Equal(new[] { before[1], before[2], before[0] }, before.Order(StringComparer.Ordinal));
        Assert.All(cards, card => Assert.Same(card,
            registry.CaptureExactReferences(new[] { registry.GetId(card, "card") }).Single().Value));
        Assert.Equal(3, next);
    }

    [Fact]
    public void DifferentRegistriesHaveIndependentAliasesAndDiagnosticReadDoesNotAllocate()
    {
        var first = new NativeEntityRegistry();
        var second = new NativeEntityRegistry();
        object entity = new();
        Assert.False(first.TryGetExistingId(entity, out _));
        Assert.Equal(0, first.TrackedReferenceCount);
        string id = first.GetId(entity, "card");
        Assert.Matches("^card_[0-9a-f]{48}$", id);
        Assert.NotEqual(id, second.GetId(entity, "card"));
        Assert.True(first.TryGetExistingId(entity, out string? existing));
        Assert.Equal(id, existing);
        Assert.Equal(id, first.GetId(entity, "different_kind"));
        Assert.Equal(1, first.TrackedReferenceCount);
    }

    [Fact]
    public void RepeatedEntropyNeverAliasesTwoExactNativeObjects()
    {
        var registry = new NativeEntityRegistry(() => new byte[24]);
        object first = new();
        string id = registry.GetId(first, "card");
        Assert.Throws<InvalidOperationException>(() => registry.GetId(new object(), "card"));
        Assert.True(registry.TryResolve<object>(id, out object? resolved));
        Assert.Same(first, resolved);
        Assert.Equal(1, registry.TrackedReferenceCount);
    }

    [Fact]
    public void ExactObjectCanBeResolvedByItsStableEntityId()
    {
        var registry = new NativeEntityRegistry();
        var entity = new object();

        string id = registry.GetId(entity, "card");

        Assert.True(registry.TryResolve<object>(id, out object? resolved));
        Assert.Same(entity, resolved);
        Assert.Equal(id, registry.GetId(entity, "card"));
    }

    [Fact]
    public void UnknownOrWrongTypedEntityFailsClosed()
    {
        var registry = new NativeEntityRegistry();
        var entity = new object();
        string id = registry.GetId(entity, "card");

        Assert.False(registry.TryResolve<string>(id, out _));
        Assert.True(registry.TryResolve<object>(id, out object? resolved));
        Assert.Same(entity, resolved);
        Assert.False(registry.TryResolve<object>("card_missing_1", out _));
    }

    [Fact]
    public void PruningRetainsLiveReferencesAndRemovesDeadEntries()
    {
        var registry = new NativeEntityRegistry();
        object live = new();
        string liveId = registry.GetId(live, "card");
        int before = registry.TrackedReferenceCount;

        string deadId = AddShortLivedEntity(registry);
        ForceCollection();

        Assert.True(registry.TryResolve<object>(liveId, out object? resolved));
        Assert.Same(live, resolved);
        Assert.True(registry.PruneDeadEntries() >= 1);
        Assert.False(registry.TryResolve<object>(deadId, out _));
        Assert.Equal(before, registry.TrackedReferenceCount);
    }

    private static string AddShortLivedEntity(NativeEntityRegistry registry)
    {
        object entity = new();
        return registry.GetId(entity, "temporary");
    }

    private static void ForceCollection()
    {
        GC.Collect();
        GC.WaitForPendingFinalizers();
        GC.Collect();
    }
}
