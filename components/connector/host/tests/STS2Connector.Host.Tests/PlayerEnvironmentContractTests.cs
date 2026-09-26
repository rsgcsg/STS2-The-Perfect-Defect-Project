using STS2Connector.Authority;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2Connector.PlayerEnvironment;
using STS2Connector.NativeUi;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace STS2Connector.Tests;

public sealed class PlayerEnvironmentContractTests
{
    [Fact]
    public void TextCombatEntryCanBeInteractiveWithoutEndTurnBinding()
    {
        var noEndTurn = new CombatTurnSurface(
            "combat_turn", "room-current", false)
        {
            PlayableCards = new[]
            {
                new VisibleCombatCommandOption("card-current", "Card",
                    Array.Empty<string>())
            }
        };
        Assert.Empty(PlayerEnvironmentService.DescribeTextCombatCommands(noEndTurn));
        var nativeComplete = new StateCompleteness(
            "contract_complete_for_immediate_combat_turn_including_visible_companions",
            "derived_from_same_validator_as_execution",
            Array.Empty<string>(), Array.Empty<string>());
        var emptyProjection = new PlayerEnvironmentBoundActionProjection(
            "test", "complete", 0, 0, 512, "test",
            Array.Empty<PlayerEnvironmentBoundAction>());

        Assert.True(PlayerEnvironmentService.CanPublishTextCombatEntry(
            true, noEndTurn, "ready", nativeComplete, emptyProjection));
        Assert.False(PlayerEnvironmentService.CanPublishTextCombatEntry(
            false, noEndTurn, "ready", nativeComplete, emptyProjection));
        Assert.False(PlayerEnvironmentService.CanPublishTextCombatEntry(
            true, noEndTurn, "settling", nativeComplete, emptyProjection));
        Assert.False(PlayerEnvironmentService.CanPublishTextCombatEntry(
            true, noEndTurn, "ready", nativeComplete,
            emptyProjection with { Status = "truncated" }));
    }

    [Fact]
    public void NativeMapBackRemainsAvailableWhenRoutesAreExactlyEmpty()
    {
        var emptyMap = new MapNavigationSurface(
            "map_navigation", "map-current", false, false, "none",
            Array.Empty<VisibleMapChoice>());
        var knownEmpty = new StateCompleteness(
            "contract_complete_for_visible_singleplayer_map_navigation",
            "temporarily_empty_while_map_input_is_not_route_ready",
            Array.Empty<string>(), Array.Empty<string>());

        Assert.True(NativeTextMenuInformation.CanPublishMapPage(
            "settling", "complete", 0, 0, "settling", knownEmpty,
            emptyMap, nativeBackAvailable: true));
        Assert.False(NativeTextMenuInformation.CanPublishMapPage(
            "settling", "complete", 0, 0, "settling", knownEmpty,
            emptyMap, nativeBackAvailable: false));
        Assert.False(NativeTextMenuInformation.CanPublishMapPage(
            "settling", "truncated", 0, 1, "settling", knownEmpty,
            emptyMap, nativeBackAvailable: true));
        Assert.False(NativeTextMenuInformation.CanPublishMapPage(
            "settling", "complete", 0, 0, "settling",
            knownEmpty with { Missing = new[] { "route_binding_unavailable" } },
            emptyMap, nativeBackAvailable: true));
    }

    [Fact]
    public void TextCombatCatalogKeepsNativeEndTurnWithoutLegacyTargetExpansion()
    {
        string[] manyTargets = Enumerable.Range(0, 576)
            .Select(index => "target-" + index).ToArray();
        var surface = new CombatTurnSurface("combat_turn", "room-current", true)
        {
            PlayableCards = new[]
            {
                new VisibleCombatCommandOption("card-current", "Card", manyTargets)
            },
            UsablePotions = new[]
            {
                new VisibleCombatCommandOption("potion-current", "Potion", manyTargets)
            }
        };

        IReadOnlyList<NativeUiActionDescriptor> legacy =
            NativeUiActionRuntime.DescribeCombatCommands(surface);
        IReadOnlyList<NativeUiActionDescriptor> text =
            PlayerEnvironmentService.DescribeTextCombatCommands(surface);

        Assert.Contains(legacy, command => command.Kind == "play_card");
        Assert.Contains(legacy, command => command.Kind == "use_potion");
        Assert.Single(text);
        Assert.Equal("end_turn", text[0].Kind);
        Assert.Equal("end_turn:room-current", text[0].Key);
        Assert.Empty(text[0].EntityBindings ?? Array.Empty<ActionEntityBinding>());
    }

    [Fact]
    public void ProcessImmutableIdentityIsComputedOnceAndReused()
    {
        int calls = 0;
        var cache = new ProcessImmutableValue<LoadedMainAssemblyIdentity>(() =>
        {
            calls++;
            return new LoadedMainAssemblyIdentity("sha", "mvid");
        });

        LoadedMainAssemblyIdentity first = cache.Read();
        LoadedMainAssemblyIdentity second = cache.Read();

        Assert.Same(first, second);
        Assert.Equal(1, calls);
        Assert.Equal("sha", second.Sha256);
        Assert.Equal("mvid", second.ModuleVersionId);
    }

