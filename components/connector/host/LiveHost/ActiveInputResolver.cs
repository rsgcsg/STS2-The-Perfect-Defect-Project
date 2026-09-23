using STS2Connector.NativeUi;
using System;
using System.Collections.Generic;
using System.Linq;
using Godot;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Screens.CharacterSelect;
using MegaCrit.Sts2.Core.Nodes.Screens.MainMenu;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.LiveHost.Contracts;

namespace STS2Connector.LiveHost;

internal enum InputOwnerLayer
{
    Modal,
    Overlay,
    Room,
    Menu
}

internal sealed record ActiveSurfaceSnapshot(
    IOverlayScreen? TopOverlay,
    bool MapIsOpen,
    NSubmenu? MenuSubmenu,
    NMainMenu? MenuRoot,
    IScreenContext? OpenModal,
    string SourceType)
{
    public bool HasBlockingSurface => TopOverlay != null || MapIsOpen || MenuSubmenu != null || MenuRoot != null || OpenModal != null;
}

internal sealed record ActiveSurfaceResolution(
    LiveObservation? Draft,
    IReadOnlyList<string> MatchedKinds,
    string? FailedProvider,
    Exception? Failure);

internal static class ActiveInputResolver
{
    public static ActiveSurfaceSnapshot Capture()
    {
        IOverlayScreen? candidate = NOverlayStack.Instance?.Peek();
        // The map's explicit open state wins over a rewards overlay retained
        // during the room-exit animation. This is not a strategic inference:
        // it is the game's own active player-facing screen state.
        bool mapIsOpen = NMapScreen.Instance?.IsOpen == true;
        // The overlay stack can retain a node for a frame (or during a room
        // transition) after it has left the visible UI. It must not keep
        // publishing stale actions over the new room state.
        IOverlayScreen? overlay = !mapIsOpen && IsVisibleActiveOverlay(candidate) ? candidate : null;
        NSubmenu? menuSubmenu = null;
        NMainMenu? menuRoot = null;
        IScreenContext? openModal = NModalContainer.Instance?.OpenModal;
        if (openModal is CanvasItem modalCanvas
            && (!ConnectorMod.IsLiveNode(modalCanvas) || !ConnectorMod.IsNodeVisible(modalCanvas)))
        {
            openModal = null;
        }
        if (overlay == null && !mapIsOpen && !RunManager.Instance.IsInProgress)
        {
            menuRoot = NGame.Instance?.MainMenu is { } rootMenu
                       && ConnectorMod.IsLiveNode(rootMenu)
                       && ConnectorMod.IsNodeVisible(rootMenu)
                ? rootMenu
                : null;
            NSubmenu? stackTop = NGame.Instance?.MainMenu?.SubmenuStack?.Peek();
            NCharacterSelectScreen? mountedCharacterSelect = NGame.Instance?.GetTree()?.Root is { } root
                ? ConnectorMod.FindFirst<NCharacterSelectScreen>(root)
                : null;
            bool mountedVisible = mountedCharacterSelect != null
                                  && ConnectorMod.IsLiveNode(mountedCharacterSelect)
                                  && ConnectorMod.IsNodeVisible(mountedCharacterSelect);
            if (stackTop != null && mountedVisible && !ReferenceEquals(stackTop, mountedCharacterSelect))
                throw new InvalidOperationException("Conflicting visible main-menu submenu ownership.");
            menuSubmenu = stackTop is { } current
                          && ConnectorMod.IsLiveNode(current)
                          && ConnectorMod.IsNodeVisible(current)
                ? current
                : mountedVisible
                    ? mountedCharacterSelect
                    : null;
        }
        string sourceType = overlay?.GetType().Name
            ?? (mapIsOpen ? "map_open" : RunManager.Instance.IsInProgress ? "run_without_visible_overlay" : "menu_or_no_run");
        if (openModal != null)
            sourceType = openModal.GetType().Name;
        else if (menuSubmenu != null)
            sourceType = menuSubmenu.GetType().Name;
        else if (menuRoot != null)
            sourceType = menuRoot.GetType().Name;
        return new ActiveSurfaceSnapshot(overlay, mapIsOpen, menuSubmenu, menuRoot, openModal, sourceType);
    }

