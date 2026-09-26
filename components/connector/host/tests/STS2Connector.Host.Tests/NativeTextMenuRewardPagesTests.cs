using System;
using STS2Connector.PlayerEnvironment;
using Xunit;

namespace STS2Connector.Host.Tests;

public sealed class NativeTextMenuRewardPagesTests
{
    [Fact]
    public void ExactReferencesRequiresEveryCurrentMemberOnceRegardlessOfOrder()
    {
        object first = new();
        object second = new();
        Assert.True(NativeTextMenuRewardPages.ExactReferences(
            new[] { first, second }, new[] { second, first }));
        Assert.False(NativeTextMenuRewardPages.ExactReferences(
            new[] { first, second }, new[] { first }));
        Assert.False(NativeTextMenuRewardPages.ExactReferences(
            new[] { first, second }, new[] { first, first }));
        Assert.False(NativeTextMenuRewardPages.ExactReferences(
            new[] { first, second }, new[] { first, new object() }));
    }
}
