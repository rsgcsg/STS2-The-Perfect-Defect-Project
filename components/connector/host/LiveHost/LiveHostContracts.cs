using STS2Connector.Authority;
using System;
using System.Collections.Generic;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace STS2Connector.LiveHost.Contracts;

public sealed record LiveHostIdentity(
    string Id,
    string Name,
    string Version,
    string SourceRevision,
    string ModuleVersionId,
    string RuntimeInstanceId)
{
    // The digest identifies the loaded Host artifact without exposing its path.
    public string ArtifactSha256 { get; init; } = string.Empty;
}

public sealed record CompatibilityAssessment(
    string Status,
    bool ActionExecutionAllowed,
    bool StateObservationAllowed,
    bool ReadAllowed,
    string Detail);

public sealed record GameBuildIdentity(
    string? Version,
    string? Commit,
    string? Branch,
    int? MainAssemblyHash,
    CompatibilityAssessment Compatibility,
    ModsetIdentity? Modset = null)
{
    // release_info.json is useful provenance, but only the runtime-computed
    // main assembly hash participates in exact compatibility identity.
    public int? ReleaseDeclaredMainAssemblyHash { get; init; }
    public string? MainAssemblySha256 { get; init; }
    public string? MainAssemblyMvid { get; init; }
}

public sealed record LoadedModAssemblyIdentity(
    string Name,
    string? Version,
    string ModuleVersionId);

public sealed record LoadedModIdentity(
    string Id,
    string? Version,
    string Source,
    string LoadState,
    bool AffectsGameplay,
    string? WorkshopId,
    IReadOnlyList<LoadedModAssemblyIdentity> Assemblies);

public sealed record ModsetIdentity(
    string Status,
    string Fingerprint,
    string FingerprintScope,
    IReadOnlyList<LoadedModIdentity> Mods,
    string Detail);

public sealed record InformationPolicyInfo(
    string Id,
    string Scope,
    bool IncludesHiddenInformation,
    string UnknownFieldBehavior);

public sealed record PlayerReadCatalogEntry(
    string Kind,
    string Scope,
    string Availability,
    string VisibilityBasis,
    bool StateBound,
    bool CreatesActionAuthority,
    string OrderingSemantics,
    string EstimatedCost,
    IReadOnlyList<string> RecommendedFor,
    IReadOnlyList<string> HiddenByPolicy);

public sealed record PlayerVisibilityState(
    string ProfileId,
    string CoreStatus,
    string PlayerVisibleClosureStatus,
    IReadOnlyList<string> AvailableReads,
    IReadOnlyList<string> LinkedDetailKinds,
    IReadOnlyList<string> HiddenByPolicy,
    IReadOnlyList<string> Missing,
    string UnknownCriticalFieldBehavior);

public sealed record HostDiagnostic(
    string Code,
    string Severity,
    string Category,
    string Effect,
    string Recoverability,
    string? Path = null,
    string? VisibilityClass = null,
    bool? RequiredForAction = null,
    string? SafeDetail = null);

public sealed record PlayerReadCompleteness(
    string PlayerVisibleSemantics,
    IReadOnlyList<string> Sources,
    IReadOnlyList<string> Missing);

[JsonConverter(typeof(PlayerReadContentJsonConverter))]
public interface IPlayerReadContent
{
    string Kind { get; }
}

public sealed class PlayerReadContentJsonConverter : JsonConverter<IPlayerReadContent>
{
    public override IPlayerReadContent Read(
        ref Utf8JsonReader reader,
        Type typeToConvert,
        JsonSerializerOptions options) =>
        throw new JsonException("Player read content is response-only.");

    public override void Write(
        Utf8JsonWriter writer,
        IPlayerReadContent value,
        JsonSerializerOptions options) =>
        JsonSerializer.Serialize(writer, value, value.GetType(), options);
}

public sealed record RunDeckReadContent(
    string Kind,
    int CardCount,
    IReadOnlyList<VisibleCard> Cards) : IPlayerReadContent;

