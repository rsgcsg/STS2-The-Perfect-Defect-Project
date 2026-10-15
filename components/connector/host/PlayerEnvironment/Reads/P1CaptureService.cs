using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2Platform.NativeFoundation;

namespace STS2Connector.PlayerEnvironment;

internal static partial class PlayerEnvironmentService
{
    internal static P1CaptureCapabilities GetP1CaptureCapabilities() => new(
        P1CaptureContract.CapabilitiesSchema, P1CaptureContract.Profile, P1CaptureContract.Revision,
        P1CaptureContract.Scope, false, false, P1CaptureContract.NativeCatalogSemantics,
        P1CaptureContract.MaxReadInstances, P1CaptureAssembly.FullP1Missing.ToArray(), new(
            P1CaptureContract.CapabilitiesSchema, P1CaptureContract.ReadProfile,
            P1CaptureContract.Profile, P1CaptureContract.CaptureSchema, P1CaptureContract.ChunkSchema,
            P1CaptureContract.ReleaseSchema, P1CaptureContract.Route + "/current",
            P1CaptureContract.Route + "/read", P1CaptureContract.Route + "/release",
            SealedObservationContract.MaxCapsuleBytes, SealedObservationContract.MaxRetainedBytes,
            SealedObservationContract.MaxCapsules, SealedObservationContract.TtlMs,
            SealedObservationContract.DefaultChunkBytes, SealedObservationContract.MinChunkBytes,
            SealedObservationContract.MaxChunkBytes, false, P1CaptureContract.Scope));

    // Exactly one synchronous main-thread BuildSnapshot. No public Read(), await,
    // UI navigation or mutation; every subsequent consumer copies the same bytes.
    internal static SealedObservationCapture ReadCurrentP1Capture(string? expectedSnapshotId) =>
        SealedObservations.Capture(CaptureP1PublicBytes, expectedSnapshotId);