    [Fact]
    public void SettlingSnapshotsCannotPublishMutationAuthority()
    {
        Assert.False(PlayerEnvironmentService.CanPublishMutationAuthority("settling"));
        Assert.False(PlayerEnvironmentService.CanPublishMutationAuthority("degraded"));
        Assert.True(PlayerEnvironmentService.CanPublishMutationAuthority("ready"));
    }

    [Fact]
    public void ExecutionReadinessRequiresAnExactAdmittedModset()
    {
        var modset = ExactConnectorModset();
        var compatibility = new CompatibilityAssessment(
            Status: "supported_exact",
            ActionExecutionAllowed: true,
            StateObservationAllowed: true,
            ReadAllowed: false,
            Detail: "Current visible UI remains the action authority.");
        var game = new GameBuildIdentity(
            "v0.111.0",
            "41cef1ea",
            "v0.111.0",
            1010476334,
            compatibility,
            modset)
        {
            MainAssemblySha256 = "game-sha",
            MainAssemblyMvid = "00000000-0000-0000-0000-000000000002"
        };

        const string sourceRevision = "0123456789abcdef0123456789abcdef01234567";
        Assert.True(EnvironmentIdentityRuntime.ExecutionAvailable(
            game,
            "loaded-sha",
            sourceRevision));
        Assert.False(EnvironmentIdentityRuntime.ExecutionAvailable(
            game with { MainAssemblyHash = null },
            "loaded-sha",
            sourceRevision));
        Assert.False(EnvironmentIdentityRuntime.ExecutionAvailable(
            game with { MainAssemblySha256 = null },
            "loaded-sha",
            sourceRevision));
        Assert.False(EnvironmentIdentityRuntime.ExecutionAvailable(
            game with
            {
                Compatibility = compatibility with { Status = "identified" }
            },
            "loaded-sha",
            sourceRevision));
        Assert.False(EnvironmentIdentityRuntime.ExecutionAvailable(
            game,
            null,
            sourceRevision));
        Assert.False(EnvironmentIdentityRuntime.ExecutionAvailable(
            game,
            "loaded-sha",
            "unavailable"));
        Assert.False(EnvironmentIdentityRuntime.ExecutionAvailable(
            game with
            {
                Compatibility = compatibility with { StateObservationAllowed = false }
            },
            "loaded-sha",
            sourceRevision));
        Assert.False(EnvironmentIdentityRuntime.ExecutionAvailable(
            game with
            {
                Modset = modset with { Status = "additional_loaded_mods" }
            },
            "loaded-sha",
            sourceRevision));
        Assert.True(EnvironmentIdentityRuntime.ExecutionAvailable(
            game with
            {
                Modset = modset with { Status = "canary_exact_observer_modset" }
            },
            "loaded-sha",
            sourceRevision));
        Assert.True(EnvironmentIdentityRuntime.ExecutionAvailable(
            game with
            {
                Modset = modset with { Status = "exact_platform_modset" }
            },
            "loaded-sha",
            sourceRevision));
        Assert.False(EnvironmentIdentityRuntime.ExecutionAvailable(
            game with { Modset = null },
            "loaded-sha",
            sourceRevision));
    }

    [Fact]
    public void ExactGamePermissionFailsClosedAndRequiresExplicitCandidateOptIn()
    {
        ExactGamePermission supported = ExactGameCompatibility.Evaluate(
            "v0.111.0",
            "41cef1ea",
            1010476334,
            "9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4",
            "57785517-0b16-42b9-8b36-bad6fb28384b",
            "darwin",
            "arm64",
            null);
        ExactGamePermission closedCandidate = ExactGameCompatibility.Evaluate(
            "v0.111.0",
            "41cef1ea",
            222455745,
            "0861bfa1df347538d932f22d580e75420f08082792eb914e53b4882764acdbe9",
            "73b63ee0-6c0a-47bb-b0d1-b21f6d94222e",
            "win32",
            "x64",
            null);
        ExactGamePermission canary = ExactGameCompatibility.Evaluate(
            "v0.111.0",
            "41cef1ea",
            222455745,
            "0861bfa1df347538d932f22d580e75420f08082792eb914e53b4882764acdbe9",
            "73b63ee0-6c0a-47bb-b0d1-b21f6d94222e",
            "win32",
            "x64",
            ExactGameCompatibility.WindowsCandidateId);
        ExactGamePermission changed = ExactGameCompatibility.Evaluate(
            "v0.111.1",
            "changed",
            222455745,
            "0861bfa1df347538d932f22d580e75420f08082792eb914e53b4882764acdbe9",
            "73b63ee0-6c0a-47bb-b0d1-b21f6d94222e",
            "win32",
            "x64",
            ExactGameCompatibility.WindowsCandidateId);

        Assert.Equal("supported_exact", supported.Status);
        Assert.True(supported.ActionExecutionAllowed);
        Assert.Equal("known_unqualified", closedCandidate.Status);
        Assert.False(closedCandidate.ActionExecutionAllowed);
        Assert.Equal("canary_exact", canary.Status);
        Assert.True(canary.ActionExecutionAllowed);
        Assert.Equal("unsupported_exact_game", changed.Status);
        Assert.False(changed.ActionExecutionAllowed);
    }

