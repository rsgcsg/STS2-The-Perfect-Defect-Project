"""Named new-Run policies over the existing, verified packed model kernels.

Freeze to_dict() through prepare_p1_run(execution_policy=...) or the learner's
model/checkpoint identity. Never replace a running Run's execution policy.
"""
from __future__ import annotations

from spireagent.json_boundary import BoundaryError

from .execution import MStdExecution


def training_execution(name: str, *, microbatch_size: int | None = None) -> MStdExecution:
    if name != "mstd-training-v2":
        if microbatch_size is not None:
            raise BoundaryError(
                "p1_execution", "explicit_new_plan_required_for_microbatch_override")
        return MStdExecution.for_plan(name)
    size = 8 if microbatch_size is None else microbatch_size
    if type(size) is not int or size not in {1, 2, 4, 8}:
        raise BoundaryError("p1_execution", "unsupported_wide_microbatch")
    # v2 is an execution plan, not another model implementation. Existing kernels
    # already support batched scenes; their old area budget usually forced size1.
    return MStdExecution(implementation="experimental-packed-v1", microbatch_size=size,
                         scene_attention_area=2**26, text_attention_area=2**26,
                         cpu_threads=2, defer_score_sync=True)
