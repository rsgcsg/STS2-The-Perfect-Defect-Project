"""Fixed experimental Workbench M2 recipe; admission remains in LocalTrainingService."""

from __future__ import annotations

import torch
from tokenizers import Tokenizer

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError
from spireagent.storage.store import ArtifactStore
from spireagent.workbench.memory_recipe import (
    M2_K1_RECIPE,
    input_profile_for_recipe,
    reset_each_step_for_recipe,
)
from stpd.fullrun.memory_training_prepare import fit_observed_memory_tokenizer
from stpd.fullrun.observed_input_sequence import load_observed_input_view
from stpd.workers.memory_ranking import MemoryConfig
from stpd.workers.memory_run import prepare_observed_memory_run


def prepare_workbench_memory(store: ArtifactStore, source_id: str,
                             producer: Producer, operation_id: str,
                             recipe: str = M2_K1_RECIPE) -> tuple[str, str]:
    """Run only in the admitted operation's private CLI child process."""
    torch.set_num_threads(2)
    view = load_observed_input_view(store, source_id)
    try:
        profile = input_profile_for_recipe(recipe)
        reset_each_step = reset_each_step_for_recipe(recipe)
    except ValueError as error:
        raise BoundaryError("local_training", "unsupported_training_recipe") from error
    settling = 0 if profile == "text-menu-v2" else 64
    tokenizer_bytes, episode_count = fit_observed_memory_tokenizer(
        view, max_settling_events=settling, input_profile=profile)
    if episode_count > 8:
        raise BoundaryError("local_training", "memory_episode_limit")
    tokenizer = Tokenizer.from_str(tokenizer_bytes.decode("utf-8"))
    config = MemoryConfig(
        vocab_size=tokenizer.get_vocab_size(), episode_count=episode_count,
        slots=1, reset_each_step=reset_each_step,
        width=48, layers=1, heads=2, feedforward=96,
        dropout=0.0, max_tokens=16384, cpu_threads=2,
        max_total_input_tokens=4194304, max_episode_observations=768,
        max_episode_input_tokens=4194304, max_chunk_steps=2,
        max_chunk_input_tokens=24576, max_actions_per_step=256,
    )
    run = prepare_observed_memory_run(
        store, source_id, config, producer, tokenizer_bytes,
        max_settling_events=settling, operation_id=operation_id, reject_diagnostics=True,
        input_profile=profile,
    )
    return run.artifact_id, run.parent("training_input")
