using STS2Connector.Authority;
using System;
using System.Collections.Generic;
using System.Linq;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2Connector.NativeUi;

namespace STS2Connector.PlayerEnvironment;

internal static partial class PlayerEnvironmentService
{
    /// <summary>The text page's combat entry actions are supplied from current
    /// native holders. Retain only the old exact end-turn binding so the legacy
    /// card-by-target expansion cannot truncate an unrelated text menu.</summary>
    internal static IReadOnlyList<NativeUiActionDescriptor> DescribeTextCombatCommands(
        CombatTurnSurface surface) => NativeUiActionRuntime.DescribeCombatCommands(
            surface with
            {
                PlayableCards = Array.Empty<VisibleCombatCommandOption>(),
                UsablePotions = Array.Empty<VisibleCombatCommandOption>()
            });

    private static IReadOnlyList<NativeUiBoundAction> BuildTextCombatBindings(
        LiveObservation draft, CombatTurnSurface surface) =>
        DescribeTextCombatCommands(surface)
            .Select(descriptor => NativeUiActionRuntime.BindActionToCurrentObservation(
                draft, descriptor))
            .Where(binding => binding != null)
            .Cast<NativeUiBoundAction>()
            .ToArray();

    private static IReadOnlyList<NativeUiBoundAction> BuildPlayerEnvironmentBindings(
        LiveObservation draft)
    {
        IReadOnlyList<NativeUiActionDescriptor>? descriptors = draft.Surface switch
        {
            NativeDeckUpgradeSelectionSurface deckUpgradeSelection =>
                NativeDeckUpgradeSelection.DescribeCommands(deckUpgradeSelection),
            NativeDeckCardSelectionSurface deckSelection =>
                NativeDeckCardSelection.DescribeCommands(deckSelection),
            NativeCombatPileSelectionSurface combatPileSelection =>
                NativeCombatPileSelection.DescribeCommands(combatPileSelection),
            NativeGeneratedCardChoiceSurface generatedChoice =>
                NativeGeneratedCardChoice.DescribeCommands(generatedChoice),
            NativeBossRelicSelectionSurface bossRelicSelection =>
                NativeBossRelicSelection.DescribeCommands(bossRelicSelection),
            NativeSimpleCardSelectionSurface simpleSelection =>
                NativeSimpleCardSelection.DescribeCommands(simpleSelection),
            RestSiteSurface restSite =>
                NativeRestSite.DescribeCommands(restSite),
            _ => null
        };
        if (descriptors == null)
            return NativeUiActionRuntime.BuildBindings(draft);

        return descriptors
            .Select(descriptor => NativeUiActionRuntime.BindActionToCurrentObservation(draft, descriptor))
            .Where(binding => binding != null)
            .Cast<NativeUiBoundAction>()
            .ToArray();
    }

    internal static BoundActionProjectionResult ProjectBoundActions(
            IReadOnlyList<NativeUiBoundAction> bindings,
            string interactionId,
            IReadOnlyDictionary<string, PlayerEnvironmentReferent> visibleReferents)
    {
        var actions = new List<PlayerEnvironmentBoundAction>();
        var exactBindings = new Dictionary<string, PlayerEnvironmentNativeBinding>(StringComparer.Ordinal);
        long totalCount = 0;
        foreach (NativeUiBoundAction binding in bindings
            .OrderBy(item => item.Candidate.CandidateId, StringComparer.Ordinal))
        {
            totalCount = SaturatingAdd(totalCount, CountParameterCombinations(binding.Candidate));
            int remaining = MaxBoundActions - actions.Count;
            if (remaining <= 0)
                continue;
            foreach (IReadOnlyDictionary<string, string> parameters in ExpandParameters(binding.Candidate, remaining))
            {
                if (!TryPublicBoundActionBindings(
                        binding.Candidate,
                        parameters,
                        visibleReferents,
                        out IReadOnlyList<PlayerEnvironmentBoundActionArgument>? publicBindings))
                {
                    continue;
                }
                int subjectIndex = SubjectBindingIndex(binding.Candidate.Command, publicBindings);
                string? subjectReferentId = subjectIndex < 0
                    ? null
                    : publicBindings[subjectIndex].ReferentId;
                PlayerEnvironmentBoundActionArgument[] arguments = publicBindings
                    .Where((_, index) => index != subjectIndex)
                    .ToArray();
                string action = GenericAction(binding.Candidate.Command, binding.Candidate.Operation);
                string boundActionId = "bound_action_" + StableIdentityHash.Object(new
                {
                    binding.Candidate.CandidateId,
                    binding.Candidate.Command,
                    action,
                    interactionId,
                    subjectReferentId,
                    arguments,
                    parameters = parameters.OrderBy(pair => pair.Key, StringComparer.Ordinal).ToArray()
                })[..20];
                string label = BoundActionLabel(binding.Candidate.Label, arguments, visibleReferents);
                var projected = new PlayerEnvironmentBoundAction(
                        boundActionId,
                        action,
                        interactionId,
                        subjectReferentId,
                        arguments,
                        label);
                if (exactBindings.TryAdd(
                    boundActionId,
                    new PlayerEnvironmentNativeBinding(binding, parameters)))
                {
                    actions.Add(projected);
                }
            }
        }
        string status = totalCount == actions.Count ? "complete" : "truncated";
        return new BoundActionProjectionResult(
            new PlayerEnvironmentBoundActionProjection(
                "sts2.player-environment/bound-actions-1",
                status,
                actions.Count,
                totalCount,
                MaxBoundActions,
                "candidate_id_then_operand_name_then_referent_id",
                actions),
            exactBindings);
    }