    [Fact]
    public void ArtifactPermissionRequiresSealedTupleOrExactSourceCanary()
    {
        ExactArtifactPermission sealedArtifact = ExactArtifactCompatibility.Evaluate(
            ExactArtifactCompatibility.SealedSourceRevision,
            ExactArtifactCompatibility.SealedArtifactSha256,
            ExactArtifactCompatibility.SealedArtifactMvid,
            null);
        const string candidateSource = "0123456789abcdef0123456789abcdef01234567";
        ExactArtifactPermission closedCandidate = ExactArtifactCompatibility.Evaluate(
            candidateSource,
            "candidate-sha",
            "candidate-mvid",
            null);
        ExactArtifactPermission canary = ExactArtifactCompatibility.Evaluate(
            candidateSource,
            "candidate-sha",
            "candidate-mvid",
            candidateSource);
        ExactArtifactPermission wrongCanary = ExactArtifactCompatibility.Evaluate(
            candidateSource,
            "candidate-sha",
            "candidate-mvid",
            "fedcba9876543210fedcba9876543210fedcba98");

        Assert.Equal("supported_exact", sealedArtifact.Status);
        Assert.True(sealedArtifact.ActionExecutionAllowed);
        Assert.Equal("artifact_unqualified", closedCandidate.Status);
        Assert.False(closedCandidate.ActionExecutionAllowed);
        Assert.Equal("canary_exact", canary.Status);
        Assert.True(canary.ActionExecutionAllowed);
        Assert.False(wrongCanary.ActionExecutionAllowed);
    }

    [Fact]
    public void ExactModsetUsesStableManifestIdRatherThanSourceNamespace()
    {
        ModsetIdentity exact = LiveModsetIdentity.Evaluate(
            "Initialized",
            ExactConnectorModset().Mods,
            "00000000-0000-0000-0000-000000000001",
            ConnectorMod.Version);
        ModsetIdentity renamedId = LiveModsetIdentity.Evaluate(
            "Initialized",
            new[]
            {
                ExactConnectorModset().Mods[0] with { Id = "STS2Connector" }
            },
            "00000000-0000-0000-0000-000000000001",
            ConnectorMod.Version);

        Assert.Equal("STS2_MCP", LiveModsetIdentity.ConnectorModId);
        Assert.Equal("exact_player_environment_only", exact.Status);
        Assert.Equal("connector_identity_missing", renamedId.Status);
    }

    [Fact]
    public void ExactUnifiedPlatformModsetUsesTheConnectorAssemblyAsItsHostIdentity()
    {
        var platform = new LoadedModIdentity(
            LiveModsetIdentity.PlatformModId,
            "0.1.0",
            "ModsDirectory",
            "Loaded",
            false,
            null,
            new[]
            {
                new LoadedModAssemblyIdentity(
                    LiveModsetIdentity.PlatformModId,
                    "0.1.0.0",
                    "00000000-0000-0000-0000-000000000001")
            });

        ModsetIdentity exact = LiveModsetIdentity.Evaluate(
            "Initialized",
            new[] { platform },
            "00000000-0000-0000-0000-000000000001",
            ConnectorMod.Version);

        Assert.Equal("STS2_PLATFORM", LiveModsetIdentity.PlatformModId);
        Assert.Equal("exact_platform_modset", exact.Status);
        Assert.Contains("unified STS2 Platform", exact.Detail);
    }

    [Fact]
    public void ExactObserverModsetRequiresItsFullFingerprintCanary()
    {
        LoadedModIdentity connector = ExactConnectorModset().Mods[0];
        var observer = new LoadedModIdentity(
            "STS2_HUMAN_ANNOTATOR",
            "0.1.0",
            "Local",
            "Loaded",
            false,
            null,
            new[]
            {
                new LoadedModAssemblyIdentity(
                    "STS2_HUMAN_ANNOTATOR",
                    "0.1.0.0",
                    "00000000-0000-0000-0000-000000000002")
            });
        LoadedModIdentity[] mods = { connector, observer };
        ModsetIdentity closed = LiveModsetIdentity.Evaluate(
            "Initialized",
            mods,
            "00000000-0000-0000-0000-000000000001",
            ConnectorMod.Version);
        ModsetIdentity admitted = LiveModsetIdentity.Evaluate(
            "Initialized",
            mods,
            "00000000-0000-0000-0000-000000000001",
            ConnectorMod.Version,
            closed.Fingerprint);
        ModsetIdentity gameplayMod = LiveModsetIdentity.Evaluate(
            "Initialized",
            new[] { connector, observer with { AffectsGameplay = true } },
            "00000000-0000-0000-0000-000000000001",
            ConnectorMod.Version,
            closed.Fingerprint);

        Assert.Equal("additional_loaded_mods", closed.Status);
        Assert.Equal("canary_exact_observer_modset", admitted.Status);
        Assert.Equal("additional_loaded_mods", gameplayMod.Status);
    }

