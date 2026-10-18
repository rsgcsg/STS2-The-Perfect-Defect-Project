using System.Reflection;
using HarmonyLib;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using STS2Connector.NativeUi;

namespace STS2Connector
{
    [Collection("Native detached preview isolation")]
    public sealed class NativeSelectorObserverQualificationTests
    {
        private const string Observer = "STS2HumanAnnotator.Mod.NativeSelectorCardDecisionPatch";
        private const string Accepted = "STS2HumanAnnotator.Mod.NativeNestedSelectorAcceptedPatch";
        private const string Owner = "rsgcsg.sts2-human-annotator";
        private static MethodBase Original(Type type, string method) => AccessTools.DeclaredMethod(type, method)!;

        [Fact]
        public void AuditedObserverShapeAllowsOnlyTheExactNativeTargetsRolesAndHarmonyOwner()
        {
            foreach (Type type in new[] { typeof(NDeckCardSelectScreen), typeof(NDeckUpgradeSelectScreen),
                typeof(NDeckEnchantSelectScreen), typeof(NSimpleCardSelectScreen), typeof(NCombatPileCardSelectScreen), typeof(NDeckTransformSelectScreen) })
                foreach (string role in new[] { "Prefix", "Postfix", "Finalizer" })
                {
                    var original = Original(type, "OnCardClicked");
                    Assert.True(NativeSelectorPublicFacts.KnownObserverShape(original, Observer, role, role, Owner));
                    Assert.False(NativeSelectorPublicFacts.KnownObserverShape(original, Observer, role, role, "foreign-owner"));
                    Assert.False(NativeSelectorPublicFacts.KnownObserverShape(original, Accepted, role, role, Owner));
                    Assert.False(NativeSelectorPublicFacts.KnownObserverShape(original, Observer, role, "Transpiler", Owner));
                }
            foreach (Type type in new[] { typeof(NDeckCardSelectScreen), typeof(NDeckUpgradeSelectScreen), typeof(NDeckEnchantSelectScreen) })
                foreach (string method in new[] { "CloseSelection", "ConfirmSelection" })
                    Assert.True(NativeSelectorPublicFacts.KnownObserverShape(Original(type, method), Accepted, "Finalizer", "Finalizer", Owner));
            Assert.True(NativeSelectorPublicFacts.KnownObserverShape(Original(typeof(NSimpleCardSelectScreen), "CompleteSelection"), Accepted, "Postfix", "Postfix", Owner));
            Assert.True(NativeSelectorPublicFacts.KnownObserverShape(Original(typeof(NCombatPileCardSelectScreen), "CompleteSelection"), Accepted, "Prefix", "Prefix", Owner));
            Assert.False(NativeSelectorPublicFacts.KnownObserverShape(Original(typeof(NDeckCardSelectScreen), "CancelSelection"), Accepted, "Prefix", "Prefix", Owner));
            Assert.False(NativeSelectorPublicFacts.KnownObserverShape(Original(typeof(NDeckTransformSelectScreen), "ConfirmSelection"), Accepted, "Prefix", "Prefix", Owner));
            Assert.True(NativeSelectorPublicFacts.KnownObserverShape(Original(typeof(NDeckTransformSelectScreen), "CompleteSelection"), Accepted, "Prefix", "Prefix", Owner));
        }

        [Fact]
        public void AForeignModuleCannotImpersonateTheExactObserverClassMethodAndHarmonyOwner()
        {
            var original = Original(typeof(NDeckCardSelectScreen), "OnCardClicked");
            var observer = typeof(STS2HumanAnnotator.Mod.NativeSelectorCardDecisionPatch).GetMethod("Prefix")!;
            Assert.True(NativeSelectorPublicFacts.KnownObserverShape(original, observer.DeclaringType!.FullName, observer.Name, "Prefix", Owner));
            Assert.NotEqual(typeof(ConnectorMod).Module, observer.Module);
            var harmony = new Harmony(Owner);
            harmony.Patch(original, prefix: new HarmonyMethod(observer));
            try
            {
                Assert.False(NativeSelectorPublicFacts.SelectorMethodQualified(original));
                Assert.False(NativeSelectorPublicFacts.MechanicsQualified(typeof(NDeckCardSelectScreen)));
            }
            finally { harmony.Unpatch(original, HarmonyPatchType.Prefix, Owner); }
            Assert.True(NativeSelectorPublicFacts.MechanicsQualified(typeof(NDeckCardSelectScreen)));
        }
    }
}

// Deliberately in the test module: same public names/owner are not authority.
namespace STS2HumanAnnotator.Mod
{
    internal static class NativeSelectorCardDecisionPatch
    {
        public static void Prefix() { }
    }
}
