using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.GameActions.Multiplayer;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Combat;
using STS2Platform.NativeFoundation;

namespace STS2Connector.Host.Tests;

[Collection("Native source carrier observer isolation")]
public sealed class NativeSourceCarrierObserverTests
{
    private static T Bare<T>() => (T)RuntimeHelpers.GetUninitializedObject(typeof(T));
    private static NativeSourceInputPrefix Prefix() => new("end_turn",new(),new(),new Dictionary<string,object>(),
        "typed exact fixture","EndPlayerTurnAction",typeof(EndPlayerTurnAction));
    [Fact]
    public void FirstExactBindingAndAcceptedCarrierAreSynchronousOrderedAndUseOriginalToken()
    {
        object token=new();var action=Bare<EndPlayerTurnAction>();var events=new List<string>();
        using var registration=NativeSourceInputProvider.Register(_=>token, (actual,delivery,reason)=>
        {Assert.Same(token,actual);Assert.Null(reason);events.Add(delivery);},(actual,carrier,kind)=>
        {Assert.Same(token,actual);Assert.Same(action,carrier);events.Add(kind);});
        var invocation=NativeSourceInputProvider.Begin(Prefix());
        try
        {
            NativeSourceInputProvider.BindSubmitted(action);NativeSourceInputProvider.BindSubmitted(action);
            Assert.Equal(new[]{"game_action_submitted"},events);
            NativeSourceInputProvider.Finish(invocation);
            Assert.True(NativeSourceInputProvider.ObserveAccepted(action));
            Assert.False(NativeSourceInputProvider.ObserveAccepted(action));
            Assert.Equal(new[]{"game_action_submitted","game_action_accepted","delivered"},events);
        }
        finally{NativeSourceInputProvider.Finish(invocation);}
    }
    [Fact]
    public void OldDisposedRegistrationNeverForwardsLateCarrierOrTerminalToNewSink()
    {
        object oldToken=new(),newToken=new();var action=Bare<EndPlayerTurnAction>();int old=0,newer=0;
        var registration=NativeSourceInputProvider.Register(_=>oldToken,(_,_,_)=>old++,(_,_,_)=>old++);
        var invocation=NativeSourceInputProvider.Begin(Prefix());NativeSourceInputProvider.BindSubmitted(action);
        NativeSourceInputProvider.Finish(invocation);registration.Dispose();Assert.Equal(1,old);
        using var replacement=NativeSourceInputProvider.Register(_=>newToken,(_,_,_)=>newer++,(_,_,_)=>newer++);
        Assert.True(NativeSourceInputProvider.ObserveAccepted(action));Assert.Equal(1,old);Assert.Equal(0,newer);
    }
    [Fact]
    public void ObserverFailureKeepsActualDeliveredDispositionAndExplicitAccountingGap()
    {
        object token=new();var action=Bare<EndPlayerTurnAction>();var terminals=new List<(string,string?)>();int attempts=0;
        using var registration=NativeSourceInputProvider.Register(_=>token,(_,delivery,reason)=>terminals.Add((delivery,reason)),
            (_,_,_)=>{attempts++;throw new InvalidOperationException("isolated observer failure");});
        var invocation=NativeSourceInputProvider.Begin(Prefix());
        try
        {
            NativeSourceInputProvider.BindSubmitted(action);NativeSourceInputProvider.Finish(invocation);
            Assert.Equal(NativeSourceInputProvider.CarrierObserverFailedReason,invocation!.CarrierObservationError);
            Assert.True(NativeSourceInputProvider.ObserveAccepted(action));
            Assert.Equal(2,attempts);Assert.Equal(("delivered",NativeSourceInputProvider.CarrierObserverFailedReason),Assert.Single(terminals));
        }
        finally{NativeSourceInputProvider.Finish(invocation);}
    }
    [Fact]
    public void ConflictingInvocationCannotStealOrDuplicateTheOriginalCarrier()
    {
        object firstToken=new(),secondToken=new();object next=firstToken;var action=Bare<EndPlayerTurnAction>();
        var observed=new List<object>();var terminal=new List<(object,string)>();
        using var registration=NativeSourceInputProvider.Register(_=>next,(token,delivery,_)=>terminal.Add((token,delivery)),
            (token,_,_)=>observed.Add(token));
        var first=NativeSourceInputProvider.Begin(Prefix());NativeSourceInputProvider.BindSubmitted(action);NativeSourceInputProvider.Finish(first);
        next=secondToken;var second=NativeSourceInputProvider.Begin(Prefix());
        try
        {
            NativeSourceInputProvider.BindSubmitted(action);Assert.Equal((secondToken,"unknown"),Assert.Single(terminal));
            Assert.True(NativeSourceInputProvider.ObserveAccepted(action));
            Assert.Equal(new[]{firstToken,firstToken},observed);Assert.Equal((firstToken,"delivered"),terminal[1]);
        }
        finally{NativeSourceInputProvider.Finish(second);}
    }
    [Fact]
    public void FailedCreatedCardProofPublishesNoCarrierOrAcceptance()
    {
        int observed=0;var terminal=new List<string>();
        using var registration=NativeSourceInputProvider.Register(_=>new(),(_,delivery,_)=>terminal.Add(delivery),(_,_,_)=>observed++);
        var invocation=NativeSourceInputProvider.Begin(new("start_card_play",new(),new(),new Dictionary<string,object>(),
            "native hand fixture","NPlayerHand.StartCardPlay"));
        try
        {
            NativeSourceInputProvider.BindCreatedCardPlay(Bare<NMouseCardPlay>());
            NativeSourceInputProvider.ObserveCreatedCardPlay(invocation,Bare<NPlayerHand>(),Bare<NHandCardHolder>());
            Assert.Equal(0,observed);Assert.Empty(terminal);
        }
        finally{NativeSourceInputProvider.Finish(invocation);}
        Assert.Equal(new[]{"unknown"},terminal);
    }
}
[CollectionDefinition("Native source carrier observer isolation",DisableParallelization=true)]
public sealed class NativeSourceCarrierObserverIsolation { }
