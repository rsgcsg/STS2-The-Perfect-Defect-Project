"""Completed projection must not leave its temporary graph in a recursive closure."""
import gc
import weakref

import pytest
from test_p1_input_v2 import public_state

from stpd.p1 import input as projection


@pytest.mark.parametrize("spec", [projection.INPUT_SPEC, projection.SORTED_INPUT_SPEC,
                                 projection.V2_INPUT_SPEC])
@pytest.mark.parametrize("reject", [False, True])
def test_projection_releases_entities_without_cycle_collection(monkeypatch, spec, reject):
    references = []
    initialize = projection.Entity.__init__

    def tracked(entity, *args, **kwargs):
        initialize(entity, *args, **kwargs)
        references.append(weakref.ref(entity))

    monkeypatch.setattr(projection.Entity, "__init__", tracked)
    state = public_state()
    if reject:
        state["zz_unsupported"] = object()

    def project_and_release():
        if reject:
            try:
                projection.project(state, input_projection=spec)
            except ValueError as error:
                assert str(error) == "unsupported public field"
            else:
                pytest.fail("unsupported public field was accepted")
        else:
            frame = projection.project(state, input_projection=spec)
            assert frame.entities and frame.choices == state["catalog"]
        # Return releases both the result and any ordinary exception traceback.

    enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        project_and_release()
        assert references
        assert all(reference() is None for reference in references)
    finally:
        if enabled:
            gc.enable()
        gc.collect()