    private static FrozenPublicObservation CaptureP1PublicBytes()
    {
        using var guard = new P1CaptureGuard();
        NativeSourceInputProvider.BeforePrefix += P1CaptureGuard.InvalidateActive;
        try
        {
            P1NativeCaptureBoundary before = P1NativeCaptureBoundary.Capture();
            SnapshotBuildResult built = BuildSnapshot(requiredReadKindsForObservation: P1CaptureReadKinds);
            bool supported = P1CaptureReadKinds(built.HostObservation).Count != 0
                && built.Snapshot.Status == "interactive" && built.Snapshot.Interaction.Stage == "ready";
            var reads = new List<P1CapturedRead>();
            var coverage = new List<P1CaptureCoverage>();
            coverage.Add(new("ordinary_combat_scope", true, supported ? "available" : "unsupported",
                Array.Empty<string>(), supported ? Array.Empty<string>() : new[] { "scene_outside_c1_capture_scope" }));
            coverage.Add(new("current_public_base", true,
                built.Snapshot.Persistent != null && built.Snapshot.Completeness.Status == "complete" ? "available" : "unknown",
                Array.Empty<string>(), built.Snapshot.Completeness.Missing.ToArray()));
            coverage.Add(new("current_parent_combat_context", true,
                built.HostObservation.Context is CombatLiveContext ? "available" : "unsupported",
                Array.Empty<string>(), built.HostObservation.Context is CombatLiveContext context
                    ? context.CaptureMissing.ToArray() : new[] { "current_combat_context_unavailable" }));
            coverage.Add(new("legacy_native_action_relation", true,
                built.Snapshot.BoundActions.Status == "complete" ? "available" : "unsupported",
                Array.Empty<string>(), built.Snapshot.BoundActions.Status == "complete"
                    ? Array.Empty<string>() : new[] { "native_action_relation_incomplete" }));

            foreach (PlayerEnvironmentReadOpportunity opportunity in built.Snapshot.Reads)
            {
                // Non-C1 scenes keep every advertised instance as an explicit
                // unsupported requirement; no guessed display context or live re-read.
                if (!supported || opportunity.TargetReferentId != null)
                {
                    string id = NewP1ReadId();
                    reads.Add(new(id, opportunity.Kind, opportunity.ReadId, opportunity.TargetReferentId,
                        "unqualified_surface", null, "unsupported", opportunity.ContentSchema, null,
                        new[] { "read_display_context_outside_c1_capture_scope" }));
                    coverage.Add(new("read_instance:" + id, true, "unsupported", new[] { id },
                        new[] { "read_display_context_outside_c1_capture_scope" }));
                    continue;
                }
                PlayerEnvironmentReadResolution resolution = ResolveReadMaterialization(
                    built.Snapshot, opportunity.ReadId, built.Snapshot.SnapshotId, built.ReadBuilds);
                PlayerEnvironmentReadResult result = resolution.ErrorCode != null
                    ? new(null, resolution.ErrorCode, resolution.Detail)
                    : BuildResolvedRead(built, resolution, built.Snapshot.SnapshotId);
                string readInstance = NewP1ReadId();
                string[] missing = result.Read == null
                    ? new[] { result.ErrorCode ?? "read_materialization_missing" }
                    : result.Read.Completeness.Missing.ToArray();
                string availability = result.Read != null && result.Read.Completeness.Status == "complete"
                    && missing.Length == 0 ? "available" : "unsupported";
                reads.Add(new(readInstance, opportunity.Kind, opportunity.ReadId, null,
                    opportunity.Kind == "combat_piles" ? "combat_piles" : "deck", null,
                    availability, opportunity.ContentSchema, result.Read?.Content.DeepClone(), missing));
                coverage.Add(new("read_instance:" + readInstance, true, availability,
                    new[] { readInstance }, missing));

                if (built.ReadBuilds.TryGetValue(opportunity.Kind, out PlayerReadBuildResult? materialized))
                {
                    if (materialized.Draft?.Content is RunDeckReadContent deck)
                        foreach (VisibleCard card in deck.Cards)
                            AddP1CardDetail(reads, coverage, card, "deck", PileType.Deck, readInstance);
                    else if (materialized.Draft?.Content is CombatPilesReadContent piles)
                        foreach (CombatPileReadZone zone in piles.Zones)
                            foreach (VisibleCard card in zone.Cards)
                                AddP1CardDetail(reads, coverage, card, zone.Zone, zone.Zone switch
                                {
                                    "draw" => PileType.Draw, "discard" => PileType.Discard,
                                    "exhaust" => PileType.Exhaust,
                                    _ => throw new SealedObservationException("inconsistent_capture", "Unknown combat pile display context.")
                                }, readInstance);
                }
            }
            if (supported && built.HostObservation.Context is CombatLiveContext combat)
                foreach (VisibleCard card in combat.Player.Hand)
                    AddP1CardDetail(reads, coverage, card, "hand", PileType.Hand, null);

            foreach (string required in new[] { "run_deck", "combat_piles" })
            {
                P1CapturedRead[] instances = reads.Where(read => read.Kind == required).ToArray();
                bool available = instances.Length == 1 && instances.All(read => read.Availability == "available" && read.Missing.Count == 0);
                coverage.Add(new(required, true, available ? "available" : "unsupported",
                    instances.Select(read => read.InstanceId).ToArray(), available ? Array.Empty<string>()
                        : new[] { "required_read_not_completely_materialized" }));
            }
            var details = reads.Where(read => read.Kind == "surface_card").ToArray();
            bool allDetails = supported && details.All(read => read.Availability == "available" && read.Missing.Count == 0);
            coverage.Add(new("surface_card_instances", true, allDetails ? "available" : "unsupported",
                details.Select(read => read.InstanceId).ToArray(), allDetails ? Array.Empty<string>()
                    : new[] { "required_card_display_detail_missing" }));

            JsonObject basis = (JsonObject)(JsonSerializer.SerializeToNode(built.Snapshot, ConnectorMod._jsonOptions)
                ?? throw new SealedObservationException("capture_failed", "The native public basis did not serialize."));
            guard.RequireStable(before.Matches(P1NativeCaptureBoundary.Capture()));
            FrozenPublicObservation frozen = P1CaptureAssembly.Freeze(basis, reads, coverage, supported,
                before.Run == null ? null : Entities.GetId(before.Run, "run"), ConnectorMod._jsonOptions);
            guard.RequireStable(before.Matches(P1NativeCaptureBoundary.Capture()));
            return frozen;
        }
        finally { NativeSourceInputProvider.BeforePrefix -= P1CaptureGuard.InvalidateActive; }
    }

    private static string NewP1ReadId() => "p1-read-" + Guid.NewGuid().ToString("N");

    // Select before BuildRequiredReads, using the same typed base draft. A
    // settling/child owner must not materialize then retrospectively relabel
    // potentially unavailable information as an ordinary-combat Read.
    internal static IReadOnlyCollection<string> P1CaptureReadKinds(LiveObservation draft) =>
        draft.Surface is CombatTurnSurface && draft.Context is CombatLiveContext { IsPlayPhase: true } context
        && context.CaptureMissing.Count == 0 && draft.Readiness == "ready"
        && draft.InputOwnership.Status == "current_ui_owned" && draft.Completeness.Missing.Count == 0
        && draft.Game.Compatibility.StateObservationAllowed && draft.Game.Compatibility.ReadAllowed
            ? new[] { PlayerVisibleReadBuilder.RunDeckKind, PlayerVisibleReadBuilder.CombatPilesKind }
            : Array.Empty<string>();

