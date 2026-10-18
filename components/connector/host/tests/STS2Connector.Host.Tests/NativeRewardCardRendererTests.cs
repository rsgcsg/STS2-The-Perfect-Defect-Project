using System.Globalization;
using System.Reflection;
using System.Reflection.Emit;
using System.Runtime.CompilerServices;
using System.Text.Json;
using HarmonyLib;
using MegaCrit.Sts2.Core.Localization.DynamicVars;
using MegaCrit.Sts2.Core.Localization;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.CardPools;
using MegaCrit.Sts2.Core.Models.Cards;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using STS2Connector.LiveHost;
using STS2Platform.NativeFoundation;

namespace STS2Connector.Host.Tests;

[Collection("Native run hook read isolation")]
public sealed class NativeRewardCardRendererTests
{
    private static int RenderCallbacks;
    private static void ObserveRenderCall() => RenderCallbacks++;
    private static object SourceImpostor(Type contract, out FieldInfo callbackCount)
    {
        var assembly=AssemblyBuilder.DefineDynamicAssembly(new AssemblyName("HostileNativeSourceCounter"),AssemblyBuilderAccess.Run);
        var builder=assembly.DefineDynamicModule("fixture").DefineType("SmartFormat.Extensions.DefaultSource",TypeAttributes.Public|TypeAttributes.Sealed);
        builder.AddInterfaceImplementation(contract);builder.DefineDefaultConstructor(MethodAttributes.Public);
        var calls=builder.DefineField("Calls",typeof(int),FieldAttributes.Public|FieldAttributes.Static);
        foreach(var method in contract.GetInterfaces().Append(contract).SelectMany(type=>type.GetMethods()).Distinct())
        {
            var implementation=builder.DefineMethod(method.Name,MethodAttributes.Public|MethodAttributes.Virtual|MethodAttributes.Final|MethodAttributes.HideBySig|MethodAttributes.NewSlot,
                method.ReturnType,method.GetParameters().Select(value=>value.ParameterType).ToArray());
            var il=implementation.GetILGenerator();
            il.Emit(OpCodes.Ldsfld,calls);il.Emit(OpCodes.Ldc_I4_1);il.Emit(OpCodes.Add);il.Emit(OpCodes.Stsfld,calls);
            if(method.ReturnType!=typeof(void))
            {
                if(!method.ReturnType.IsValueType)il.Emit(OpCodes.Ldnull);
                else {var value=il.DeclareLocal(method.ReturnType);il.Emit(OpCodes.Ldloca,value);il.Emit(OpCodes.Initobj,method.ReturnType);il.Emit(OpCodes.Ldloc,value);}
            }
            il.Emit(OpCodes.Ret);builder.DefineMethodOverride(implementation,method);
        }
        Type type=builder.CreateType()!;callbackCount=type.GetField("Calls")!;
        return Activator.CreateInstance(type)!;
    }
    private static string FormatterDiagnostic()
    {
        var flags=BindingFlags.Instance|BindingFlags.Static|BindingFlags.Public|BindingFlags.NonPublic;
        var formatter=typeof(LocManager).GetField("_smartFormatter",flags)!.GetValue(null)!;
        var result=new List<string>{formatter.GetType().FullName!,formatter.GetType().Module.ModuleVersionId.ToString()};
        foreach(var property in formatter.GetType().GetProperties(flags).Where(value=>new[]{"Parser","Settings","SourceExtensions","FormatterExtensions"}.Contains(value.Name)))
        {
            var value=property.GetValue(formatter);result.Add(property.Name+"="+value?.GetType().FullName);
            if(value is System.Collections.IEnumerable entries)
                foreach(object entry in entries) result.Add("extension="+entry.GetType().FullName);
        }
        result.AddRange(formatter.GetType().GetMethods(flags|BindingFlags.DeclaredOnly).Where(method=>!NativeCardPreviewReadQualification.IsUnpatched(method)).Select(method=>"unpatched=false:"+method.Name));
        return string.Join("|",result);
    }