    [Theory]
    [InlineData("headless", "headless")]
    [InlineData("HEADLESS", "headless")]
    [InlineData("macos", "live_ui")]
    [InlineData(null, "live_ui")]
    public void HostKindReflectsTheActualDisplayDriver(string? displayDriver, string expected) =>
        Assert.Equal(expected, EnvironmentIdentityRuntime.HostKind(displayDriver));

    [Fact]
    public void PublicModsetListsOnlyActuallyLoadedMods()
    {
        ModsetIdentity modset = ExactConnectorModset() with
        {
            Mods = new[]
            {
                ExactConnectorModset().Mods[0],
                new LoadedModIdentity(
                    "disabled-helper",
                    "1.0.0",
                    "ModsDirectory",
                    "Disabled",
                    true,
                    null,
                    Array.Empty<LoadedModAssemblyIdentity>())
            }
        };
        var game = new GameBuildIdentity(
            "v0.111.0",
            "41cef1ea",
            "v0.111.0",
            1010476334,
            new CompatibilityAssessment("identified", true, true, true, "test"),
            modset);

        PlayerEnvironmentGameIdentity projected = PlayerEnvironmentService.ToGameIdentity(game);

        Assert.Equal(new[] { "STS2_MCP" }, projected.Modset.LoadedModIds);
    }

    [Fact]
    public void CWireExcludesModeFrameAnnotationsAndNativeBindingOperands()
    {
        string[] observationProperties = typeof(PlayerEnvironmentSnapshot)
            .GetProperties()
            .Select(property => property.Name)
            .ToArray();
        string[] requestProperties = typeof(PlayerEnvironmentActionRequest)
            .GetProperties()
            .Select(property => property.Name)
            .ToArray();
        string[] boundActionProperties = typeof(PlayerEnvironmentBoundAction)
            .GetProperties()
            .Select(property => property.Name)
            .ToArray();

        Assert.DoesNotContain("Mode", observationProperties);
        Assert.DoesNotContain("Frame", observationProperties);
        Assert.DoesNotContain("OptionalAnnotations", observationProperties);
        Assert.DoesNotContain("Mode", requestProperties);
        Assert.DoesNotContain("ExpectedFrameId", requestProperties);
        Assert.DoesNotContain("ExpectedOwnerId", requestProperties);
        Assert.DoesNotContain("Parameters", requestProperties);
        Assert.DoesNotContain("Parameters", boundActionProperties);
        Assert.DoesNotContain("ParameterDomains", boundActionProperties);
        Assert.DoesNotContain("EntityBindings", boundActionProperties);
        Assert.Contains("SnapshotId", observationProperties);
        Assert.Contains("Referents", observationProperties);
        Assert.Contains("Interaction", observationProperties);
        Assert.Contains("Reads", observationProperties);
        Assert.DoesNotContain("Bridge", observationProperties);
        Assert.DoesNotContain("Game", observationProperties);
        Assert.DoesNotContain("Entities", observationProperties);
        Assert.DoesNotContain("Controls", observationProperties);

        string[] capabilityProperties = typeof(PlayerEnvironmentCapabilitiesResponse)
            .GetProperties()
            .Select(property => property.Name)
            .ToArray();
        Assert.DoesNotContain("BusinessSourceRequired", capabilityProperties);
        Assert.DoesNotContain("BusinessOutcomeRequired", capabilityProperties);
        Assert.Contains("EnvironmentFingerprint", capabilityProperties);
    }

    [Theory]
    [InlineData("play_card", "play_card", "play")]
    [InlineData("select_entity", "unknown_source_select", "select")]
    [InlineData("confirm_interaction", "confirm_selection", "confirm")]
    [InlineData("activate_control", "open_shop", "open")]
    [InlineData("activate_control", "leave_shop", "close")]
    [InlineData("choose", "choose_event_option", "activate")]
    [InlineData("choose", "proceed_event", "activate")]
    [InlineData("choose", "choose_rest_option", "activate")]
    [InlineData("activate_control", "proceed_rest_site", "activate")]
    [InlineData("purchase", "purchase_shop_card", "activate")]
    [InlineData("activate_control", "close_shop_inventory", "close")]
    [InlineData("choose", "choose_treasure_relic", "activate")]
    [InlineData("choose", "skip_treasure_relic", "skip")]
    [InlineData("activate_control", "proceed_treasure_room", "activate")]
    public void GenericUiActionsDoNotExposeBusinessOperationAsTheWireVerb(
        string command,
        string operation,
        string expected)
    {
        Assert.Equal(expected, PlayerEnvironmentService.GenericAction(command, operation));
    }

