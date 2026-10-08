using System;
using System.Collections.Generic;
using System.Reflection;
using System.Runtime.CompilerServices;
using Godot;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using STS2Connector.LiveHost;

namespace STS2Connector.NativeUi;

// Only a completed native UpdateCardDisplay certifies original -> rendered
// clone. Its private current list/index are never a public roster or operand.
internal static class NativeCardInspectionBinding
{
    internal sealed record Fact(IReadOnlyList<CardModel> List, int Index, CardModel Original,
        NCard DisplayNode, CardModel? DisplayModel, NTickbox Upgrade, bool Checked);
    internal sealed record Binding(NInspectCardScreen Owner, NativeSourceInvocation<Fact>.Ticket Ticket, Fact Source);
    private static readonly ConditionalWeakTable<NInspectCardScreen, NativeSourceInvocation<Fact>> Sources = new();
    private const BindingFlags Fields = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.DeclaredOnly;
    private static readonly FieldInfo? Cards = typeof(NInspectCardScreen).GetField("_cards", Fields);
    private static readonly FieldInfo? Index = typeof(NInspectCardScreen).GetField("_index", Fields);
    private static readonly FieldInfo? Card = typeof(NInspectCardScreen).GetField("_card", Fields);
    private static readonly FieldInfo? Upgrade = typeof(NInspectCardScreen).GetField("_upgradeTickbox", Fields);
    private static bool registered;
    internal static void Registered() => registered = true;
    private static Fact? ReadCurrent(NInspectCardScreen owner)
    {
        if (owner.GetType() != typeof(NInspectCardScreen)
            || Cards?.GetValue(owner) is not IReadOnlyList<CardModel> list
            || Index?.GetValue(owner) is not int index || index < 0 || index >= list.Count
            || list[index] is not { } original || Card?.GetValue(owner) is not NCard node
            || Upgrade?.GetValue(owner) is not NTickbox upgrade) return null;
        return new(list, index, original, node, node.Model, upgrade, upgrade.IsTicked);
    }
    internal static NativeSourceInvocation<Fact>.Ticket? DisplayEntering(NInspectCardScreen owner)
    {
        var source = Sources.GetValue(owner, static _ => new());
        if (ReadCurrent(owner) is not { } current) { source.Invalidate(); return null; }
        return source.Begin(current);
    }
    internal static void DisplayReturned(NInspectCardScreen owner, NativeSourceInvocation<Fact>.Ticket? ticket)
    {
        var source = Sources.GetValue(owner, static _ => new());
        if (ticket != null && ReadCurrent(owner) is { } current && CanComplete(ticket.Fact, current))
            source.Complete(ticket, current);
        else source.Invalidate(ticket);
    }
    internal static void DisplayFinalized(NInspectCardScreen owner, NativeSourceInvocation<Fact>.Ticket? ticket, Exception? exception) =>
        Sources.GetValue(owner, static _ => new()).Finalized(ticket, exception);
    internal static void Invalidate(NInspectCardScreen owner)
    {
        if (Sources.TryGetValue(owner, out var source)) source.Invalidate();
    }
    internal static bool SameSource(Fact a, Fact b) => ReferenceEquals(a.List, b.List)
        && a.Index == b.Index && ReferenceEquals(a.Original, b.Original)
        && ReferenceEquals(a.DisplayNode, b.DisplayNode) && ReferenceEquals(a.Upgrade, b.Upgrade);
    internal static bool SameDisplay(Fact a, Fact b) => SameSource(a, b)
        && ReferenceEquals(a.DisplayModel, b.DisplayModel) && a.Checked == b.Checked;
    internal static bool CanComplete(Fact entered, Fact returned) => SameSource(entered, returned)
        && entered.Checked == returned.Checked && returned.DisplayModel != null
        && !ReferenceEquals(returned.Original, returned.DisplayModel);
    internal static Binding? Capture(NInspectCardScreen owner, NCard exactDisplay)
    {
        if (!registered || !Sources.TryGetValue(owner, out var source) || source.Completed is not { } ticket
            || !ConnectorMod.IsLiveNode(owner) || !ConnectorMod.IsNodeVisible(owner)
            || !ActiveScreenContext.Instance.IsCurrent(owner)
            || !ReferenceEquals(exactDisplay, ticket.Fact.DisplayNode)
            || ReadCurrent(owner) is not { DisplayModel: not null } current || !SameDisplay(ticket.Fact, current)) return null;
        return new(owner, ticket, current);
    }
}