public sealed record CombatPileReadZone(
    string Zone,
    int CardCount,
    string OrderingSemantics,
    IReadOnlyList<VisibleCard> Cards);

public sealed record CombatPilesReadContent(
    string Kind,
    IReadOnlyList<CombatPileReadZone> Zones) : IPlayerReadContent;

/// <summary>
/// Read-only projection of the current merchant catalog. The entries describe
/// facts a player can inspect by opening the merchant UI; they do not publish
/// purchase authority when the inventory is closed.
/// </summary>
public sealed record ShopCatalogReadContent(
    string Kind,
    string AccessState,
    IReadOnlyList<VisibleShopCardOffer> Cards,
    IReadOnlyList<VisibleShopRelicOffer> Relics,
    IReadOnlyList<VisibleShopPotionOffer> Potions,
    VisibleShopCardRemovalOffer? CardRemoval) : IPlayerReadContent;

public sealed record StateCompleteness(
    string PlayerVisibleSemantics,
    string InteractionDiscovery,
    IReadOnlyList<string> Sources,
    IReadOnlyList<string> Missing);

public sealed record InputOwnership(
    string Status,
    string? SurfaceKind,
    string Reason);

public sealed record ActionEntityBinding(
    string Role,
    string EntityId);

public sealed record VisibleEnchantment(
    string DefinitionId,
    string? Name,
    string? Description,
    int Amount,
    string ObservationSource);

public sealed record VisibleCard(
    string EntityId,
    string DefinitionId,
    string? Name,
    string Type,
    string Cost,
    string? StarCost,
    string? Description,
    string Rarity,
    bool IsUpgraded,
    bool IsSelected,
    VisibleEnchantment? ExistingEnchantment,
    string? TargetType = null,
    bool? CanPlay = null,
    string? UnplayableReason = null);

public sealed record VisibleStatus(
    string DefinitionId,
    string? Name,
    decimal Amount,
    string Type,
    string? Description);

public sealed record VisibleIntent(
    string Type,
    string? Label,
    string? Title,
    string? Description);

public sealed record VisibleEnemy(
    string EntityId,
    uint? CombatId,
    string DefinitionId,
    string? Name,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] decimal? Hp,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] decimal? MaxHp,
    decimal Block,
    IReadOnlyList<VisibleStatus> Statuses,
    IReadOnlyList<VisibleIntent> Intents,
    bool HealthBarVisible,
    string HpDisplayMode,
    bool HpNumbersAvailable);

public sealed record VisibleCombatPlayer(
    string PlayerEntityId,
    decimal Block,
    int Energy,
    int MaxEnergy,
    int? Stars,
    IReadOnlyList<VisibleCard> Hand,
    int DrawPileCount,
    int DiscardPileCount,
    int ExhaustPileCount,
    IReadOnlyList<VisibleStatus> Statuses,
    IReadOnlyList<VisibleCombatCompanion> Companions,
    IReadOnlyList<VisibleCombatPotionState> PotionStates,
    IReadOnlyList<VisibleOrb> Orbs,
    int? OrbSlots);

public sealed record VisibleCombatCompanion(
    string EntityId,
    string DefinitionId,
    string? Name,
    bool IsAlive,
    bool HealthBarVisible,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] decimal? Hp,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] decimal? MaxHp,
    decimal Block,
    IReadOnlyList<VisibleStatus> Statuses,
    string HpDisplayMode,
    bool HpNumbersAvailable);

public sealed record VisibleCombatPotionState(
    string EntityId,
    string TargetType,
    bool CanUse,
    bool Automatic);

public sealed record VisibleCombatPotion(
    string EntityId,
    string DefinitionId,
    string? Name,
    string? Description,
    int Slot,
    string TargetType,
    bool CanUse,
    bool Automatic);