    [Fact]
    public void BreakingWireCleanupUsesRevisionedSchemas()
    {
        Assert.Equal("1.0.0", PlayerEnvironmentContract.ProtocolVersion);
        Assert.Equal("sts2.player-environment/snapshot-1", PlayerEnvironmentContract.SnapshotSchema);
        Assert.Equal("sts2.player-environment/action-1", PlayerEnvironmentContract.ActionSchema);
        Assert.Equal("sts2.player-environment/receipt-1", PlayerEnvironmentContract.ReceiptSchema);
        Assert.Equal("STS2_MCP.conf", ConnectorMod.ConfigFileName);
    }

    [Fact]
    public void UnknownDeliveryContractNeverPermitsRetry()
    {
        var receipt = new PlayerEnvironmentActionReceipt(
            PlayerEnvironmentContract.ProtocolVersion,
            PlayerEnvironmentContract.ReceiptSchema,
            "request-a",
            "unknown",
            new PlayerEnvironmentActionSummary(
                "bound-action-a",
                "activate",
                "control-a",
                Array.Empty<PlayerEnvironmentBoundActionArgument>()),
            "input_delivery_unknown",
            "Delivery may have occurred.",
            new PlayerEnvironmentRetryPolicy(false, "unknown_delivery_never_retry"),
            null);

        Assert.False(receipt.Retry.Allowed);
        Assert.Equal("unknown", receipt.Delivery);
    }

    [Fact]
    public void ReadOnlyDetailContractsArePlayerEnvironmentAndStateBound()
    {
        Assert.Equal(
            "sts2.player-environment/read-1",
            PlayerEnvironmentContract.ReadSchema);

        var entry = new PlayerEnvironmentReadOpportunity(
            "read:surface_card:card-a",
            "surface_card",
            "card-a",
            "sts2.player-environment/read/surface_card-1",
            "normal_player_visible_surface_card",
            SnapshotBound: true,
            "single_entity",
            Array.Empty<string>());
        Assert.True(entry.SnapshotBound);
        Assert.True(ConnectorMod.IsSafePlayerEnvironmentReadIdentifier("read:run_deck"));
        Assert.True(ConnectorMod.IsSafePlayerEnvironmentReadIdentifier("read:surface_card:card-a"));
        Assert.False(ConnectorMod.IsSafePlayerEnvironmentReadIdentifier("run_deck"));
        Assert.False(ConnectorMod.IsSafePlayerEnvironmentReadIdentifier("read:../hidden"));
        Assert.False(ConnectorMod.IsSafePlayerEnvironmentReadIdentifier("read:"));
    }

    [Fact]
    public void HiddenCombatPileOrderIsNotReportedAsMissingVisibleInformation()
    {
        IReadOnlyList<string> hidden =
            PlayerVisibleReadBuilder.HiddenByPolicyFor(
                PlayerVisibleReadBuilder.CombatPilesKind);
        PlayerEnvironmentCompleteness completeness =
            PlayerEnvironmentService.ToCompleteness(
                new PlayerReadCompleteness(
                    "complete_for_player_visible_combat_pile_contents_without_draw_order",
                    new[] { "NCardPileScreen player-visible card grid" },
                    Array.Empty<string>()),
                hidden);

        Assert.Equal("complete", completeness.Status);
        Assert.Empty(completeness.Missing);
        Assert.Equal(new[] { "draw_pile_true_order" }, completeness.HiddenByPolicy);
    }

    [Fact]
    public void CardBundleCardsReceiveReadOnlyLinkedDetailWithoutActionAuthority()
    {
        var surface = new CardBundleSelectionSurface(
            "card_bundle_selection",
            "choosing",
            "bundle-screen",
            "Choose a bundle.",
            null,
            new[] { "bundle-a" },
            CanConfirm: false,
            CanCancelPreview: false,
            new[]
            {
                new VisibleCardBundle(
                    "bundle-a",
                    new[]
                    {
                        TestCard("bundle-card-a", "Alpha"),
                        TestCard("bundle-card-b", "Beta")
                    })
            });

        PlayerEnvironmentLinkedDetailCatalogEntry[] catalog =
            PlayerEnvironmentService.BuildLinkedDetailCatalog(surface).ToArray();

        Assert.Equal(2, catalog.Length);
        Assert.All(catalog, entry => Assert.Equal("surface_card", entry.Kind));
        Assert.Equal(
            new[] { "bundle-card-a", "bundle-card-b" },
            catalog.Select(entry => entry.EntityId));
    }