    private static IReadOnlyList<PlayerEnvironmentInteractionCapability>
        ProjectInteractionCapabilities(
            PlayerEnvironmentBoundActionProjection projection,
            IReadOnlyDictionary<string, PlayerEnvironmentReferent> visibleReferents) =>
        projection.Status != "complete"
            ? Array.Empty<PlayerEnvironmentInteractionCapability>()
            : projection.Actions
            .Select(action => new PlayerEnvironmentInteractionCapability(
                action.Verb,
                action.SubjectReferentId != null
                    && visibleReferents.TryGetValue(
                        action.SubjectReferentId,
                        out PlayerEnvironmentReferent? subject)
                    ? subject.Role
                    : null,
                action.Arguments
                    .Select(argument => new PlayerEnvironmentCapabilityArgument(
                        argument.Role,
                        Required: true))
                    .ToArray(),
                "current_native_interaction"))
            .GroupBy(value => StableIdentityHash.Object(value), StringComparer.Ordinal)
            .Select(group => group.First())
            .OrderBy(value => value.Verb, StringComparer.Ordinal)
            .ThenBy(value => value.SubjectRole, StringComparer.Ordinal)
            .ToArray();

    internal static long CountParameterCombinations(NativeUiActionCandidate candidate)
    {
        long count = 1;
        foreach (NativeUiOperandDomain domain in candidate.OperandDomains.Values)
        {
            if (domain.EntityIds.Count == 0)
                return 0;
            if (count > long.MaxValue / domain.EntityIds.Count)
                return long.MaxValue;
            count *= domain.EntityIds.Count;
        }
        return count;
    }

    private static long SaturatingAdd(long left, long right) =>
        left > long.MaxValue - right ? long.MaxValue : left + right;

    internal static string CanonicalAuthoritySignature(
        IReadOnlyList<NativeUiBoundAction> bindings) => StableIdentityHash.Object(
        bindings.Select(binding => new
        {
            binding.Candidate.CandidateId,
            binding.Candidate.Command,
            binding.Candidate.Operation,
            Operands = binding.Candidate.Operands.OrderBy(pair => pair.Key, StringComparer.Ordinal).ToArray(),
            Domains = binding.Candidate.OperandDomains
                .OrderBy(pair => pair.Key, StringComparer.Ordinal)
                .Select(pair => new
                {
                    pair.Key,
                    pair.Value.Kind,
                    EntityIds = pair.Value.EntityIds.OrderBy(value => value, StringComparer.Ordinal).ToArray()
                }).ToArray(),
            EntityBindings = binding.Candidate.EntityBindings
                .OrderBy(value => value.Role, StringComparer.Ordinal)
                .ThenBy(value => value.EntityId, StringComparer.Ordinal)
                .Select(value => new { value.Role, value.EntityId })
                .ToArray(),
            binding.Candidate.BindingKind
        }).OrderBy(value => value.CandidateId, StringComparer.Ordinal).ToArray());

    private static bool TryPublicBoundActionBindings(
        NativeUiActionCandidate candidate,
        IReadOnlyDictionary<string, string> parameters,
        IReadOnlyDictionary<string, PlayerEnvironmentReferent> visibleReferents,
        out IReadOnlyList<PlayerEnvironmentBoundActionArgument> bindings)
    {
        var result = new List<PlayerEnvironmentBoundActionArgument>();
        foreach ((string name, string value) in parameters)
        {
            if (!name.EndsWith("_id", StringComparison.Ordinal) || IsPrivateBindingParameter(name))
                continue;
            if (!visibleReferents.ContainsKey(value))
            {
                bindings = Array.Empty<PlayerEnvironmentBoundActionArgument>();
                return false;
            }
            ActionEntityBinding? entityBinding = candidate.EntityBindings.FirstOrDefault(item =>
                string.Equals(item.EntityId, value, StringComparison.Ordinal)
                && !IsOwnerRole(item.Role)
                && !IsPrivateBindingRole(item.Role));
            string role = PublicRole(entityBinding?.Role ?? name);
            if (!result.Any(item => string.Equals(item.Role, role, StringComparison.Ordinal)
                && string.Equals(item.ReferentId, value, StringComparison.Ordinal)))
            {
                result.Add(new PlayerEnvironmentBoundActionArgument(role, value));
            }
        }
        bindings = result;
        return true;
    }

    private static int SubjectBindingIndex(
        string command,
        IReadOnlyList<PlayerEnvironmentBoundActionArgument> bindings)
    {
        return SubjectRoleIndex(command, bindings.Select(item => item.Role).ToArray());
    }

