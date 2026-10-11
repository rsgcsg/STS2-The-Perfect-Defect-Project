using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text.Json.Nodes;
using Godot;
using MegaCrit.Sts2.Core.Entities.CardRewardAlternatives;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Models.Cards;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Potions;
using MegaCrit.Sts2.Core.Nodes.Relics;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.Shops;
using MegaCrit.Sts2.Core.Rooms;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector;

public sealed class NativeRewardMerchantInformationTests
{
    private static T Bare<T>() where T : class => (T)RuntimeHelpers.GetUninitializedObject(typeof(T));
    private static NativeCardRewardInformation Reward(Task? task = null) => new(
        Bare<NCardRewardSelectionScreen>(), Bare<Control>(), Bare<Control>(), task ?? new TaskCompletionSource<int?>().Task,
        new[] { new NativeRewardInformationCard(Bare<NGridCardHolder>(), Bare<NCard>(), Bare<Control>(), new DefendDefect(), true, true) },
        new[] { new NativeRewardInformationAlternative(Bare<NCardRewardAlternativeButton>(), Bare<CardRewardAlternative>(), "Skip", true) });

    [Fact]
    public void OpeningGuardDisablesInspectButNotExactNativeFocus()
    {
        var reward = Reward();
        var card = reward.Cards[0];
        var opening = reward with { Cards = new[] { card with { Clickable = false } } };
        Assert.True(reward.Allows(opening, card, inspect: false));
        Assert.False(reward.Allows(opening, card, inspect: true));
        Assert.True(reward.Allows(reward, card, inspect: true));
        Assert.True(opening.Alternatives[0].Enabled);
        // Compatibility readiness remains untouched by native-logical composition.
        Assert.Equal("settling", CardRewardSurfaceReader.ClassifyReadiness(1, 0, 1, 1));
    }