    [Fact]
    public void DeckSelectorCommandsDependOnCurrentUiStateNotBusinessSource()
    {
        var cardA = TestCard("card-a", "Alpha");
        var cardB = TestCard("card-b", "Beta");
        var selecting = new NativeDeckCardSelectionSurface(
            NativeDeckCardSelection.SurfaceKind,
            "selecting",
            "screen-a",
            "Choose cards",
            1,
            2,
            1,
            new[] { "card-b" },
            new[] { "card-a" },
            new[] { "card-b" },
            Cancelable: true,
            CanPreview: true,
            CanCancelSelection: true,
            CanCancelPreview: false,
            CanConfirm: false,
            new[] { cardA, cardB });

        NativeUiActionDescriptor[] commands =
            NativeDeckCardSelection.DescribeCommands(selecting).ToArray();

        Assert.Contains(commands, command =>
            command.Kind == NativeDeckCardSelection.SelectOperation);
        Assert.Contains(commands, command =>
            command.Kind == NativeDeckCardSelection.DeselectOperation);
        Assert.Contains(commands, command =>
            command.Kind == NativeDeckCardSelection.PreviewOperation);
        Assert.Contains(commands, command =>
            command.Kind == NativeDeckCardSelection.CancelSelectionOperation);
        Assert.DoesNotContain(commands, command =>
            command.EvidenceCode.Contains("source", StringComparison.OrdinalIgnoreCase));
    }

    [Fact]
    public void DeckSelectorPreviewOnlyPublishesCurrentPreviewControls()
    {
        var surface = new NativeDeckCardSelectionSurface(
            NativeDeckCardSelection.SurfaceKind,
            "preview",
            "screen-a",
            "Confirm cards",
            1,
            1,
            1,
            new[] { "card-a" },
            Array.Empty<string>(),
            Array.Empty<string>(),
            Cancelable: false,
            CanPreview: false,
            CanCancelSelection: false,
            CanCancelPreview: true,
            CanConfirm: true,
            new[] { TestCard("card-a", "Alpha") });

        NativeUiActionDescriptor[] commands =
            NativeDeckCardSelection.DescribeCommands(surface).ToArray();

        Assert.Equal(2, commands.Length);
        Assert.Contains(commands, command =>
            command.Kind == NativeDeckCardSelection.CancelPreviewOperation);
        Assert.Contains(commands, command =>
            command.Kind == NativeDeckCardSelection.ConfirmOperation);
    }

    [Fact]
    public void CombatPileCommandsDependOnVisibleSelectionMechanicsNotBusinessSource()
    {
        var cardA = TestCard("card-a", "Alpha");
        var cardB = TestCard("card-b", "Beta");
        var surface = new NativeCombatPileSelectionSurface(
            NativeCombatPileSelection.SurfaceKind,
            "selecting",
            "screen-a",
            "Choose from discard pile",
            "discard",
            1,
            2,
            1,
            new[] { "card-b" },
            new[] { "card-a" },
            new[] { "card-b" },
            Cancelable: true,
            CanCancel: true,
            CanConfirm: true,
            new[] { cardA, cardB });

        NativeUiActionDescriptor[] commands =
            NativeCombatPileSelection.DescribeCommands(surface).ToArray();

        Assert.Equal(4, commands.Length);
        Assert.Contains(commands, command =>
            command.Kind == NativeCombatPileSelection.SelectOperation);
        Assert.Contains(commands, command =>
            command.Kind == NativeCombatPileSelection.DeselectOperation);
        Assert.Contains(commands, command =>
            command.Kind == NativeCombatPileSelection.CancelOperation);
        Assert.Contains(commands, command =>
            command.Kind == NativeCombatPileSelection.ConfirmOperation);
        Assert.All(commands, command =>
        {
            Assert.DoesNotContain("source", command.EvidenceCode, StringComparison.OrdinalIgnoreCase);
            Assert.DoesNotContain("contract", command.EvidenceCode, StringComparison.OrdinalIgnoreCase);
        });
    }

    [Fact]
    public void RestCommandsDependOnCurrentButtonsNotPurposeSpecificWitnesses()
    {
        var surface = new RestSiteSurface(
            NativeRestSite.SurfaceKind,
            "screen-rest",
            new[]
            {
                new VisibleRestOption("option-rest", 0, "REST", "Rest", "Heal", false),
                new VisibleRestOption("option-dig", 1, "DIG", "Dig", "Find a relic", true)
            },
            CanProceed: true);

        NativeUiActionDescriptor[] commands =
            NativeRestSite.DescribeCommands(surface).ToArray();

        Assert.Equal(2, commands.Length);
        Assert.Contains(commands, command =>
            command.Kind == "choose_rest_option"
            && command.EntityBindings?.Any(binding => binding.EntityId == "option-dig") == true);
        Assert.Contains(commands, command => command.Kind == "proceed_rest_site");
        Assert.All(commands, command =>
        {
            Assert.DoesNotContain("source", command.EvidenceCode, StringComparison.OrdinalIgnoreCase);
            Assert.DoesNotContain("witness", command.EvidenceCode, StringComparison.OrdinalIgnoreCase);
            Assert.DoesNotContain("outcome", command.EvidenceCode, StringComparison.OrdinalIgnoreCase);
        });
    }

