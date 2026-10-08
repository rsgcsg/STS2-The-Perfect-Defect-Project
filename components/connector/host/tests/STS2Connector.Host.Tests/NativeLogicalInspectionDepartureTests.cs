using STS2Platform.GameMod;
using Xunit;

namespace STS2Connector;

public sealed class NativeLogicalInspectionDepartureTests
{
    [Theory]
    [InlineData(false, true, true)]
    [InlineData(true, false, true)]
    [InlineData(true, true, false)]
    public void StaleUnmountedAndAlreadyHiddenCallbacksCannotIssueWitness(bool live, bool mounted, bool visible)
    {
        object inspect = new(), context = new();
        Assert.Null(ConnectorNativeLogicalInspectionDeparture.Begin(inspect, inspect, inspect, context, live, mounted, visible));
        Assert.Null(ConnectorNativeLogicalInspectionDeparture.Begin(inspect, new object(), inspect, context, true, true, true));
        Assert.Null(ConnectorNativeLogicalInspectionDeparture.Begin(inspect, inspect, new object(), context, true, true, true));
    }
    [Fact]
    public void MissingUpdateWrongContextReopenedReplacedDeletedAndNativeFailureCannotFakeReturn()
    {
        object inspect = new(), context = new();
        using var witness = ConnectorNativeLogicalInspectionDeparture.Begin(inspect, inspect, inspect, context, true, true, true);
        Assert.Equal(InspectionDepartureDisposition.Missing, witness!.Returned(inspect, inspect, true, false, false));
        ConnectorNativeLogicalInspectionDeparture.ContextReturned(new object(), true);
        Assert.Equal(InspectionDepartureDisposition.Missing, witness.Returned(inspect, inspect, true, false, false));
        ConnectorNativeLogicalInspectionDeparture.ContextReturned(context, false);
        Assert.Equal(InspectionDepartureDisposition.Missing, witness.Returned(inspect, inspect, true, true, false));
        Assert.Equal(InspectionDepartureDisposition.Missing, witness.Returned(inspect, new object(), true, false, false));
        Assert.Equal(InspectionDepartureDisposition.Missing, witness.Returned(new object(), inspect, true, false, false));
        Assert.Equal(InspectionDepartureDisposition.Missing, witness.Returned(inspect, inspect, false, false, false));
        Assert.Equal(InspectionDepartureDisposition.Missing, witness.Returned(inspect, inspect, true, false, true));
    }
    [Fact]
    public void DirectNativeUpdateReturnOverwritesEarlierNestedReturnAndScopesExpireWithCallback()
    {
        object inspect = new(), context = new();
        using var outer = ConnectorNativeLogicalInspectionDeparture.Begin(inspect, inspect, inspect, context, true, true, true);
        // An earlier Update inside a visibility listener did publish. The actual
        // native body's final Update did not; its own returned result is decisive.
        ConnectorNativeLogicalInspectionDeparture.ContextReturned(context, true);
        using (var inner = ConnectorNativeLogicalInspectionDeparture.Begin(inspect, inspect, inspect, context, true, true, true))
        {
            ConnectorNativeLogicalInspectionDeparture.ContextReturned(context, true);
            Assert.Equal(InspectionDepartureDisposition.ExistingContextPublication, inner!.Returned(inspect, inspect, true, false, false));
        }
        ConnectorNativeLogicalInspectionDeparture.ContextReturned(context, false);
        Assert.Equal(InspectionDepartureDisposition.Publish, outer!.Returned(inspect, inspect, true, false, false));
        outer.Dispose();
        ConnectorNativeLogicalInspectionDeparture.ContextReturned(context, true);
        Assert.Equal(InspectionDepartureDisposition.Missing, outer.Returned(inspect, inspect, true, false, false));
    }
}
