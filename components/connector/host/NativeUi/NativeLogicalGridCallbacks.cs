using System;
using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;

namespace STS2Connector.NativeUi;

/// <summary>Closed exact-game input adapter. These six callbacks are the game's
/// actual HolderPressed -> OnCardClicked(CardModel) boundary, with an already
/// authorized game-owned model. No method/type/signature comes from a client.
/// Binding/current-stage admission belongs to NativeLogicalGridState.</summary>
internal static class NativeLogicalGridCallbacks
{
    internal static bool Supported(Type type) => type == typeof(NDeckCardSelectScreen)
        || type == typeof(NDeckUpgradeSelectScreen) || type == typeof(NDeckTransformSelectScreen)
        || type == typeof(NDeckEnchantSelectScreen) || type == typeof(NSimpleCardSelectScreen)
        || type == typeof(NCombatPileCardSelectScreen);

    internal static void Press(NCardGridSelectionScreen owner, CardModel card)
    {
        switch (owner)
        {
            case NDeckCardSelectScreen deck: PressDeck(deck, card); break;
            case NDeckUpgradeSelectScreen upgrade: PressUpgrade(upgrade, card); break;
            case NDeckTransformSelectScreen transform: PressTransform(transform, card); break;
            case NDeckEnchantSelectScreen enchant: PressEnchant(enchant, card); break;
            case NSimpleCardSelectScreen simple: PressSimple(simple, card); break;
            case NCombatPileCardSelectScreen pile: PressPile(pile, card); break;
            default: throw new NotSupportedException("Unregistered native logical grid callback.");
        }
    }

    // UnsafeAccessor lookup does not walk base types. Every first parameter is
    // therefore the actual declared concrete override, never the abstract base.
    [UnsafeAccessor(UnsafeAccessorKind.Method, Name = "OnCardClicked")]
    internal static extern void PressDeck(NDeckCardSelectScreen owner, CardModel card);
    [UnsafeAccessor(UnsafeAccessorKind.Method, Name = "OnCardClicked")]
    internal static extern void PressUpgrade(NDeckUpgradeSelectScreen owner, CardModel card);
    [UnsafeAccessor(UnsafeAccessorKind.Method, Name = "OnCardClicked")]
    internal static extern void PressTransform(NDeckTransformSelectScreen owner, CardModel card);
    [UnsafeAccessor(UnsafeAccessorKind.Method, Name = "OnCardClicked")]
    internal static extern void PressEnchant(NDeckEnchantSelectScreen owner, CardModel card);
    [UnsafeAccessor(UnsafeAccessorKind.Method, Name = "OnCardClicked")]
    internal static extern void PressSimple(NSimpleCardSelectScreen owner, CardModel card);
    [UnsafeAccessor(UnsafeAccessorKind.Method, Name = "OnCardClicked")]
    internal static extern void PressPile(NCombatPileCardSelectScreen owner, CardModel card);
}
