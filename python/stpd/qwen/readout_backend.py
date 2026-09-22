"""Input-gradient access to the pinned portable Qwen; not a second Qwen load.

The pre-existing feature backend remains inference-only. This interface explicitly
preserves gradients to soft queries while leaving all pretrained parameters frozen.
"""

from __future__ import annotations

from typing import Any

import torch
from torch import Tensor

from ..models.packed_actions import pack_actions
from ..models.token_core import TokenCore
from .portable_backend import PortableQwenBackend, validate_engineering_identity


class FrozenQwenTokenCore(TokenCore):
    supports_bidirectional = False

    def __init__(self, backend: PortableQwenBackend, *, max_tokens: int | None = None) -> None:
        super().__init__()
        validate_engineering_identity(backend.identity)
        self.identity = backend.identity
        self.model = backend._base_model
        self.model.eval().requires_grad_(False)
        self.width = backend.hidden_size
        self.vocab_size = int(self.model.config.vocab_size)
        self.max_tokens = backend.pin.l1.hard_limit if max_tokens is None else max_tokens
        if (type(self.max_tokens) is not int or self.max_tokens <= 0
                or self.max_tokens > int(self.model.config.max_position_embeddings)):
            raise ValueError("token budget must fit the pinned backbone context")
        self.frozen = True
        self.eos_token_id = int(backend._tokenizer.eos_token_id)

    def embed_tokens(self, ids: Tensor) -> Tensor:
        self.validate_tokens(ids)
        return self.model.get_input_embeddings()(ids)  # type: ignore[no-any-return]

    def readout_initial(self) -> Tensor:
        embedding = self.model.get_input_embeddings()
        return embedding.weight[self.eos_token_id].detach().clone()  # type: ignore[no-any-return]

    def contextualize_packed(
        self, embeddings: Tensor, positions: Tensor, blocked: Tensor,
    ) -> Tensor:
        mask = torch.zeros_like(blocked, dtype=embeddings.dtype).masked_fill(
            blocked, torch.finfo(embeddings.dtype).min,
        )
        result = self.model(inputs_embeds=embeddings[None], attention_mask=mask[None, None],
                            position_ids=positions[None], use_cache=False, return_dict=True)
        return result.last_hidden_state[0]  # type: ignore[no-any-return]

    def read_action_queries(
        self, state: Tensor, actions: tuple[Tensor, ...], query: Tensor,
    ) -> Tensor:
        """All fixed tokens once, then all readouts together, using the same Qwen.

        Readouts have no outgoing edges to other positions, so removing them from the
        fixed pass and restoring their incoming edges is an exact eval-mode decomposition.
        Cache is shared within this decision only; no loop of Qwen calls over candidates.
        """
        if self.model.training or any(p.requires_grad for p in self.model.parameters()):
            raise ValueError("packed prefix execution requires the frozen eval backbone")
        packed = pack_actions(self.embed_tokens(state),
                              tuple(self.embed_tokens(a) for a in actions), query)
        fixed, readouts = packed.prefix, packed.readouts
        mask = torch.zeros_like(packed.blocked, dtype=query.dtype).masked_fill(
            packed.blocked, torch.finfo(query.dtype).min,
        )
        with torch.no_grad():
            prefix = self.model(
                inputs_embeds=packed.embeddings[fixed][None],
                attention_mask=mask[fixed][:, fixed][None, None],
                position_ids=packed.positions[fixed][None], use_cache=True, return_dict=True,
            )
        keys = torch.cat((fixed, readouts))
        result = self.model(
            inputs_embeds=packed.embeddings[readouts][None],
            attention_mask=mask[readouts][:, keys][None, None],
            position_ids=packed.positions[readouts][None],
            past_key_values=prefix.past_key_values, use_cache=True, return_dict=True,
        )
        return result.last_hidden_state[0]  # type: ignore[no-any-return]

    def read_last_query(self, ids: Tensor, query: Tensor) -> Tensor:
        """Exact causal decomposition: fixed prefix KV, differentiable final query.

        Only prefix tokens and backbone weights are fixed. No earlier causal position
        can depend on the appended query, so their KV can be computed without autograd.
        Cache lifetime is one branch; it never crosses candidates or query updates.
        Use no_grad, not inference_mode: query backward needs the constant KV tensors.
        """
        self.validate_tokens(ids)
        length = ids.numel()
        if length + 1 > self.max_tokens or query.shape != (self.width,):
            raise ValueError("invalid query shape or joint input token limit")
        if self.model.training or any(p.requires_grad for p in self.model.parameters()):
            raise ValueError("prefix decomposition requires the frozen eval backbone")
        with torch.no_grad():
            prefix = self.model(
                input_ids=ids[None, :],
                attention_mask=torch.ones(1, length, device=ids.device, dtype=torch.long),
                position_ids=torch.arange(length, device=ids.device)[None, :],
                use_cache=True, return_dict=True,
            )
        result = self.model(
            inputs_embeds=query.reshape(1, 1, self.width),
            attention_mask=torch.ones(1, length + 1, device=ids.device, dtype=torch.long),
            position_ids=torch.tensor([[length]], device=ids.device, dtype=torch.long),
            past_key_values=prefix.past_key_values,
            use_cache=True, return_dict=True,
        )
        return result.last_hidden_state[0, 0]  # type: ignore[no-any-return]

    def contextualize(self, embeddings: Tensor, *, causal: bool) -> Tensor:
        if not causal:
            raise ValueError("pinned Qwen requires causal attention")
        if (
            embeddings.ndim != 2 or embeddings.shape[1] != self.width
            or not 0 < embeddings.shape[0] <= self.max_tokens
        ):
            raise ValueError("invalid embedding shape or input token limit")
        length = embeddings.shape[0]
        # Explicit positions reset for every isolated candidate branch.
        result: Any = self.model(
            inputs_embeds=embeddings.unsqueeze(0),
            attention_mask=torch.ones(1, length, device=embeddings.device, dtype=torch.long),
            position_ids=torch.arange(length, device=embeddings.device)[None, :],
            use_cache=False, return_dict=True,
        )
        return result.last_hidden_state[0]  # type: ignore[no-any-return]