public sealed record VisibleRelic(
    string EntityId,
    string DefinitionId,
    string? Name,
    string? Description,
    decimal? Counter,
    IReadOnlyList<VisibleKeyword> Keywords,
    IReadOnlyList<VisibleCard> CardPreviews);

public sealed record VisibleKeyword(
    string Name,
    string? Description);

public sealed record VisibleBoss(
    string DefinitionId,
    string? Name,
    int Order);

public sealed record VisibleRunModifier(
    string DefinitionId,
    string? Name,
    string? Description,
    IReadOnlyList<VisibleKeyword> Keywords,
    IReadOnlyList<VisibleCard> CardPreviews);

public sealed record VisibleRunHud(
    int Act,
    string ActDefinitionId,
    string? ActName,
    int Floor,
    int Ascension,
    IReadOnlyList<VisibleBoss> Bosses,
    IReadOnlyList<VisibleRunModifier> Modifiers);

public sealed record VisiblePlayerHud(
    string EntityId,
    string CharacterDefinitionId,
    string? CharacterName,
    decimal Hp,
    decimal MaxHp,
    int Gold,
    IReadOnlyList<VisibleRelic> Relics,
    IReadOnlyList<VisibleOwnedPotion> Potions,
    int MaxPotionSlots);

public sealed record PersistentStateCompleteness(
    string PlayerVisibleSemantics,
    IReadOnlyList<string> Sources,
    IReadOnlyList<string> Missing);

/// <summary>
/// Persistent facts rendered by the normal single-player run HUD. This is
/// read-only state, not an Interaction or source of action authority.
/// </summary>
public sealed record PersistentVisibleState(
    string Scope,
    VisibleRunHud Run,
    VisiblePlayerHud Player,
    PersistentStateCompleteness Completeness);

/// <summary>
/// A relic currently rendered by the treasure-room holder. Rarity and keyword
/// hover tips belong to this surface because the normal UI exposes both.
/// </summary>
public sealed record VisibleTreasureRelic(
    string EntityId,
    string DefinitionId,
    string? Name,
    string? Description,
    string Rarity,
    IReadOnlyList<VisibleKeyword> Keywords,
    IReadOnlyList<VisibleCard> CardPreviews);

public sealed record VisibleOrb(
    string EntityId,
    string DefinitionId,
    string? Name,
    string? Description,
    decimal PassiveValue,
    decimal EvokeValue,
    int QueueIndex,
    bool IsNextToEvoke);

public sealed record VisibleEventOption(
    string EntityId,
    int Index,
    string? Title,
    string? Description,
    bool IsEnabled,
    bool IsLocked,
    bool IsProceed,
    bool WasChosen,
    bool WillKillPlayer,
    string? RelicName,
    string? RelicDescription,
    IReadOnlyList<VisibleEventOptionTooltip> Tooltips);

public sealed record VisibleEventOptionTooltip(
    string Kind,
    string? Name,
    string? Description,
    VisibleCard? Card);

[JsonConverter(typeof(LiveContextJsonConverter))]
public interface ILiveContext
{
    string Kind { get; }
}

public sealed class LiveContextJsonConverter : JsonConverter<ILiveContext>
{
    public override ILiveContext Read(
        ref Utf8JsonReader reader,
        Type typeToConvert,
        JsonSerializerOptions options) =>
        throw new JsonException("Player contexts are response-only Host objects.");

    public override void Write(
        Utf8JsonWriter writer,
        ILiveContext value,
        JsonSerializerOptions options) =>
        JsonSerializer.Serialize(writer, value, value.GetType(), options);
}

public sealed record EventLiveContext(
    string Kind,
    string EventId,
    string? Name,
    bool Ancient,
    bool InDialogue,
    string? Body) : ILiveContext;

public sealed record CombatLiveContext(
    string Kind,
    string EncounterType,
    int Round,
    string TurnOwner,
    bool IsPlayPhase,
    VisibleCombatPlayer Player,
    IReadOnlyList<VisibleEnemy> Enemies) : ILiveContext
{
    // Host-private capture accounting follows this context across input owners.
    // It is merged into public completeness, never serialized as gameplay facts.
    [JsonIgnore]
    internal IReadOnlyList<string> CaptureMissing { get; init; } = Array.Empty<string>();
}