    [Theory]
    [InlineData(true, true, true, true)]
    [InlineData(false, true, true, false)]
    [InlineData(true, false, true, false)]
    [InlineData(true, true, false, false)]
    public void RestActionabilityRequiresTheExactVisibleEnabledNativeControl(
        bool optionEnabled,
        bool buttonEnabled,
        bool buttonVisible,
        bool expected)
    {
        Assert.Equal(
            expected,
            NativeRestSite.IsOptionActionable(
                optionEnabled,
                buttonEnabled,
                buttonVisible));
    }

    [Fact]
    public void PlayerFactsComeFromCurrentUiMechanicsWithoutBusinessSourceFields()
    {
        var surface = new NativeCombatPileSelectionSurface(
            NativeCombatPileSelection.SurfaceKind,
            "selecting",
            "screen-a",
            "Choose from discard pile",
            "discard",
            1,
            1,
            0,
            Array.Empty<string>(),
            new[] { "card-a" },
            Array.Empty<string>(),
            Cancelable: true,
            CanCancel: true,
            CanConfirm: false,
            Cards: new[] { TestCard("card-a", "Alpha") });

        string facts = JsonSerializer.Serialize(
            PlayerEnvironmentService.ProjectVisibleFacts(
                surface,
                new UnknownLiveContext(
                    "unknown",
                    "PrivateOwnerType",
                    "Visible UI owner is not classified.")));

        Assert.Contains("pile_type", facts);
        Assert.Contains("card-a", facts);
        Assert.Contains("Visible UI owner is not classified.", facts);
        Assert.DoesNotContain("screen-a", facts);
        Assert.DoesNotContain("screen_entity_id", facts);
        Assert.DoesNotContain("source_kind", facts);
        Assert.DoesNotContain("source_type", facts);
        Assert.DoesNotContain("destination_pile", facts);
        Assert.DoesNotContain("mutation_kind", facts);
        Assert.DoesNotContain("commit_mode", facts);
    }

    [Fact]
    public void VisibleEntityFactsExistWithoutActionMaterialization()
    {
        JsonNode facts = JsonNode.Parse("""
        {"context":{"enemies":[{"entity_id":"enemy-a","name":"Visible enemy","hp":12}]}}
        """)!;

        IReadOnlyDictionary<string, PlayerEnvironmentReferent> referents =
            PlayerEnvironmentService.ProjectFactReferents(facts);

        PlayerEnvironmentReferent enemy = Assert.Contains("enemy-a", referents);
        Assert.Equal("enemy", enemy.Role);
        Assert.Equal("Visible enemy", enemy.Label);
        Assert.Null(enemy.State.Enabled);
        Assert.Equal("native_visible_fact", enemy.State.ObservationBasis);
    }

    [Fact]
    public void PublicShopFactsKeepOffersButHideNativeOwnerAndSlotBindings()
    {
        var surface = new ShopInventorySurface(
            "shop_inventory",
            "screen-private",
            new[]
            {
                new VisibleShopCardOffer(
                    "offer-visible",
                    "slot-private",
                    0,
                    45,
                    Stocked: true,
                    Visible: true,
                    Affordable: true,
                    CanPurchase: true,
                    BlockedReason: null,
                    OnSale: false,
                    TestCard("card-visible", "Strike"))
            },
            Array.Empty<VisibleShopRelicOffer>(),
            Array.Empty<VisibleShopPotionOffer>(),
            CardRemoval: null,
            CanClose: true);

        string facts = JsonSerializer.Serialize(
            PlayerEnvironmentService.ProjectVisibleFacts(
                surface,
                new ShopLiveContext("shop")));

        Assert.Contains("offer-visible", facts);
        Assert.Contains("card-visible", facts);
        Assert.DoesNotContain("screen-private", facts);
        Assert.DoesNotContain("slot-private", facts);
        Assert.DoesNotContain("screen_entity_id", facts);
        Assert.DoesNotContain("slot_entity_id", facts);
    }