    private static void Set(object target,string name,object? value) => target.GetType().GetField(name,
        BindingFlags.Instance|BindingFlags.NonPublic)!.SetValue(target,value);
    [Fact]
    public void SameActualNativeRewardCardRetainsTheFullReferenceDtoAfterExactParentScreenBinding()
    {
        // Component fixture only: two synthetic localization entries isolate
        // the actual native card/SmartFormat renderer from Godot filesystem.
        // No card renderer, reward selection, legality or effect is patched.
        var instance=typeof(LocManager).GetProperty("Instance",BindingFlags.Static|BindingFlags.Public)!;
        object? previous=instance.GetValue(null);
        var manager=(LocManager)RuntimeHelpers.GetUninitializedObject(typeof(LocManager));
        var tables=new Dictionary<string,LocTable> { ["cards"]=new("cards",new Dictionary<string,string>
            { ["STRIKE_IRONCLAD.title"]="Strike", ["STRIKE_IRONCLAD.description"]="Deal {Damage:diff()} damage.",
              ["DEFEND_IRONCLAD.title"]="Defend", ["DEFEND_IRONCLAD.description"]="Gain {Block:diff()} Block." },null),
              ["static_hover_tips"]=new("static_hover_tips",new Dictionary<string,string> { ["BLOCK.title"]="Block", ["BLOCK.description"]="Reduces attack damage.",
                ["SUMMON_STATIC.title"]="Summon", ["SUMMON_STATIC.description"]="Summon a companion.",
                ["SUMMON_DYNAMIC.title"]="Summon", ["SUMMON_DYNAMIC.description"]="Summon a companion." },null) };
        instance.SetValue(null,manager);
        try
        {
            typeof(LocManager).GetMethod("LoadLocFormatters",BindingFlags.Instance|BindingFlags.NonPublic)!.Invoke(manager,null);
            Set(manager,"_tables",tables);Set(manager,"_engTables",tables);
            foreach(var pair in new Dictionary<string,object> { ["Language"]="eng",["CultureInfo"]=CultureInfo.GetCultureInfo("en"),
                ["StringComparer"]=StringComparer.Ordinal,["OverridesActive"]=false,["ValidationErrors"]=Array.Empty<LocValidationError>() })
                typeof(LocManager).GetProperty(pair.Key)!.SetValue(manager,pair.Value);
            ModelDb.Inject(typeof(StrikeIronclad));ModelDb.Inject(typeof(DefendIronclad));ModelDb.Inject(typeof(IroncladCardPool));
            var card=ModelDb.Card<StrikeIronclad>().ToMutable();
            typeof(CardModel).GetField("_pool",BindingFlags.Instance|BindingFlags.NonPublic)!.SetValue(card,ModelDb.CardPool<IroncladCardPool>());
            Assert.True(NativeCardPreviewReadQualification.TryPreflightRender(card,CardPreviewMode.Normal,PileType.None,out string? gap),gap+"|"+FormatterDiagnostic());
            var outer=LiveContextReader.BuildRewardCard(card,"card");
            var damage=card.DynamicVars["Damage"];
            decimal originalPreview=damage.PreviewValue;
            try
            {
                // Original Human01 occurrence41 retained an old hand preview4
                // on an actual discard Strike whose native fresh display is6.
                damage.PreviewValue=4;
                Assert.Contains("4",card.GetDescriptionForPile(PileType.Discard));
                var discard=LiveContextReader.BuildCard(card,"discard-card",
                    displayPile:PileType.Discard,strictRendering:true);
                Assert.Equal("Deal 6 damage.",discard.Description);
                Assert.Equal(4,damage.PreviewValue);
            }
            finally { damage.PreviewValue=originalPreview; }
            // These native overrides and calculated multipliers were rejected
            // by the old per-class/constant-IL admission. Preflight reads only
            // their callable identities; it does not load their hover assets.
            foreach (Type type in new[] { typeof(Barrage), typeof(BodySlam), typeof(Compact), typeof(Fuel) })
            {
                if (!ModelDb.Contains(type)) ModelDb.Inject(type);
                CardModel ordinary = ModelDb.GetById<CardModel>(ModelDb.GetId(type)).ToMutable();
                typeof(CardModel).GetField("_pool", BindingFlags.Instance | BindingFlags.NonPublic)!
                    .SetValue(ordinary, ModelDb.CardPool<IroncladCardPool>());
                Assert.True(NativeCardPreviewReadQualification.TryPreflightRender(ordinary,
                    CardPreviewMode.Normal, PileType.None, out string? ordinaryGap), type.Name + ":" + ordinaryGap);
            }

            var guard = new Harmony("sts2-test-public-card-render-preflight");
            var update = typeof(CardModel).GetMethod(nameof(CardModel.UpdateDynamicVarPreview))!;
            RenderCallbacks = 0;
            try
            {
                guard.Patch(update, prefix: new HarmonyMethod(typeof(NativeRewardCardRendererTests).GetMethod(nameof(ObserveRenderCall),BindingFlags.Static|BindingFlags.NonPublic)));
                Assert.False(NativeCardPreviewReadQualification.TryPreflightRender(card,CardPreviewMode.Normal,PileType.None,out string? patchedGap));
                Assert.Equal("native_card_render_fixed_closure_unqualified", patchedGap);
                Assert.False(NativePublicCardDetails.Capture(card,PileType.None).Complete);
                Assert.Equal(0,RenderCallbacks);
            }
            finally { guard.UnpatchAll(guard.Id); }
            var malicious = new CalculatedVar("Hostile").WithMultiplier((_,_)=> { RenderCallbacks++; return 7; });
            typeof(DynamicVar).GetField("_owner",BindingFlags.Instance|BindingFlags.NonPublic)!.SetValue(malicious,card);
            var nativeVariables = (Dictionary<string,DynamicVar>)typeof(DynamicVarSet).GetField("_vars",BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(card.DynamicVars)!;
            nativeVariables.Add("Hostile",malicious);
            try
            {
                Assert.False(NativeCardPreviewReadQualification.TryPreflightRender(card,CardPreviewMode.Normal,PileType.None,out string? delegateGap));
                Assert.Equal("native_card_render_current_values_unqualified",delegateGap);
                Assert.False(NativePublicCardDetails.Capture(card,PileType.None).Complete);
                Assert.Equal(0,RenderCallbacks);
            }
            finally { nativeVariables.Remove("Hostile"); }
            Assert.True(NativeCardPreviewReadQualification.TryPreflightRender(card,CardPreviewMode.Normal,PileType.None,out gap),gap);
            try
            {
                guard.Patch(typeof(IroncladCardPool).GetProperty("EnergyColorName")!.GetMethod!,
                    prefix:new HarmonyMethod(typeof(NativeRewardCardRendererTests).GetMethod(nameof(ObserveRenderCall),BindingFlags.Static|BindingFlags.NonPublic)));
                Assert.False(NativeCardPreviewReadQualification.TryPreflightRender(card,CardPreviewMode.Normal,PileType.None,out string? poolGap));
                Assert.Equal("native_card_render_current_values_unqualified",poolGap);
                Assert.False(NativePublicCardDetails.Capture(card,PileType.None).Complete);
                Assert.Equal(0,RenderCallbacks);
            }
            finally { guard.UnpatchAll(guard.Id); }
            ModelDb.Inject(typeof(Zap));ModelDb.Inject(typeof(MegaCrit.Sts2.Core.Models.Orbs.LightningOrb));
            var zap=ModelDb.Card<Zap>().ToMutable();
            typeof(CardModel).GetField("_pool",BindingFlags.Instance|BindingFlags.NonPublic)!.SetValue(zap,ModelDb.CardPool<IroncladCardPool>());
            var cache=MegaCrit.Sts2.Core.Assets.PreloadManager.Cache;
            int missed=cache.MissedCacheAssetCount;
            string[] cached=cache.GetCacheKeys().ToArray();
            Assert.True(NativeCardPreviewReadQualification.TryPreflightRender(zap,CardPreviewMode.Normal,PileType.None,out string? iconGap), iconGap);
            Assert.Equal(missed,cache.MissedCacheAssetCount);Assert.Equal(cached,cache.GetCacheKeys());
            var formatter=typeof(LocManager).GetField("_smartFormatter",BindingFlags.Static|BindingFlags.NonPublic)!.GetValue(null)!;
            var sourcesProperty=formatter.GetType().GetProperty("SourceExtensions",BindingFlags.Instance|BindingFlags.NonPublic)!;
            var sources=(System.Collections.IList)sourcesProperty.GetValue(formatter)!;
            var impostor=SourceImpostor(sourcesProperty.PropertyType.GetGenericArguments()[0],out var impostorCalls);
            sources.Insert(0,impostor);
            try
            {
                Assert.Equal("SmartFormat.Extensions.DefaultSource",impostor.GetType().FullName);
                Assert.False(NativeCardPreviewReadQualification.TryPreflightRender(card,CardPreviewMode.Normal,PileType.None,out string? impostorGap));
                Assert.Equal("native_card_render_formatter_roster_unqualified",impostorGap);
                Assert.False(NativePublicCardDetails.Capture(card,PileType.None).Complete);
                Assert.Equal(0,(int)impostorCalls.GetValue(null)!);
            }
            finally { sources.Remove(impostor); }
            Assert.Equal("Deal 6 damage.",outer.Description);Assert.Equal("AnyEnemy",outer.TargetType);
            var defend=ModelDb.Card<DefendIronclad>().ToMutable();
            typeof(CardModel).GetField("_pool",BindingFlags.Instance|BindingFlags.NonPublic)!.SetValue(defend,ModelDb.CardPool<IroncladCardPool>());
            var defender=NativePublicCardDetails.Capture(defend,PileType.None);
            Assert.True(defender.Complete,string.Join(",",defender.Missing));
            var keywords=(List<Dictionary<string,object?>>)defender.Card["keywords"]!;
            Assert.Equal("Block",keywords.Single()["name"]);
            Assert.Equal("Reduces attack damage.",keywords.Single()["description"]);
            var nativeOptions=new List<CardCreationResult>{new(card)};
            var parent=(CardReward)RuntimeHelpers.GetUninitializedObject(typeof(CardReward));Set(parent,"_cards",nativeOptions);
            var screen=(NCardRewardSelectionScreen)RuntimeHelpers.GetUninitializedObject(typeof(NCardRewardSelectionScreen));
            using(NativeCardRewardDecisionProvider.BeginSynchronousOnSelect(parent))
                NativeCardRewardDecisionProvider.RegisterFromShowScreen(screen,nativeOptions,Array.Empty<MegaCrit.Sts2.Core.Entities.CardRewardAlternatives.CardRewardAlternative>());
            Assert.Same(parent,NativeCardRewardDecisionProvider.CaptureParentFacts(screen).ParentReward);
            var opened=LiveContextReader.BuildRewardCard(nativeOptions.Single().Card,"card");
            Assert.Equal(JsonSerializer.Serialize(outer,ConnectorMod._jsonOptions),JsonSerializer.Serialize(opened,ConnectorMod._jsonOptions));
        }
        finally {instance.SetValue(null,previous);}
    }
}
