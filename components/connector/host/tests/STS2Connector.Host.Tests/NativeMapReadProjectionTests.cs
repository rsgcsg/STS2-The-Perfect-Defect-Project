using System.Reflection;
using System.Collections;
using HarmonyLib;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Modding;
using MegaCrit.Sts2.Core.Models.Relics;
using MegaCrit.Sts2.Core.Models.Modifiers;
using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Singleton;
using MegaCrit.Sts2.Core.Runs;
using STS2Platform.NativeFoundation;

namespace STS2Connector.Host.Tests;

[Collection("Native run hook read isolation")]
public sealed class NativeMapReadProjectionTests
{
    private static T Bare<T>() => (T)RuntimeHelpers.GetUninitializedObject(typeof(T));
    private static void Set(object target, string name, object? value)
    {
        for (Type? type = target.GetType(); type != null; type = type.BaseType)
            if (type.GetField(name, BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public | BindingFlags.DeclaredOnly) is { } field)
            { field.SetValue(target, value); return; }
        throw new MissingFieldException(name);
    }
    private static (RunState Run, MockCraftedActMap Map, List<MapCoord> Visited) Fixture()
    {
        var start = new MapPoint(0,-1) { PointType=MapPointType.Ancient };
        var boss = new MapPoint(0,2) { PointType=MapPointType.Boss };
        var map = new MockCraftedActMap(2,2,start,boss);
        map.Put(0,0); map.Put(1,0); map.Put(0,1);
        start.AddChildPoint(map.GetPoint(0,0)!);
        map.GetPoint(0,0)!.AddChildPoint(map.GetPoint(0,1)!);
        map.GetPoint(0,1)!.AddChildPoint(boss);
        RunState run=Bare<RunState>(); var visited=new List<MapCoord>();
        Set(run,"<Map>k__BackingField",map); Set(run,"_visitedMapCoords",visited);
        Set(run,"_players",new List<MegaCrit.Sts2.Core.Entities.Players.Player>());
        Set(run,"<Modifiers>k__BackingField",Array.Empty<ModifierModel>());
        Set(run,"<BadgeModels>k__BackingField",Array.Empty<BadgeModel>());
        Set(run,"<MultiplayerScalingModel>k__BackingField",Bare<MultiplayerScalingModel>());
        return(run,map,visited);
    }
    [Fact]
    public void InitialCurrentMapHasEveryGridAndSpecialNodeAndNativeUntravelableBaseline()
    {
        var (run,map,_)=Fixture(); var captured=NativeMapReadProjection.Capture(run);
        Assert.True(captured.Status=="captured",captured.Gap);
        Assert.Null(captured.Current); Assert.Empty(captured.Visited); Assert.Equal(5,captured.Nodes.Count);
        Assert.Equal("travelable",captured.Nodes.Single(node=>ReferenceEquals(node.Point,map.StartingMapPoint)).State);
        Assert.All(captured.Nodes.Where(node=>!ReferenceEquals(node.Point,map.StartingMapPoint)),node=>Assert.Equal("untravelable",node.State));
    }
    [Fact]
    public void OrdinaryChildrenThenLastRowBossUseSameNativeDestinationOwner()
    {
        var (run,map,visited)=Fixture();visited.Add(map.StartingMapPoint.coord);
        var captured=NativeMapReadProjection.Capture(run);Assert.True(captured.Status=="captured",captured.Gap);
        Assert.Equal("traveled",captured.Nodes.Single(node=>ReferenceEquals(node.Point,map.StartingMapPoint)).State);
        Assert.Same(map.GetPoint(0,0),captured.Nodes.Single(node=>node.State=="travelable").Point);
        visited.Add(map.GetPoint(0,1)!.coord);
        captured=NativeMapReadProjection.Capture(run);Assert.True(captured.Status=="captured",captured.Gap);
        Assert.Same(map.BossMapPoint,captured.Nodes.Single(node=>node.State=="travelable").Point);
    }
    [Fact]
    public void SecondBossAndNativeSpecialBranchesNeverInvokeUnknownTravelHook()
    {
        var (run,original,visited)=Fixture();
        var map=Bare<StandardActMap>();
        Set(map,"<Grid>k__BackingField",new MapPoint?[,]{{original.GetPoint(0,0),original.GetPoint(0,1)},{original.GetPoint(1,0),null}});
        Set(map,"<StartingMapPoint>k__BackingField",original.StartingMapPoint);
        Set(map,"<BossMapPoint>k__BackingField",original.BossMapPoint);
        var second=new MapPoint(0,3){PointType=MapPointType.Boss};
        Set(map,"<SecondBossMapPoint>k__BackingField",second);map.BossMapPoint.AddChildPoint(second);
        Set(run,"<Map>k__BackingField",map);
        int calls=0;var registry=(IList)typeof(ModHelper).GetField("_runHookSubscribers",BindingFlags.Static|BindingFlags.NonPublic)!.GetValue(null)!;
        try
        {
            ModHelper.SubscribeForRunStateHooks("map-unknown-subscriber",_=>{calls++;return Array.Empty<AbstractModel>();});
            foreach(var last in new MapCoord?[]{null,original.GetPoint(0,1)!.coord,map.BossMapPoint.coord})
            {
                visited.Clear();if(last!=null)visited.Add(last.Value);
                var capture=NativeMapReadProjection.Capture(run);Assert.True(capture.Status=="captured",capture.Gap);
                Assert.Equal(6,capture.Nodes.Count);
                Assert.Same(last==null?map.StartingMapPoint:last==map.BossMapPoint.coord?second:map.BossMapPoint,
                    capture.Nodes.Single(node=>node.State=="travelable").Point);
            }
            Assert.Equal(0,calls);
            visited.Clear();visited.Add(map.StartingMapPoint.coord);
            var rejected=NativeMapReadProjection.Capture(run);Assert.Equal("unsupported",rejected.Status);Assert.Equal(0,calls);
        }
        finally{registry.Clear();}
    }
    private static int unknownCalls;
    private static bool UnknownFreeTravel(ref bool __result){unknownCalls++;__result=true;return false;}
    [Fact]
    public void PatchedNativeFreeTravelBodyIsRejectedBeforeInvocation()
    {
        var(run,map,visited)=Fixture();visited.Add(map.StartingMapPoint.coord);
        var harmony=new Harmony("map-read-negative");unknownCalls=0;
        try
        {
            harmony.Patch(typeof(AbstractModel).GetMethod(nameof(AbstractModel.ShouldAllowFreeTravel)),
                prefix:new HarmonyMethod(typeof(NativeMapReadProjectionTests),nameof(UnknownFreeTravel)));
            Assert.Equal("unsupported",NativeMapReadProjection.Capture(run).Status);Assert.Equal(0,unknownCalls);
        }
        finally{harmony.UnpatchAll(harmony.Id);}
    }
    [Fact]
    public void RegeneratedHistoryMayContainUnresolvedPastAndCurrentCoordinatesWithNativeStartingFallback()
    {
        var(run,map,visited)=Fixture();visited.Add(new MapCoord(9,9));
        var capture=NativeMapReadProjection.Capture(run);Assert.True(capture.Status=="captured",capture.Gap);
        Assert.Equal(new MapCoord(9,9),capture.Current);Assert.Single(capture.Visited);
        Assert.Same(map.StartingMapPoint,capture.Nodes.Single(node=>node.State=="travelable").Point);
    }
    [Fact]
    public void AmbiguousCoordinatesAndForeignChildrenRefuseInsteadOfDroppingNodes()
    {
        var(run,map,_)=Fixture();map.GetPoint(1,0)!.coord=map.GetPoint(0,0)!.coord;
        Assert.Equal("unknown",NativeMapReadProjection.Capture(run).Status);
        var(other,clean,_discard)=Fixture();clean.StartingMapPoint.AddChildPoint(new MapPoint(7,7));
        Assert.Equal("unknown",NativeMapReadProjection.Capture(other).Status);
    }
    [Fact]
    public void DetachedValuesDoNotReadLaterPointMutationAndDestinationsOverrideVisitedState()
    {
        var(run,map,visited)=Fixture();visited.Add(map.StartingMapPoint.coord);visited.Add(map.GetPoint(0,0)!.coord);
        map.GetPoint(0,0)!.AddChildPoint(map.StartingMapPoint);
        var capture=NativeMapReadProjection.Capture(run);Assert.True(capture.Status=="captured",capture.Gap);
        Assert.Equal("travelable",capture.Nodes.Single(node=>ReferenceEquals(node.Point,map.StartingMapPoint)).State);
        var node=capture.Nodes.Single(node=>ReferenceEquals(node.Point,map.GetPoint(0,0)));
        map.GetPoint(0,0)!.PointType=MapPointType.Boss;
        Assert.Equal("monster",node.PointType);Assert.Equal(2,node.Children.Count);
    }

}