public sealed record RewardFlowLiveContext(
    string Kind,
    string RewardKind) : ILiveContext;

public sealed record RestLiveContext(
    string Kind) : ILiveContext;

public sealed record TreasureLiveContext(
    string Kind) : ILiveContext;

public sealed record GameOverLiveContext(
    string Kind,
    string Result,
    string GameMode,
    int? Score,
    int? FloorReached,
    int? Ascension) : ILiveContext;

public sealed record MenuLiveContext(
    string Kind,
    string Flow) : ILiveContext;

public sealed record VisibleOwnedPotion(
    string EntityId,
    string DefinitionId,
    string? Name,
    string? Description,
    int Slot,
    IReadOnlyList<VisibleKeyword> Keywords,
    IReadOnlyList<VisibleCard> CardPreviews);

public sealed record ShopLiveContext(
    string Kind) : ILiveContext;

public sealed record VisibleMapCoordinate(
    int Col,
    int Row,
    string? PointType = null);

public sealed record VisibleMapNode(
    string EntityId,
    int Col,
    int Row,
    string PointType,
    string State,
    IReadOnlyList<VisibleMapCoordinate> Children);

public sealed record MapLiveContext(
    string Kind,
    int ActIndex,
    VisibleMapCoordinate? CurrentPosition,
    IReadOnlyList<VisibleMapCoordinate> Visited,
    IReadOnlyList<VisibleMapNode> Nodes) : ILiveContext;

public sealed record CombatTransitionLiveContext(
    string Kind,
    string Phase,
    string Transition) : ILiveContext;

public sealed record RunTransitionLiveContext(
    string Kind,
    string Phase,
    string Transition) : ILiveContext;

public sealed record UnknownLiveContext(
    string Kind,
    string SourceType,
    string Reason) : ILiveContext
{
    [JsonIgnore]
    internal IReadOnlyList<string> CaptureMissing { get; init; } = Array.Empty<string>();
}

[JsonConverter(typeof(LiveSurfaceJsonConverter))]
public interface ILiveSurface
{
    string Kind { get; }
}

public sealed class LiveSurfaceJsonConverter : JsonConverter<ILiveSurface>
{
    public override ILiveSurface Read(
        ref Utf8JsonReader reader,
        Type typeToConvert,
        JsonSerializerOptions options) =>
        throw new JsonException("Player surfaces are response-only Host objects.");

    public override void Write(
        Utf8JsonWriter writer,
        ILiveSurface value,
        JsonSerializerOptions options) =>
        JsonSerializer.Serialize(writer, value, value.GetType(), options);
}

public sealed record DeckEnchantSelectionSurface(
    string Kind,
    string Stage,
    string ScreenEntityId,
    string? Prompt,
    int MinSelect,
    int MaxSelect,
    int SelectedCount,
    IReadOnlyList<string> SelectedCardEntityIds,
    bool Cancelable,
    VisibleEnchantment Enchantment,
    IReadOnlyList<VisibleCard> Cards) : ILiveSurface
{
    public IReadOnlyList<string> SelectableCardEntityIds { get; init; } = Array.Empty<string>();
    public IReadOnlyList<string> DeselectableCardEntityIds { get; init; } = Array.Empty<string>();
    public bool CanPreview { get; init; }
    public bool CanCloseSelection { get; init; }
    public bool CanConfirm { get; init; }
    public bool CanCancelPreview { get; init; }
}

public sealed record EventOptionSurface(
    string Kind,
    string ScreenEntityId,
    IReadOnlyList<VisibleEventOption> Options) : ILiveSurface;

public sealed record VisibleDialogueLine(
    string EntityId,
    int Index,
    string Text,
    string Speaker,
    bool IsCurrent);