    internal static bool IsVisibleActiveOverlay(IOverlayScreen? overlay) =>
        overlay is CanvasItem canvas
        && ConnectorMod.IsLiveNode(canvas)
        && ConnectorMod.IsNodeVisible(canvas);

    public static ActiveSurfaceResolution Resolve(
        ActiveSurfaceSnapshot snapshot,
        IReadOnlyList<ILiveSurfaceReader> providers,
        NativeEntityRegistry entities,
        GameBuildIdentity game,
        Func<LiveObservation?>? specializedSurface = null)
    {
        ActiveSurfaceResolution? preferred = ResolvePreferredSurface(
            snapshot.OpenModal != null,
            () => PotionPopupSurfaceReader.Capture(entities, game, snapshot),
            specializedSurface);
        if (preferred != null) return preferred;
        var matches = new List<(string Kind, LiveObservation Draft)>();
        foreach (ILiveSurfaceReader provider in providers.Where(provider =>
                     IsActiveLayer(
                         provider.Layer,
                         snapshot.TopOverlay != null,
                         snapshot.MapIsOpen,
                         snapshot.MenuSubmenu != null || snapshot.MenuRoot != null,
                         snapshot.OpenModal != null)))
        {
            try
            {
                LiveObservation? draft = provider.TryBuild(snapshot, entities, game);
                if (draft != null)
                    matches.Add((provider.Kind, draft));
            }
            catch (Exception ex)
            {
                return new ActiveSurfaceResolution(
                    null,
                    matches.Select(match => match.Kind).ToArray(),
                    provider.Kind,
                    ex);
            }
        }

        return new ActiveSurfaceResolution(
            matches.Count == 1 ? matches[0].Draft : null,
            matches.Select(match => match.Kind).ToArray(),
            null,
            null);
    }

    // Both public snapshot paths use the same precedence and failure boundary.
    // A room/selector adapter must never hide a top-bar popup or a modal.
    internal static ActiveSurfaceResolution? ResolvePreferredSurface(
        bool hasOpenModal,
        Func<LiveObservation?> popup,
        Func<LiveObservation?>? specializedSurface)
    {
        if (hasOpenModal) return null;
        string provider = "potion_popup";
        try
        {
            LiveObservation? draft = popup();
            if (draft != null)
                return new ActiveSurfaceResolution(draft, new[] { draft.Surface.Kind }, null, null);
            provider = "specialized_surface";
            draft = specializedSurface?.Invoke();
            return draft == null ? null
                : new ActiveSurfaceResolution(draft, new[] { draft.Surface.Kind }, null, null);
        }
        catch (Exception exception)
        {
            return new ActiveSurfaceResolution(null, Array.Empty<string>(), provider, exception);
        }
    }

    internal static InputOwnerLayer SelectLayer(
        bool hasVisibleOverlay,
        bool mapIsOpen,
        bool hasMenuSubmenu = false,
        bool hasOpenModal = false) =>
        hasOpenModal
            ? InputOwnerLayer.Modal
            : hasMenuSubmenu
            ? InputOwnerLayer.Menu
            : hasVisibleOverlay || mapIsOpen
                ? InputOwnerLayer.Overlay
                : InputOwnerLayer.Room;

    internal static bool IsActiveLayer(
        InputOwnerLayer providerLayer,
        bool hasVisibleOverlay,
        bool mapIsOpen,
        bool hasMenuSubmenu = false,
        bool hasOpenModal = false) =>
        providerLayer == SelectLayer(hasVisibleOverlay, mapIsOpen, hasMenuSubmenu, hasOpenModal);
}
