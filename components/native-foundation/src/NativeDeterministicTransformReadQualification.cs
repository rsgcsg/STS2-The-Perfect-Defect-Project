using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Localization.DynamicVars;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Cards;
using MegaCrit.Sts2.Core.Models.Relics;

namespace STS2Platform.NativeFoundation;

public static partial class NativeCardPreviewReadQualification
{
    // Exact Claws preview lambda passes forPreview=true to this native factory.
    // Its false/Commit branch is never a read operation. No arbitrary delegate
    // is invoked to discover whether it happens to be deterministic.
    private static readonly MethodInfo? ClawsProjection = typeof(Claws).GetMethod("<AfterObtained>b__6_1", NativeMembers);

    internal static bool IsDeterministicTransformProjection(Func<CardModel, CardTransformation>? projection) =>
        typeof(CardModel).Module.ModuleVersionId == AuditedGameMvid
        && projection != null && projection.GetInvocationList().Length == 1
        && projection.Method == ClawsProjection && projection.Target?.GetType() == typeof(Claws)
        && IsUnpatched(ClawsProjection)
        && MethodsUnpatched(typeof(Claws), "CreateMaulFromOriginal")
        && IsUnpatched(typeof(CardTransformation).GetConstructor(new[] { typeof(CardModel), typeof(CardModel) }));

    internal static bool TryPreflightDeterministicTransform(CardModel original,
        Func<CardModel, CardTransformation>? projection, out string? gap)
    {
        gap = "native_deterministic_transform_delegate_unqualified";
        if (!IsDeterministicTransformProjection(projection)) return false;
        if (!TryPreflightCopy(original, out gap)) return false;
        gap = "native_claws_preview_factory_closure_unqualified";
        try
        {
            if (!MethodsUnpatched(typeof(RelicModel), "get_Owner")
                || !MethodsUnpatched(typeof(CardModel), "get_Owner", "get_IsUpgraded", "get_IsTransformable", "get_IsRemovable",
                    "get_Keywords", "get_Pile", "get_CombatState", "get_Type", "get_Rarity", "get_Tags", "ToMutable")
                || !MethodsUnpatched(typeof(CardTransformation), "AssertTransformable", "get_Original", "get_Replacement", "get_ReplacementOptions")
                || !MethodsUnpatched(typeof(AbstractModel), "AssertCanonical")
                || !MethodsUnpatched(typeof(ModelDb), "Card", "Get", "GetId")
                || !IsUnpatched(typeof(ModelDb).GetMethod("Card", NativeMembers)?.MakeGenericMethod(typeof(Maul)))
                || !typeof(ModelDb).GetMethods(NativeMembers).Where(method => method.IsGenericMethodDefinition
                    && method.GetGenericArguments().Length == 1 && method.Name is "Get" or "GetId")
                    .All(method => IsUnpatched(method.MakeGenericMethod(typeof(Maul))))
                || projection!.Target is not Claws claws || !claws.IsMutable || !ReferenceEquals(claws.Owner, original.Owner)) return false;

            // Obtain the fixed registered canonical by CLR field enumeration,
            // before the native factory's ModelDb lookup or lazy card getters.
            if (typeof(ModelDb).GetField("_contentById", NativeMembers)?.GetValue(null) is not Dictionary<ModelId, AbstractModel> registry
                || registry.GetType() != typeof(Dictionary<ModelId, AbstractModel>)
                || !ReferenceEquals(registry.Comparer, EqualityComparer<ModelId>.Default)) return false;
            CardModel? canonical = registry.Values.OfType<Maul>().SingleOrDefault();
            if (canonical == null || !CanonicalRegistryBindingQualified(canonical)
                || Mutable?.GetValue(canonical) is not false || !CopyClosureUnpatched()
                || !BaseImplementation(typeof(Maul), typeof(CardModel), "DeepCloneFields")
                || !BaseImplementation(typeof(Maul), typeof(CardModel), "AfterCloned")
                || !BaseImplementation(typeof(Maul), typeof(CardModel), "get_CanonicalKeywords")
                || !MethodsUnpatched(typeof(Maul), "get_CanonicalVars")
                || Enchantment?.GetValue(canonical) != null || Affliction?.GetValue(canonical) != null
                || !CopyCostQualified(canonical) || !UpgradeClosureQualified(typeof(Maul))
                || !FixedConstructorsUnpatched(typeof(DynamicVar)) || !FixedConstructorsUnpatched(typeof(DamageVar))
                || !MethodsUnpatched(typeof(DamageVar), "set_Props") || !ReadOnlyVariableArrayQualified()) return false;
            if (CardVars?.GetValue(canonical) is DynamicVarSet vars && !CopyVariablesQualified(canonical, vars)) return false;
            if (Canonical?.GetValue(canonical) is { } parent && !ReferenceEquals(parent, canonical)) return false;
            if (Keywords?.GetValue(canonical) is { } keywords && (keywords is not HashSet<CardKeyword> set
                || set.GetType() != typeof(HashSet<CardKeyword>) || !ReferenceEquals(set.Comparer, EqualityComparer<CardKeyword>.Default))) return false;
            if (typeof(CardModel).GetField("_temporaryStarCosts", NativeMembers)?.GetValue(canonical) is not List<TemporaryCardCost>) return false;

            if (Enchantment?.GetValue(original) is EnchantmentModel attached)
            {
                if (!MethodsUnpatched(typeof(EnchantmentModel), "get_CanonicalInstance", "CanEnchant", "CanEnchantCardType")
                    || !MethodsUnpatched(attached.GetType(), "CanEnchant", "CanEnchantCardType")
                    || !MethodsUnpatched(typeof(CardEnergyCost), "get_CostsX")
                    || !NativeRenderMethod(typeof(Maul).GetMethod("get_GainsBlock", NativeMembers))) return false;
                // Existing attachment-copy preflight owns MutableClone and its
                // initialized variables; reuse the mature model-only enchant
                // update closure for this same native enchantment type.
                var enchantmentCanonical = attached.CanonicalInstance;
                if (enchantmentCanonical.GetType() != attached.GetType()
                    || !TryPreflightSingleEnchant(original, enchantmentCanonical, out gap)) return false;
            }
            gap = null;
            return true;
        }
        catch { return false; }
    }

    private static bool ReadOnlyVariableArrayQualified()
    {
        Type? type = typeof(CardModel).Assembly.GetType("<>z__ReadOnlyArray`1")?.MakeGenericType(typeof(DynamicVar));
        return type != null && type.GetMethods(NativeMembers).All(IsUnpatched) && FixedConstructorsUnpatched(type)
            && type.GetNestedTypes(NativeMembers).All(nested => nested.GetMethods(NativeMembers).All(IsUnpatched)
                && FixedConstructorsUnpatched(nested));
    }
}
