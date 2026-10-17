using System;
using System.Linq;
using System.Reflection;
using HarmonyLib;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;

namespace STS2Platform.NativeFoundation;

internal static class NativeSelectorReadQualification
{
    // The unified Mod's existing recorder observes these callbacks. It never
    // changes their selection result or suppresses the original native body.
    // This permission is local to selector mechanics, never public formatting.
    internal static bool SelectorMethodQualified(MethodBase original, Module observerModule, string decisionObserver, string acceptedObserver, string observerOwner)
    {
        if (Harmony.GetPatchInfo(original) is not { } patches) return true;
        bool Allowed(Patch patch, string role) => patch.PatchMethod.Module == observerModule
            && KnownObserverShape(original, patch.PatchMethod.DeclaringType?.FullName, patch.PatchMethod.Name, role, patch.owner, decisionObserver, acceptedObserver, observerOwner);
        return patches.Transpilers.Count == 0
            && patches.Prefixes.All(patch => Allowed(patch, "Prefix"))
            && patches.Postfixes.All(patch => Allowed(patch, "Postfix"))
            && patches.Finalizers.All(patch => Allowed(patch, "Finalizer"));
    }

    internal static bool KnownObserverShape(MethodBase original, string? patchType, string patchMethod,
        string role, string harmonyOwner, string decisionObserver, string acceptedObserver, string observerOwner)
    {
        if (harmonyOwner != observerOwner || patchMethod != role
            || role is not ("Prefix" or "Postfix" or "Finalizer")
            || original.DeclaringType is not { } owner || !Supported(owner)) return false;
        Type[] args = original.GetParameters().Select(parameter => parameter.ParameterType).ToArray();
        if (original.Name == "OnCardClicked" && args.SequenceEqual(new[] { typeof(CardModel) }))
            return patchType == decisionObserver;
        bool simpleCompletion = owner == typeof(NSimpleCardSelectScreen) || owner == typeof(NCombatPileCardSelectScreen);
        bool accepted = simpleCompletion ? original.Name == "CompleteSelection" && args.Length == 0
            : args.SequenceEqual(new[] { typeof(NButton) }) && (owner == typeof(NDeckTransformSelectScreen)
                ? original.Name is "CloseSelection" or "CompleteSelection"
                : original.Name is "CloseSelection" or "ConfirmSelection");
        return accepted && patchType == acceptedObserver;
    }

    private static bool Supported(Type type) => type == typeof(NDeckCardSelectScreen)
        || type == typeof(NDeckUpgradeSelectScreen) || type == typeof(NDeckEnchantSelectScreen)
        || type == typeof(NSimpleCardSelectScreen) || type == typeof(NCombatPileCardSelectScreen)
        || type == typeof(NDeckTransformSelectScreen);
}
