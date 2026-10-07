using STS2Connector.PlayerEnvironment;
using Xunit;

namespace STS2Connector;

public sealed class NativeOrbRosterTests
{
    private sealed class OrbNode(string? model = null)
    {
        internal string? Model = model;
        internal OrbNode? Left;
        internal OrbNode? Right;
        internal bool CurrentMember = true;
    }
    private static OrbNode[] Ring(params string?[] models)
    {
        var nodes = models.Select(model => new OrbNode(model)).ToArray();
        for (int index = 0; index < nodes.Length; index++)
        {
            nodes[index].Left = nodes[(index + 1) % nodes.Length];
            nodes[index].Right = nodes[(index + nodes.Length - 1) % nodes.Length];
        }
        return nodes;
    }
    private static NativeOrbRosterResult<OrbNode> Capture(OrbNode? anchor) => NativeOrbRoster.Capture(anchor,
        false, node => node.CurrentMember, node => node.Left, node => node.Right);

    [Fact]
    public void NativeCurrentRingExcludesTheOutgoingFadeChildAfterEvoke()
    {
        var current = Ring("remaining-orb", null, null);
        var outgoing = new OrbNode("evoked-orb") { Left = current[1], Right = current[2] };
        Assert.Equal(4, new[] { outgoing }.Concat(current).Count());
        var result = Capture(current[0]);
        Assert.Null(result.Error);
        Assert.Equal(current, result.Nodes);
        Assert.DoesNotContain(outgoing, result.Nodes);
    }

    [Fact]
    public void LogicalChannelBeforeSmallWaitDoesNotEraseCurrentEmptyUiSlot()
    {
        var current = Ring("old-orb", null, null);
        var logical = new List<string> { "old-orb", "newly-channelled" };
        // Exact OrbQueue.TryEnqueue adds the new model before SmallWait;
        // OrbCmd.Channel calls AddOrbAnim only after that await returns.
        var result = Capture(current[0]);
        Assert.Null(result.Error);
        Assert.Equal(2, logical.Count);
        Assert.Single(result.Nodes, node => node.Model != null);
        Assert.Equal(2, result.Nodes.Count(node => node.Model == null));
    }

    [Fact]
    public void CurrentUiOrderAndCapacityAreNotReconstructedFromLogicalQueue()
    {
        var current = Ring("second", "first", null, null);
        var logical = new[] { "first", "second" };
        var result = Capture(current[0]);
        Assert.Null(result.Error);
        Assert.Equal(4, result.Nodes.Count);
        Assert.Equal(new[] { "second", "first" }, result.Nodes.Where(node => node.Model != null).Select(node => node.Model));
        Assert.NotEqual(logical, result.Nodes.Where(node => node.Model != null).Select(node => node.Model).ToArray());
    }

    [Fact]
    public void ExactHitboxAnchorProvesEmptyUiIndependentlyOfLogicalCapacity()
    {
        var valid = NativeOrbRoster.Capture<OrbNode>(null, true,
            _ => throw new InvalidOperationException(), _ => throw new InvalidOperationException(),
            _ => throw new InvalidOperationException());
        Assert.Null(valid.Error);
        Assert.Empty(valid.Nodes);
        Assert.Equal(NativeOrbRosterError.NavigationUnresolved,
            NativeOrbRoster.Capture<OrbNode>(null, false, _ => true, node => node.Left, node => node.Right).Error);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("single")]
    public void OneSlotSupportsBothSelfLinks(string? model)
    {
        var result = Capture(Ring(model)[0]);
        Assert.Null(result.Error);
        Assert.Single(result.Nodes);
    }

    [Fact]
    public void InvalidForeignOrDeadNextNodeIsRejectedBeforeItsRightAccessor()
    {
        var nodes = Ring("first", null);
        nodes[1].CurrentMember = false;
        int rightReads = 0;
        var result = NativeOrbRoster.Capture(nodes[0], false,
            node => node.CurrentMember, node => node.Left,
            node => { rightReads++; throw new InvalidOperationException("invalid native object accessor"); });
        Assert.Equal(NativeOrbRosterError.NavigationUnresolved, result.Error);
        Assert.Equal(0, rightReads);
    }

    [Fact]
    public void InvalidCycleBrokenInverseAndUnboundedRingRemainUnresolved()
    {
        var nodes = Ring("first", "second", null);
        nodes[1].Right = nodes[2];
        Assert.Equal(NativeOrbRosterError.NavigationUnresolved, Capture(nodes[0]).Error);
        nodes = Ring("first", "second", null);
        nodes[2].Left = nodes[1];
        Assert.Equal(NativeOrbRosterError.NavigationUnresolved, Capture(nodes[0]).Error);
        nodes = Ring("first", "second", null);
        Assert.Equal(NativeOrbRosterError.NavigationUnresolved,
            NativeOrbRoster.Capture(nodes[0], false, _ => true,
                node => node.Left, node => node.Right, maxNodes: 2).Error);
    }
}
