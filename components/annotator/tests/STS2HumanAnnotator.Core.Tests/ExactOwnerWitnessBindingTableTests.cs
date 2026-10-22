using STS2HumanAnnotator.Core;
using Xunit;

namespace STS2HumanAnnotator.Core.Tests;

public sealed class ExactOwnerWitnessBindingTableTests
{
    [Fact]
    public void SessionResetReleasesVictoryCarrierOnSurvivingRunManagerWithoutAcceptingLateOldRoot()
    {
        var table = new ExactOwnerWitnessBindingTable<object>();
        var manager = new object(); var finalVote = new object(); var nextVote = new object();
        Assert.True(table.TryBind(finalVote, "victory-ready"));
        Assert.True(table.TryTransfer(finalVote, manager, "victory-ready"));
        // Victory has no ActEntered: the recording's close/reset owns cleanup.
        table.Clear();
        Assert.False(table.Contains(manager));
        Assert.False(table.TryGetOwner("victory-ready", out _));
        Assert.True(table.TryBind(nextVote, "new-run-ready"));
        Assert.True(table.TryTransfer(nextVote, manager, "new-run-ready"));
        Assert.False(table.TakeIfMatches(manager, "victory-ready"));
        Assert.True(table.TryGetWitness(manager, out var root));
        Assert.Equal("new-run-ready", root);
        Assert.True(table.TakeIfMatches(manager, "new-run-ready"));
    }

    [Fact]
    public void NonGameOwnerCannotReplaceAnotherWitnessOnSameObject()
    {
        var table = new ExactOwnerWitnessBindingTable<object>();
        var owner = new object();
        Assert.True(table.TryBind(owner, "root-a"));
        Assert.False(table.TryBind(owner, "root-b"));
        Assert.True(table.TryGetWitness(owner, out string? witness));
        Assert.Equal("root-a", witness);
    }

    [Fact]
    public void SameWitnessCannotBindTwoAliveOwners()
    {
        var table = new ExactOwnerWitnessBindingTable<object>();
        var first = new object();
        var second = new object();
        Assert.True(table.TryBind(first, "root-a"));
        Assert.False(table.TryBind(second, "root-a"));
        Assert.True(table.TryGetOwner("root-a", out object? owner));
        Assert.Same(first, owner);
    }

    [Fact]
    public void ExactPairRemovalCannotConsumeAnotherCarrier()
    {
        var table = new ExactOwnerWitnessBindingTable<object>();
        var owner = new object();
        Assert.True(table.TryBind(owner, "root-a"));
        Assert.False(table.TakeIfMatches(owner, "root-b"));
        Assert.True(table.Contains(owner));
        Assert.True(table.TakeIfMatches(owner, "root-a"));
        Assert.False(table.TryGetOwner("root-a", out _));
    }

    [Fact]
    public void ExactTransferMovesOneWitnessWithoutAConsumptionGap()
    {
        var table = new ExactOwnerWitnessBindingTable<object>();
        var source = new object();
        var destination = new object();
        Assert.True(table.TryBind(source, "root-a"));

        Assert.True(table.TryTransfer(source, destination, "root-a"));
        Assert.False(table.Contains(source));
        Assert.True(table.TryGetWitness(destination, out string? witness));
        Assert.Equal("root-a", witness);
        Assert.True(table.TryGetOwner("root-a", out object? owner));
        Assert.Same(destination, owner);
    }

    [Fact]
    public void FailedTransferRetainsExactSourceCarrier()
    {
        var table = new ExactOwnerWitnessBindingTable<object>();
        var source = new object();
        var occupied = new object();
        Assert.True(table.TryBind(source, "root-a"));
        Assert.True(table.TryBind(occupied, "root-b"));

        Assert.False(table.TryTransfer(source, occupied, "root-a"));
        Assert.True(table.TryGetWitness(source, out string? witness));
        Assert.Equal("root-a", witness);
    }
    [Fact]
    public void RewardScreenVoteRunManagerLifecyclePreservesSingleExactCarrier()
    {
        var table = new ExactOwnerWitnessBindingTable<object>();
        var rewardScreen = new object();
        var vote = new object();
        var runManager = new object();
        Assert.True(table.TryBind(rewardScreen, "ready"));
        // Production Prefix used to rebind here: the screen still owned ready.
        Assert.False(table.TryBind(vote, "ready"));
        Assert.True(table.TryTransfer(rewardScreen, vote, "ready"));
        Assert.True(table.TryGetWitness(vote, out var witness));
        Assert.Equal("ready", witness);
        Assert.False(table.TryTransfer(rewardScreen, new object(), "ready"));
        Assert.True(table.TryTransfer(vote, runManager, "ready"));
        Assert.False(table.TakeIfMatches(rewardScreen, "ready"));
        Assert.False(table.TakeIfMatches(vote, "ready"));
        Assert.True(table.TakeIfMatches(runManager, "ready"));
        Assert.False(table.TryGetOwner("ready", out _));
    }
}