public sealed record EventDialogueSurface(
    string Kind,
    string ScreenEntityId,
    int CurrentLineIndex,
    IReadOnlyList<VisibleDialogueLine> RevealedLines,
    string AdvanceLabel) : ILiveSurface
{
    public bool CanAdvance { get; init; }
}

public sealed record VisibleRestOption(
    string EntityId,
    int Index,
    string OptionId,
    string? Name,
    string? Description,
    bool Enabled);

public sealed record RestSiteSurface(
    string Kind,
    string ScreenEntityId,
    IReadOnlyList<VisibleRestOption> Options,
    bool CanProceed) : ILiveSurface;

public sealed record VisibleShopCardOffer(
    string EntityId,
    string SlotEntityId,
    int InventoryIndex,
    int Price,
    bool Stocked,
    bool Visible,
    bool Affordable,
    bool CanPurchase,
    string? BlockedReason,
    bool OnSale,
    VisibleCard? Card);

public sealed record VisibleShopRelicOffer(
    string EntityId,
    string SlotEntityId,
    int InventoryIndex,
    int Price,
    bool Stocked,
    bool Visible,
    bool Affordable,
    bool CanPurchase,
    string? BlockedReason,
    VisibleRelic? Relic);

public sealed record VisibleShopPotionOffer(
    string EntityId,
    string SlotEntityId,
    int InventoryIndex,
    int Price,
    bool Stocked,
    bool Visible,
    bool Affordable,
    bool CanPurchase,
    string? BlockedReason,
    string? DefinitionId,
    string? Name,
    string? Description,
    string? Rarity);

public sealed record VisibleShopCardRemovalOffer(
    string EntityId,
    string SlotEntityId,
    int InventoryIndex,
    int Price,
    int NextPriceIncrease,
    bool Stocked,
    bool Visible,
    bool Affordable,
    bool CanPurchase,
    string? BlockedReason);

public sealed record ShopInventorySurface(
    string Kind,
    string ScreenEntityId,
    IReadOnlyList<VisibleShopCardOffer> Cards,
    IReadOnlyList<VisibleShopRelicOffer> Relics,
    IReadOnlyList<VisibleShopPotionOffer> Potions,
    VisibleShopCardRemovalOffer? CardRemoval,
    bool CanClose) : ILiveSurface;

public sealed record ShopRoomSurface(
    string Kind,
    string RoomEntityId,
    bool CanOpenInventory,
    bool CanProceed) : ILiveSurface;

/// <summary>
/// The single-player treasure-room lifecycle. Chest opening, relic choice,
/// skip, and room departure expose distinct current native controls.
/// </summary>
public sealed record TreasureRoomSurface(
    string Kind,
    string Stage,
    string RoomEntityId,
    bool ChestOpened,
    IReadOnlyList<VisibleTreasureRelic> Relics,
    bool CanChoose,
    bool CanSkip,
    bool CanProceed) : ILiveSurface;

/// <summary>
/// The native one-of-N relic choice is a child of a PlayerChoice command.
/// Parent lineage stays in Native Foundation; this surface contains only
/// relics and controls rendered to the local player.
/// </summary>
internal sealed record NativeBossRelicSelectionSurface(
    string Kind,
    string ScreenEntityId,
    IReadOnlyList<VisibleRelic> Relics,
    IReadOnlyList<string> SelectableRelicEntityIds,
    bool CanSkip) : ILiveSurface;

/// <summary>
/// The ordinary single-player game-over lifecycle. The intro and summary are
/// separate stages; returning to the main menu is not legal before the actual
/// summary control becomes visible and enabled.
/// </summary>
public sealed record GameOverSurface(
    string Kind,
    string Stage,
    string ScreenEntityId,
    string? ReturnDestination,
    bool CanAdvanceSummary,
    bool CanReturn,
    IReadOnlyList<VisibleMenuOption> OtherControls) : ILiveSurface;

