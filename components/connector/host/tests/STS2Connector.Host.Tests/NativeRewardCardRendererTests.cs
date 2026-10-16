using System.Globalization;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text.Json;
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
            { ["STRIKE_IRONCLAD.title"]="Strike", ["STRIKE_IRONCLAD.description"]="Deal {Damage:diff()} damage." },null) };
        instance.SetValue(null,manager);
        try
        {
            typeof(LocManager).GetMethod("LoadLocFormatters",BindingFlags.Instance|BindingFlags.NonPublic)!.Invoke(manager,null);
            Set(manager,"_tables",tables);Set(manager,"_engTables",tables);
            foreach(var pair in new Dictionary<string,object> { ["Language"]="eng",["CultureInfo"]=CultureInfo.GetCultureInfo("en"),
                ["StringComparer"]=StringComparer.Ordinal,["OverridesActive"]=false,["ValidationErrors"]=Array.Empty<LocValidationError>() })
                typeof(LocManager).GetProperty(pair.Key)!.SetValue(manager,pair.Value);
            ModelDb.Inject(typeof(StrikeIronclad));ModelDb.Inject(typeof(IroncladCardPool));
            var card=ModelDb.Card<StrikeIronclad>().ToMutable();
            typeof(CardModel).GetField("_pool",BindingFlags.Instance|BindingFlags.NonPublic)!.SetValue(card,ModelDb.CardPool<IroncladCardPool>());
            var outer=LiveContextReader.BuildRewardCard(card,"card");
            Assert.Equal("Deal 6 damage.",outer.Description);Assert.Equal("AnyEnemy",outer.TargetType);
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
