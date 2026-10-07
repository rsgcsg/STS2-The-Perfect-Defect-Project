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
    private static NativeOrbRosterResult<OrbNode> Capture(OrbNode? anchor, int capacity,
        params string[] models) => NativeOrbRoster.Capture(anchor, false, capacity, models,
            node => node.CurrentMember, node => node.Left, node => node.Right, node => node.Model);

    [Fact]
    public void NativeCurrentRingExcludesTheOutgoingFadeChildAfterEvoke()
    {
        var current = Ring("remaining-orb", null, null);
        var outgoing = new OrbNode("evoked-orb") { Left = current[1], Right = current[2] };
        var sceneChildren = new[] { outgoing }.Concat(current).ToArray();
        Assert.Equal(4, sceneChildren.Length); // Old scene-child enumeration poisoned capacity/membership.
        var result = Capture(current[0], 3, "remaining-orb");
        Assert.Null(result.Error);
        Assert.Equal(current, result.Nodes);
        Assert.DoesNotContain(outgoing, result.Nodes);
        Assert.Equal(2, result.Nodes.Count(node => node.Model == null));
    }

    [Fact]
    public void LeftNavigationIsTheNativeForwardQueueOrder()
    {
        var nodes = Ring("first", "second", null);
        var result = Capture(nodes[0], 3, "first", "second");
        Assert.Null(result.Error);
        Assert.Equal(nodes, result.Nodes);
        Assert.Equal(NativeOrbRosterError.CaptureInconsistent, Capture(nodes[0], 3, "second", "first").Error);
    }

    [Fact]
    public void ZeroSlotsRequireExactEmptyAnchorAndActualEmptyLogicalBasis()
    {
        var valid = NativeOrbRoster.Capture<OrbNode>(null, true, 0, Array.Empty<string>(),
            _ => throw new InvalidOperationException(), _ => throw new InvalidOperationException(),
            _ => throw new InvalidOperationException(), _ => throw new InvalidOperationException());
        Assert.Null(valid.Error);
        Assert.Empty(valid.Nodes);
        Assert.Equal(NativeOrbRosterError.CaptureInconsistent,
            NativeOrbRoster.Capture<OrbNode>(null, false, 0, Array.Empty<string>(), _ => true,
                node => node.Left, node => node.Right, node => node.Model).Error);
        // ClearOrbs can empty the UI while logical capacity remains: do not invent capacity zero.
        Assert.Equal(NativeOrbRosterError.CaptureInconsistent,
            NativeOrbRoster.Capture<OrbNode>(null, true, 3, Array.Empty<string>(), _ => true,
                node => node.Left, node => node.Right, node => node.Model).Error);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("single")]
    public void OneSlotSupportsBothSelfLinks(string? model)
    {
        var node = Ring(model)[0];
        var result = Capture(node, 1, model == null ? Array.Empty<string>() : new[] { model });
        Assert.Null(result.Error);
        Assert.Single(result.Nodes);
    }

    [Fact]
    public void InvalidForeignOrDeadNextNodeIsRejectedBeforeItsRightAccessor()
    {
        var nodes = Ring("first", null);
        nodes[1].CurrentMember = false;
        int rightReads = 0;
        var result = NativeOrbRoster.Capture(nodes[0], false, 2, new[] { "first" },
            node => node.CurrentMember, node => node.Left,
            node => { rightReads++; throw new InvalidOperationException("invalid native object accessor"); },
            node => node.Model);
        Assert.Equal(NativeOrbRosterError.NavigationUnresolved, result.Error);
        Assert.Equal(0, rightReads);
    }

    [Fact]
    public void InvalidCycleBrokenInverseAndUnboundedRingAreUnresolved()
    {
        var nodes = Ring("first", "second", null);
        nodes[1].Right = nodes[2];
        Assert.Equal(NativeOrbRosterError.NavigationUnresolved, Capture(nodes[0], 3, "first", "second").Error);
        nodes = Ring("first", "second", null);
        nodes[2].Left = nodes[1];
        Assert.Equal(NativeOrbRosterError.NavigationUnresolved, Capture(nodes[0], 3, "first", "second").Error);
        nodes = Ring("first", "second", null);
        Assert.Equal(NativeOrbRosterError.NavigationUnresolved,
            NativeOrbRoster.Capture(nodes[0], false, 3, new[] { "first", "second" }, _ => true,
                node => node.Left, node => node.Right, node => node.Model, maxNodes: 2).Error);
    }

    [Fact]
    public void CurrentModelMismatchCapacityMismatchAndNonemptyTailRemainInconsistent()
    {
        Assert.Equal(NativeOrbRosterError.CaptureInconsistent, Capture(Ring("not-frozen", null)[0], 2, "frozen").Error);
        Assert.Equal(NativeOrbRosterError.CaptureInconsistent, Capture(Ring("first", null)[0], 3, "first").Error);
        Assert.Equal(NativeOrbRosterError.CaptureInconsistent, Capture(Ring("first", "unexpected")[0], 2, "first").Error);
        Assert.Equal(NativeOrbRosterError.CaptureInconsistent, Capture(Ring("same", "same")[0], 2, "same", "same").Error);
    }
}