public sealed record VisibleCharacterChoice(
    string EntityId,
    int Index,
    string CharacterId,
    string Name,
    bool IsLocked,
    bool IsSelected,
    bool IsRandom,
    bool IsEnabled);

public sealed record VisibleStartingRelic(
    string DefinitionId,
    string? Name,
    string? Description);

public sealed record VisibleSelectedCharacterDetails(
    string CharacterId,
    string Title,
    string? Description,
    int? StartingHp,
    int? StartingGold,
    VisibleStartingRelic? StartingRelic);

/// <summary>
/// Ordinary single-player character selection. This contract intentionally
/// exposes only facts rendered by the current screen; it does not leak the
/// starting deck or collection totals exposed by the legacy reconstruction.
/// </summary>
public sealed record CharacterSelectSurface(
    string Kind,
    string Stage,
    string ScreenEntityId,
    IReadOnlyList<VisibleCharacterChoice> Characters,
    VisibleSelectedCharacterDetails? SelectedDetails,
    int? Ascension,
    string? AscensionTitle,
    string? AscensionDescription,
    bool CanDecreaseAscension,
    bool CanIncreaseAscension,
    bool CanEmbark,
    bool CanGoBack) : ILiveSurface;

public sealed record VisibleTutorialOption(
    string SemanticId,
    string Label,
    bool Enabled);

/// <summary>
/// A currently open, exact native tutorial modal. Only explicitly audited
/// tutorial types may construct this Surface; an unknown modal remains
/// unsupported and owns no mutation authority.
/// </summary>
public sealed record TutorialSurface(
    string Kind,
    string Stage,
    string ScreenEntityId,
    string TutorialId,
    int? CurrentPage,
    int? TotalPages,
    string? Title,
    string? Body,
    string? PageLabel,
    IReadOnlyList<VisibleTutorialOption> Options) : ILiveSurface;

public sealed record VisibleMenuOption(
    string EntityId,
    string SemanticId,
    string Label,
    string? Description,
    bool Enabled,
    string ActionSupport,
    string? BlockedReason);

public sealed record VisibleContinueRunSummary(
    string CharacterId,
    string? CharacterName,
    string ActId,
    string? ActName,
    int Floor,
    int Hp,
    int MaxHp,
    int Gold,
    int Ascension);

public sealed record MainMenuSurface(
    string Kind,
    string Stage,
    string ScreenEntityId,
    IReadOnlyList<VisibleMenuOption> Options,
    VisibleContinueRunSummary? ContinueRun) : ILiveSurface;

public sealed record SingleplayerMenuSurface(
    string Kind,
    string Stage,
    string ScreenEntityId,
    IReadOnlyList<VisibleMenuOption> Options) : ILiveSurface;

/// <summary>
/// Purpose-specific random deck transformation. PreviewKind describes the
/// visible presentation, not a future outcome; ReplacementKnown must remain
/// false for random transforms until after commit.
/// </summary>
public sealed record DeckTransformSelectionSurface(
    string Kind,
    string Stage,
    string ScreenEntityId,
    string Prompt,
    int MinSelect,
    int MaxSelect,
    int SelectedCount,
    IReadOnlyList<string> SelectedCardEntityIds,
    bool Cancelable,
    bool UpgradeToggleVisible,
    bool ShowingUpgradePreviews,
    string PreviewKind,
    bool ReplacementKnown,
    IReadOnlyList<VisibleCard> Cards) : ILiveSurface
{
    public IReadOnlyList<string> SelectableCardEntityIds { get; init; } = Array.Empty<string>();
    public IReadOnlyList<string> DeselectableCardEntityIds { get; init; } = Array.Empty<string>();
    public bool CanPreview { get; init; }
    public bool CanCancelSelection { get; init; }
    public bool CanCancelPreview { get; init; }
    public bool CanConfirm { get; init; }
    public bool CanToggleUpgradeView { get; init; }
}