    [Theory]
    [InlineData("owner")]
    [InlineData("row")]
    [InlineData("container")]
    [InlineData("task")]
    [InlineData("holder")]
    [InlineData("node")]
    [InlineData("hitbox")]
    [InlineData("model")]
    [InlineData("alternative")]
    [InlineData("alternative_button")]
    [InlineData("label")]
    [InlineData("missing")]
    public void RewardOccurrenceChangeRejectsBeforeInput(string changed)
    {
        var reward = Reward();
        var card = reward.Cards[0];
        var alt = reward.Alternatives[0];
        var current = changed switch
        {
            "owner" => reward with { Owner = Bare<NCardRewardSelectionScreen>() },
            "row" => reward with { Row = Bare<Control>() },
            "container" => reward with { AlternativeContainer = Bare<Control>() },
            "task" => reward with { Completion = new TaskCompletionSource<int?>().Task },
            "holder" => reward with { Cards = new[] { card with { Holder = Bare<NGridCardHolder>() } } },
            "node" => reward with { Cards = new[] { card with { Node = Bare<NCard>() } } },
            "hitbox" => reward with { Cards = new[] { card with { Hitbox = Bare<Control>() } } },
            "model" => reward with { Cards = new[] { card with { Model = new DefendDefect() } } },
            "alternative" => reward with { Alternatives = new[] { alt with { Model = Bare<CardRewardAlternative>() } } },
            "alternative_button" => reward with { Alternatives = new[] { alt with { Button = Bare<NCardRewardAlternativeButton>() } } },
            "label" => reward with { Alternatives = new[] { alt with { Label = "Reroll" } } },
            _ => reward with { Cards = Array.Empty<NativeRewardInformationCard>() }
        };
        int calls = 0;
        var result = NativeInformationInput.Dispatch(() => reward.Allows(current, card, inspect: true), () => calls++, "inspect");
        Assert.Equal(0, calls);
        Assert.Equal(LegacyNativeInputDisposition.NotDelivered, result.LegacyDisposition);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void RewardCompletionOrCancellationClosesFocusAndInspection(bool cancelled)
    {
        var completion = new TaskCompletionSource<int?>();
        var reward = Reward(completion.Task);
        if (cancelled) completion.SetCanceled(); else completion.SetResult(0);
        Assert.False(reward.Allows(reward, reward.Cards[0], inspect: false));
        Assert.False(reward.Allows(reward, reward.Cards[0], inspect: true));
    }

    private static NativeMerchantInformation Merchant(NativeMerchantInformationKind kind)
    {
        NativeMerchantInformationEntry entry = kind switch
        {
            NativeMerchantInformationKind.Card => new(Bare<MerchantCardEntry>(), Bare<NMerchantCard>(), Bare<Control>(), Bare<NCard>(), new object(), kind, true),
            NativeMerchantInformationKind.Relic => new(Bare<MerchantRelicEntry>(), Bare<NMerchantRelic>(), Bare<Control>(), Bare<NRelic>(), new object(), kind, true),
            _ => new(Bare<MerchantPotionEntry>(), Bare<NMerchantPotion>(), Bare<Control>(), Bare<NPotion>(), new object(), kind, true)
        };
        return new(Bare<MerchantRoom>(), Bare<NMerchantRoom>(), Bare<MerchantInventory>(), Bare<NMerchantInventory>(),
            Bare<NBackButton>(), Bare<Control>(), false, new[] { entry });
    }

    [Theory]
    [InlineData("Card", true)]
    [InlineData("Relic", true)]
    [InlineData("Potion", false)]
    public void MerchantTypedCapabilityDoesNotInventPotionInspection(string kind, bool inspect)
    {
        var merchant = Merchant(Enum.Parse<NativeMerchantInformationKind>(kind));
        var entry = merchant.Entries[0];
        Assert.True(merchant.Allows(merchant, entry, inspect: false));
        Assert.Equal(inspect, merchant.Allows(merchant, entry, inspect: true));
        Assert.False(merchant.Allows(merchant with { Entries = new[] { entry with { Enabled = false } } }, entry, inspect: false));
    }

    [Theory]
    [InlineData("inventory")]
    [InlineData("room")]
    [InlineData("owner")]
    [InlineData("slot")]
    [InlineData("entry")]
    [InlineData("hitbox")]
    [InlineData("display")]
    [InlineData("model")]
    [InlineData("back")]
    [InlineData("blocker")]
    [InlineData("blocked")]
    [InlineData("sold")]
    public void MerchantOwnerStockOrRenderedModelChangeRejectsStaleInput(string changed)
    {
        var merchant = Merchant(NativeMerchantInformationKind.Card);
        var entry = merchant.Entries[0];
        var current = changed switch
        {
            "back" => merchant with { Back = Bare<NBackButton>() },
            "blocker" => merchant with { InputBlocker = Bare<Control>() },
            "blocked" => merchant with { InputBlocked = true },
            "inventory" => merchant with { Inventory = Bare<MerchantInventory>() },
            "room" => merchant with { Room = Bare<MerchantRoom>() },
            "owner" => merchant with { Owner = Bare<NMerchantInventory>() },
            "slot" => merchant with { Entries = new[] { entry with { Slot = Bare<NMerchantCard>() } } },
            "entry" => merchant with { Entries = new[] { entry with { Entry = Bare<MerchantCardEntry>() } } },
            "hitbox" => merchant with { Entries = new[] { entry with { Hitbox = Bare<Control>() } } },
            "display" => merchant with { Entries = new[] { entry with { Display = Bare<NCard>() } } },
            "model" => merchant with { Entries = new[] { entry with { Model = new object() } } },
            _ => merchant with { Entries = Array.Empty<NativeMerchantInformationEntry>() }
        };
        int calls = 0;
        var result = NativeInformationInput.Dispatch(() => merchant.Allows(current, entry, inspect: true), () => calls++, "inspect");
        Assert.Equal(0, calls);
        Assert.False(result.Accepted);
    }

    [Theory]
    [InlineData(false, true, (int)Control.MouseFilterEnum.Ignore, true)]
    [InlineData(true, false, (int)Control.MouseFilterEnum.Stop, true)]
    [InlineData(false, false, (int)Control.MouseFilterEnum.Ignore, false)]
    [InlineData(true, true, (int)Control.MouseFilterEnum.Stop, false)]
    [InlineData(false, true, (int)Control.MouseFilterEnum.Stop, false)]
    [InlineData(true, false, (int)Control.MouseFilterEnum.Ignore, false)]
    [InlineData(false, true, (int)Control.MouseFilterEnum.Pass, false)]
    public void MerchantOpeningAndBlockInputRetainTheNativeControlTuple(bool blocked, bool back, int mouseFilter, bool coherent)
    {
        Assert.Equal(coherent, NativeMerchantInformation.ControlsCoherent(blocked, back, (Control.MouseFilterEnum)mouseFilter));
        var merchant = Merchant(NativeMerchantInformationKind.Card);
        // A native blocker closes input even when the stock slot itself stayed enabled.
        Assert.False(merchant.Allows(merchant with { InputBlocked = true }, merchant.Entries[0], inspect: false));
        Assert.False(merchant.Allows(merchant with { InputBlocked = true }, merchant.Entries[0], inspect: true));
    }

    [Fact]
    public void RepeatedNativeFocusReusesOnlyTheSameCurrentSourceEntryAndRenderedSet()
    {
        object source = new(), set = new();
        Assert.True(NativeTipReturn.CanReuse(source, source, NativeTipEntry.Focus, NativeTipEntry.Focus, set, set, true, true));
        Assert.False(NativeTipReturn.CanReuse(new object(), source, NativeTipEntry.Focus, NativeTipEntry.Focus, set, set, true, true));
        Assert.False(NativeTipReturn.CanReuse(source, source, NativeTipEntry.Mouse, NativeTipEntry.Focus, set, set, true, true));
        Assert.False(NativeTipReturn.CanReuse(source, source, NativeTipEntry.Focus, NativeTipEntry.Focus, new object(), set, true, true));
        Assert.False(NativeTipReturn.CanReuse(source, source, NativeTipEntry.Focus, NativeTipEntry.Focus, set, set, false, true));
        Assert.False(NativeTipReturn.CanReuse(source, source, NativeTipEntry.Focus, NativeTipEntry.Focus, set, set, true, false));
    }

    [Fact]
    public void CallbackExceptionAfterEntryIsUnknownAndIsNotReplayed()
    {
        int calls = 0;
        var result = NativeInformationInput.Dispatch(() => true, () => { calls++; throw new InvalidOperationException(); }, "inspect");
        Assert.Equal(1, calls);
        Assert.Equal(NativeInputDelivery.Unknown, result.Delivery);
        Assert.Equal(LegacyNativeInputDisposition.Unknown, result.LegacyDisposition);
    }

    [Fact]
    public void ExactGameAbiRetainsPendingRewardAndClosedMerchantDisplayBindings()
    {
        const BindingFlags fields = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.DeclaredOnly;
        Assert.Equal(typeof(TaskCompletionSource<int?>), typeof(NCardRewardSelectionScreen).GetField("_completionSource", fields)!.FieldType);
        Assert.Equal(typeof(bool), typeof(NCardHolder).GetField("_isClickable", fields)!.FieldType);
        Assert.Equal(typeof(bool), typeof(NMerchantInventory).GetField("_isInputBlocked", fields)!.FieldType);
        Assert.Equal(typeof(NCard), typeof(NMerchantCard).GetField("_cardNode", fields)!.FieldType);
        Assert.Equal(typeof(NRelic), typeof(NMerchantRelic).GetField("_relicNode", fields)!.FieldType);
        Assert.Equal(typeof(NPotion), typeof(NMerchantPotion).GetField("_potionNode", fields)!.FieldType);
        Assert.Equal(typeof(NMerchantSlot), typeof(NMerchantCard).GetMethod("_GuiInput")!.DeclaringType);
        Assert.NotNull(typeof(NMerchantCard).GetMethod("OnPreview", fields));
        Assert.NotNull(typeof(NMerchantRelic).GetMethod("OnPreview", fields));
        Assert.Null(typeof(NMerchantPotion).GetMethod("OnPreview", fields));
    }

    private static PlayerEnvironmentSnapshot Page(JsonNode surface, string status = "interactive")
    {
        return new("1.0.0", PlayerEnvironmentContract.SnapshotSchema, "snapshot", 1, DateTimeOffset.UnixEpoch, status, null,
            new("interaction", "card_reward_selection", status, null, "surface", new(surface, new JsonObject()),
                Array.Empty<PlayerEnvironmentInteractionCapability>()),
            PlayerEnvironmentService.ProjectFactReferents(surface).Values.ToArray(),
            new("sts2.player-environment/bound-actions-1", "complete", 0, 0, 512, "native", Array.Empty<PlayerEnvironmentBoundAction>()),
            Array.Empty<PlayerEnvironmentReadOpportunity>(), new("complete", "visible", "exact", Array.Empty<string>(), Array.Empty<string>()),
            new("runtime", "environment"), new("player_visible_v1", "current_page", false, "omit"));
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void RewardCompositionRetainsAllAlternativesAndInformationAlongsideCurrentSelections(bool opening)
    {
        var surface = new CardRewardSelectionSurface("card_reward_selection", "screen",
            new[] { new VisibleCard("card", "DEFEND", "Defend", "Skill", "1", null, "Block", "Basic", false, false, null) },
            new[] { new VisibleCardRewardAlternative("skip", 0, "Skip", true), new VisibleCardRewardAlternative("reroll", 1, "Reroll", true) })
        { SelectableCardEntityIds = opening ? Array.Empty<string>() : new[] { "card" } };
        var observation = new LiveObservation("source", opening ? "settling" : "ready", new RewardFlowLiveContext("reward_flow", "card_reward"),
            surface, new("contract_complete_for_card_reward_selection", "exact", Array.Empty<string>(), Array.Empty<string>()),
            new("v", "commit", null, null, new("test", true, true, false, "fixture")), Array.Empty<string>());
        var content = PlayerEnvironmentService.ProjectVisibleFacts(surface, observation.Context!);
        var page = Page(content.Surface, opening ? "settling" : "interactive");
        string[] additional = { "focus", "topbar", "potion" };
        var infos = additional.Select(key => new TextMenuLeaf(key, "root", key, key, null, Array.Empty<PlayerEnvironmentBoundActionArgument>(),
            () => NativeInputResult.Delivered(key))).ToArray();
        int dispatch = 0;
        bool current = true;
        var frame = NativeLogicalCardRewardCapture.Compose(observation, surface, page, infos, () => current,
            _ => { dispatch++; return NativeInputResult.Delivered("legacy reward"); }, "owner");
        Assert.Equal(additional.Length + 2 + (opening ? 0 : 1), frame.Leaves.Count);
        foreach (string key in additional) Assert.Contains(frame.Leaves, value => value.Key == key);
        Assert.Contains(frame.Leaves, value => value.SubjectReferentId == "skip");
        Assert.Contains(frame.Leaves, value => value.SubjectReferentId == "reroll");
        Assert.Equal(!opening, frame.Leaves.Any(value => value.SubjectReferentId == "card"));
        Assert.Equal("interactive", frame.Page.Status);
        Assert.True(frame.Leaves.Single(value => value.SubjectReferentId == "skip").Dispatch().Accepted);
        Assert.Equal(1, dispatch);
        current = false;
        Assert.All(frame.Leaves, value => Assert.False(value.Dispatch().Accepted));
        Assert.Equal(1, dispatch);
        foreach (string gap in new[] { "persistent_hud", "public_information_binding_card_subject", "native_consistency" })
        {
            var partial = STS2Connector.PlayerEnvironment.NativeLogicalCapture.Validate(frame with { Page = frame.Page with
                { Completeness = page.Completeness with { Status = "partial", Missing = new[] { gap } } } });
            Assert.Empty(partial.Leaves);
            Assert.Contains(gap, partial.Page.Completeness.Missing);
        }
    }

    [Theory]
    [InlineData("card")]
    [InlineData("relic")]
    [InlineData("potion")]
    public void StockSubjectsUseCurrentOffersIndependentlyOfGoldAndOwnedInventory(string kind)
    {
        foreach (bool affordable in new[] { false, true })
        {
            var model = new JsonObject { ["entity_id"] = "model", ["definition_id"] = "ITEM", ["name"] = "Item",
                ["description"] = "Unopened details", ["card_previews"] = new JsonArray("unopened") };
            var offer = new JsonObject { ["entity_id"] = "offer", ["stocked"] = true, ["visible"] = true,
                ["affordable"] = affordable, ["can_purchase"] = affordable };
            if (kind == "potion")
                foreach (var pair in model) offer[pair.Key == "entity_id" ? "unused_model_id" : pair.Key] = pair.Value?.DeepClone();
            else offer[kind] = model;
            var page = Page(new JsonObject { ["kind"] = "shop_inventory", [kind + "s"] = new JsonArray(offer) });
            var bindings = new PublicInformationBindings(page);
            var subject = Assert.IsType<PlayerEnvironmentReferent>(bindings.MerchantOffer("offer", kind));
            Assert.Equal("offer", subject.ReferentId);
            Assert.Equal("Item", subject.Label);
            Assert.Equal(kind, subject.Properties!["offer_kind"]!.GetValue<string>());
            Assert.Null(subject.Properties!["information_model"]!["description"]);
            Assert.Null(subject.Properties!["information_model"]!["card_previews"]);
            Assert.Equal(affordable, subject.Properties!["affordable"]!.GetValue<bool>());
            Assert.Equal(affordable, subject.Properties!["can_purchase"]!.GetValue<bool>());
            Assert.True(bindings.Complete);
            offer["stocked"] = false;
            var sold = new PublicInformationBindings(page);
            Assert.Null(sold.MerchantOffer("offer", kind));
            Assert.False(sold.Complete);
        }
    }
}