    private static int SubjectRoleIndex(string command, IReadOnlyList<string> roles)
    {
        if (roles.Count == 0)
            return -1;
        string[] preferred = command switch
        {
            "play_card" => new[] { "card" },
            "use_potion" => new[] { "potion" },
            "select_entity" or "deselect_entity" => new[] { "card", "option", "item", "entity" },
            "purchase" => new[] { "item", "card", "relic", "potion" },
            "navigate" => new[] { "map_choice", "node" },
            "choose" => new[] { "option", "choice", "card", "reward" },
            _ => Array.Empty<string>()
        };
        foreach (string role in preferred)
        {
            int index = roles.ToList().FindIndex(item => item.Contains(role, StringComparison.Ordinal));
            if (index >= 0)
                return index;
        }
        int nonTarget = roles.ToList().FindIndex(item => item != "target");
        return nonTarget >= 0 ? nonTarget : 0;
    }

    private static string BoundActionLabel(
        string label,
        IReadOnlyList<PlayerEnvironmentBoundActionArgument> arguments,
        IReadOnlyDictionary<string, PlayerEnvironmentReferent> referents)
    {
        PlayerEnvironmentBoundActionArgument? target = arguments.FirstOrDefault(item => item.Role == "target");
        if (target == null
            || !referents.TryGetValue(target.ReferentId, out PlayerEnvironmentReferent? referent)
            || string.IsNullOrWhiteSpace(referent.Label)
            || label.Contains(referent.Label, StringComparison.Ordinal))
            return label;
        return $"{label} -> {referent.Label}";
    }

    private static IEnumerable<IReadOnlyDictionary<string, string>> ExpandParameters(
        NativeUiActionCandidate candidate,
        int limit)
    {
        var current = new List<Dictionary<string, string>>
        {
            new(candidate.Operands, StringComparer.Ordinal)
        };
        foreach ((string name, NativeUiOperandDomain domain) in candidate.OperandDomains
            .OrderBy(pair => pair.Key, StringComparer.Ordinal))
        {
            current = current.SelectMany(parameters => domain.EntityIds
                .OrderBy(entityId => entityId, StringComparer.Ordinal)
                .Select(entityId =>
            {
                var expanded = new Dictionary<string, string>(parameters, StringComparer.Ordinal)
                {
                    [name] = entityId
                };
                return expanded;
            })).Take(limit).ToList();
        }
        return current;
    }

    private static string SurfaceContentSchema(string surfaceKind) =>
        $"sts2.player-environment/surface/{surfaceKind}-1";

    private static string ReferentPropertiesSchema(string role) =>
        $"sts2.player-environment/referent/{role}-1";

    internal static string GenericAction(string command, string operation) => command switch
    {
        "play_card" => "play",
        "use_potion" => "use",
        "end_turn" => "end_turn",
        "select_entity" => "select",
        "deselect_entity" => "deselect",
        "confirm_interaction" => "confirm",
        "cancel_interaction" => "cancel",
        "purchase" or "choose" or "navigate" or "activate_control" =>
            operation.Contains("skip", StringComparison.Ordinal) ? "skip" :
            operation.Contains("close", StringComparison.Ordinal) || operation.Contains("leave", StringComparison.Ordinal) ? "close" :
            operation.Contains("open", StringComparison.Ordinal) ? "open" : "activate",
        _ => "activate"
    };

    private static bool IsOwnerRole(string role) =>
        role.Contains("screen", StringComparison.Ordinal)
        || role.Contains("room", StringComparison.Ordinal)
        || role is "hand" or "owner";

    private static bool IsPrivateBindingRole(string role) =>
        IsOwnerRole(role)
        || role.Contains("source", StringComparison.Ordinal)
        || role.Contains("annotation_input", StringComparison.Ordinal)
        || role is "dialogue_line";

    private static bool IsPrivateBindingParameter(string name) =>
        name is "screen_id" or "room_id" or "hand_id" or "source_id" or "control_id"
            or "menu_screen_id" or "game_over_screen_id" or "map_screen_id"
            or "map_annotation_input_id" or "dialogue_line_id"
        || name.EndsWith("_screen_id", StringComparison.Ordinal)
        || name.EndsWith("_room_id", StringComparison.Ordinal);

    private static string PublicRole(string role)
    {
        string normalized = role.EndsWith("_id", StringComparison.Ordinal)
            ? role[..^3]
            : role;
        return normalized switch
        {
            "map_node" or "destination" => "destination",
            "character_choice" => "character",
            "rest_option" or "option" or "choice" or "alternative" => "option",
            "shop_offer" => "offer",
            "shop_card_removal" => "service",
            _ => SchemaToken(normalized)
        };
    }

    private static string SchemaToken(string value)
    {
        char[] token = value
            .ToLowerInvariant()
            .Select(character => char.IsLetterOrDigit(character) || character == '_'
                ? character
                : '_')
            .ToArray();
        string normalized = new string(token).Trim('_');
        return string.IsNullOrWhiteSpace(normalized) ? "entity" : normalized;
    }

}