    private static void AddP1CardDetail(List<P1CapturedRead> reads, List<P1CaptureCoverage> coverage,
        VisibleCard visible, string displayContext, PileType pile, string? parentRead)
    {
        if (reads.Count >= P1CaptureContract.MaxReadInstances)
            throw new SealedObservationException("too_large", "The capture exceeds the announced Read-instance limit.");
        Entities.TryResolve(visible.EntityId, out CardModel? card);
        JsonObject content = (JsonObject)(JsonSerializer.SerializeToNode(visible, ConnectorMod._jsonOptions)
            ?? throw new SealedObservationException("capture_failed", "The current public card did not serialize."));
        P1CapturedRead detail = P1CaptureAssembly.CurrentCardDetail(NewP1ReadId(), content,
            visible.EntityId, displayContext, parentRead, card == null ? null : () =>
                ConnectorMod.StripRichTextTags(card.GetDescriptionForPile(pile)).Replace("\n", " "));
        reads.Add(detail);
        coverage.Add(new("read_instance:" + detail.InstanceId, true, detail.Availability,
            new[] { detail.InstanceId }, detail.Missing));
    }

    internal static SealedObservationChunk ReadP1Capture(string captureId, string cursor, int maxBytes) =>
        SealedObservations.Read(captureId, cursor, maxBytes, SealedObservationProfile.P1Capture);
    internal static SealedObservationRelease ReleaseP1Capture(string captureId) =>
        SealedObservations.Release(captureId, SealedObservationProfile.P1Capture);

    // Private typed dependency boundary only. In particular the draw sequence is
    // used solely to reject drift; no private hash/order enters the public packet.
    // The C1 profile explicitly does not qualify every game's getter as atomic.
    private sealed record P1NativeCaptureBoundary(object? Run, object?[] Owners, object?[] Values, object[] Members)
    {
        internal static P1NativeCaptureBoundary Capture()
        {
            RunState? run = RunManager.Instance.DebugOnlyGetState();
            var player = run == null ? null : LocalContext.GetMe(run);
            var combat = CombatManager.Instance.DebugOnlyGetState();
            var pc = player?.PlayerCombatState;
            ActiveSurfaceSnapshot surface = ActiveInputResolver.Capture();
            long count = (long)(player?.Deck.Cards.Count ?? 0) + (pc?.Hand.Cards.Count ?? 0)
                + (pc?.DrawPile.Cards.Count ?? 0) + (pc?.DiscardPile.Cards.Count ?? 0) + (pc?.ExhaustPile.Cards.Count ?? 0);
            if (count + 2 > P1CaptureContract.MaxReadInstances)
                throw new SealedObservationException("too_large", "The current public card instances exceed the capture bound.");
            var members = new List<object>();
            if (player != null) members.AddRange(player.Deck.Cards);
            if (pc != null)
            {
                members.AddRange(pc.Hand.Cards); members.AddRange(pc.DrawPile.Cards);
                members.AddRange(pc.DiscardPile.Cards); members.AddRange(pc.ExhaustPile.Cards);
            }
            return new(run,
                new object?[] { run?.CurrentRoom, combat, player, pc, NPlayerHand.Instance,
                    surface.TopOverlay, surface.OpenModal, surface.MenuRoot, surface.MenuSubmenu,
                    MegaCrit.Sts2.Core.Nodes.Rooms.NCombatRoom.Instance,
                    MegaCrit.Sts2.Core.Nodes.Rooms.NCombatRoom.Instance?.Ui.EndTurnButton },
                new object?[] { surface.MapIsOpen, RunManager.Instance.IsInProgress, CombatManager.Instance.IsInProgress,
                    CombatManager.Instance.PlayerActionsDisabled, combat?.RoundNumber, combat?.CurrentSide,
                    pc?.Phase, pc?.TurnNumber, NPlayerHand.Instance?.CurrentMode, NPlayerHand.Instance?.IsInCardSelection,
                    pc?.Energy, pc?.Stars, pc?.Hand.Cards.Count, pc?.DrawPile.Cards.Count,
                    pc?.DiscardPile.Cards.Count, pc?.ExhaustPile.Cards.Count, player?.Deck.Cards.Count,
                    player?.Creature.CurrentHp, player?.Creature.MaxHp, player?.Creature.Block }, members.ToArray());
        }
        internal bool Matches(P1NativeCaptureBoundary other) => ReferenceEquals(Run, other.Run)
            && Owners.Length == other.Owners.Length && Owners.Zip(other.Owners).All(pair => ReferenceEquals(pair.First, pair.Second))
            && Values.SequenceEqual(other.Values)
            && Members.Length == other.Members.Length && Members.Zip(other.Members).All(pair => ReferenceEquals(pair.First, pair.Second));
    }
}