public sealed record VisibleCombatCommandOption(
    string EntityId,
    string? Name,
    IReadOnlyList<string> TargetEntityIds);

public sealed record CombatTurnSurface(
    string Kind,
    string RoomEntityId,
    bool CanEndTurn) : ILiveSurface
{
    public IReadOnlyList<VisibleCombatCommandOption> PlayableCards { get; init; } =
        Array.Empty<VisibleCombatCommandOption>();
    public IReadOnlyList<VisibleCombatCommandOption> UsablePotions { get; init; } =
        Array.Empty<VisibleCombatCommandOption>();
}

public sealed record CombatHandCardSelectionSurface(
    string Kind,
    string HandEntityId,
    string Prompt,
    string SelectionMode,
    int MinSelect,
    int MaxSelect,
    int SelectedCount,
    IReadOnlyList<string> SelectedCardEntityIds,
    bool RequireManualConfirmation,
    bool IsPeeking,
    IReadOnlyList<string> SelectableCardEntityIds,
    IReadOnlyList<string> DeselectableCardEntityIds,
    bool CanConfirm,
    bool CanClosePeek,
    IReadOnlyList<VisibleCard> Cards) : ILiveSurface;

public sealed record VisibleCardRewardAlternative(
    string EntityId,
    int Index,
    string Label,
    bool Enabled);

/// <summary>
/// A player-visible room reward. The Host deliberately exposes only the
/// semantic kind and the text already rendered by the reward button.
/// </summary>
public sealed record VisibleReward(
    string EntityId,
    string Kind,
    string Label,
    string? Description,
    bool Enabled);

public sealed record CardRewardSelectionSurface(
    string Kind,
    string ScreenEntityId,
    IReadOnlyList<VisibleCard> Cards,
    IReadOnlyList<VisibleCardRewardAlternative> Alternatives) : ILiveSurface
{
    public IReadOnlyList<string> SelectableCardEntityIds { get; init; } =
        Array.Empty<string>();
}

public sealed record VisibleCardBundle(
    string EntityId,
    IReadOnlyList<VisibleCard> Cards);

public sealed record CardBundleSelectionSurface(
    string Kind,
    string Stage,
    string ScreenEntityId,
    string? Prompt,
    string? SelectedBundleEntityId,
    IReadOnlyList<string> SelectableBundleEntityIds,
    bool CanConfirm,
    bool CanCancelPreview,
    IReadOnlyList<VisibleCardBundle> Bundles) : ILiveSurface;

public sealed record RewardClaimSurface(
    string Kind,
    string ScreenEntityId,
    IReadOnlyList<VisibleReward> Rewards,
    bool PotionSlotsFull,
    IReadOnlyList<VisibleCombatPotion> DiscardablePotions,
    bool CanProceed,
    bool ProceedSkipsRemainingRewards) : ILiveSurface;

public sealed record VisibleMapChoice(
    string EntityId,
    int Col,
    int Row,
    string PointType);

public sealed record MapNavigationSurface(
    string Kind,
    string ScreenEntityId,
    bool TravelEnabled,
    bool Traveling,
    string DrawingMode,
    IReadOnlyList<VisibleMapChoice> NextOptions) : ILiveSurface
{
    public string? AnnotationInputEntityId { get; init; }
    public bool CanExitAnnotation { get; init; }
}

public sealed record UnsupportedSurface(
    string Kind,
    string SourceType,
    string Reason) : ILiveSurface;

public sealed record NoActionSurface(
    string Kind,
    string Reason,
    string? Message) : ILiveSurface;

public sealed record PotionPopupSurface(string Kind, string ScreenEntityId, string PotionEntityId,
    string DefinitionId, string Name, int Slot, bool CanUse, bool CanDiscard) : ILiveSurface
{
    // Legacy wire name: exact direct native use also includes non-combat AnyTime potions.
    public bool DirectCombatUse { get; init; }
    public IReadOnlyList<string> UseTargetEntityIds { get; init; } = Array.Empty<string>();
}