    [Fact]
    public void FiniteProjectionCountExposesRatherThanHidesExpansionOverflow()
    {
        string[] cards = Enumerable.Range(0, 24).Select(i => $"card-{i}").ToArray();
        string[] targets = Enumerable.Range(0, 24).Select(i => $"target-{i}").ToArray();
        var candidate = Candidate(
            "candidate-many",
            "Choose",
            new Dictionary<string, NativeUiOperandDomain>
            {
                ["card_id"] = new("entity_id", cards),
                ["target_id"] = new("entity_id", targets)
            }) with
        {
            EntityBindings = cards.Select(id => new ActionEntityBinding("card", id))
                .Concat(targets.Select(id => new ActionEntityBinding("target", id)))
                .ToArray()
        };

        Assert.Equal(576, PlayerEnvironmentService.CountParameterCombinations(candidate));
        Dictionary<string, PlayerEnvironmentReferent> visibleReferents = cards
            .Select(id => VisibleReferent(id, "card"))
            .Concat(targets.Select(id => VisibleReferent(id, "target")))
            .ToDictionary(value => value.ReferentId, StringComparer.Ordinal);
        BoundActionProjectionResult projection = PlayerEnvironmentService.ProjectBoundActions(
            new[] { new NativeUiBoundAction(candidate) },
            "interaction-a",
            visibleReferents);
        Assert.Equal("truncated", projection.Projection.Status);
        Assert.Equal(512, projection.Projection.MaterializedCount);
        Assert.Equal(576, projection.Projection.TotalCount);
        Assert.Equal(512, projection.Bindings.Count);
        BoundActionProjectionResult rewardProfile =
            PlayerEnvironmentService.CloseIncompleteRewardCatalog(projection);
        Assert.Equal("unavailable", rewardProfile.Projection.Status);
        Assert.Equal(576, rewardProfile.Projection.TotalCount);
        Assert.Equal(0, rewardProfile.Projection.MaterializedCount);
        Assert.Empty(rewardProfile.Projection.Actions);
        Assert.Empty(rewardProfile.Bindings);
    }

    [Fact]
    public void NativeOperandsCannotCreatePlayerReferentsOrActionAuthority()
    {
        var candidate = Candidate(
            "candidate-hidden",
            "Choose hidden operand",
            new Dictionary<string, NativeUiOperandDomain>
            {
                ["card_id"] = new("entity_id", new[] { "card-not-observed" })
            }) with
        {
            EntityBindings = new[] { new ActionEntityBinding("card", "card-not-observed") }
        };

        BoundActionProjectionResult projection = PlayerEnvironmentService.ProjectBoundActions(
            new[] { new NativeUiBoundAction(candidate) },
            "interaction-a",
            new Dictionary<string, PlayerEnvironmentReferent>());

        Assert.Equal("truncated", projection.Projection.Status);
        Assert.Equal(0, projection.Projection.MaterializedCount);
        Assert.Equal(1, projection.Projection.TotalCount);
        Assert.Empty(projection.Bindings);
        BoundActionProjectionResult rewardProfile =
            PlayerEnvironmentService.CloseIncompleteRewardCatalog(projection);
        Assert.Equal("unavailable", rewardProfile.Projection.Status);
        Assert.Equal(1, rewardProfile.Projection.TotalCount);
        Assert.Empty(rewardProfile.Projection.Actions);
    }

    [Fact]
    public void ConsumerLabelsDoNotChangeCanonicalActionAuthorityIdentity()
    {
        NativeUiActionCandidate left = Candidate("candidate-a", "Old label");
        NativeUiActionCandidate right = left with { Label = "Consumer-friendly new label" };

        string leftSignature = PlayerEnvironmentService.CanonicalAuthoritySignature(
            new[] { new NativeUiBoundAction(left) });
        string rightSignature = PlayerEnvironmentService.CanonicalAuthoritySignature(
            new[] { new NativeUiBoundAction(right) });

        Assert.Equal(leftSignature, rightSignature);
    }

    private static NativeUiActionCandidate Candidate(
        string id,
        string label,
        IReadOnlyDictionary<string, NativeUiOperandDomain>? domains = null) => new(
        id,
        "choose",
        "test_choose",
        label,
        new Dictionary<string, string>(),
        domains ?? new Dictionary<string, NativeUiOperandDomain>(),
        Array.Empty<ActionEntityBinding>(),
        "native_ui");

    private static PlayerEnvironmentReferent VisibleReferent(string id, string role) => new(
        id,
        role,
        "entity",
        id,
        new PlayerEnvironmentReferentState(
            Visible: true,
            Enabled: true,
            Selected: false,
            Focused: false,
            ObservationBasis: "native_visible_fact"),
        PropertiesSchema: null,
        Properties: null);

    private static STS2Connector.LiveHost.Contracts.VisibleCard TestCard(
        string entityId,
        string name) => new(
            entityId,
            "test_card",
            name,
            "skill",
            "1",
            null,
            "Test description",
            "common",
            IsUpgraded: false,
            IsSelected: false,
            ExistingEnchantment: null);

    private static ModsetIdentity ExactConnectorModset() => new(
        "exact_player_environment_only",
        "fingerprint",
        "test_scope",
        new[]
        {
            new LoadedModIdentity(
                "STS2_MCP",
                ConnectorMod.Version,
                "ModsDirectory",
                "Loaded",
                false,
                null,
                new[]
                {
                    new LoadedModAssemblyIdentity(
                        "STS2_MCP",
                        ConnectorMod.Version,
                        "00000000-0000-0000-0000-000000000001")
                })
        },
        "Exact test Connector Modset.");
}
