# Exact historical source proof fixture

`evidence_rc27_basis.json` contains the canonical 39-file SHA256 inventory and
complete UTF-8 `python/uv.lock` read from Git commit
`9c1c14f447f7f6dd03c50f78ed8380da74e6a82f` in this repository. The original
source digest is `516782ca56d9a1c8ad218804fd111731507175bd93855f0896a6e608387c25b2`;
the full lock digest is
`46eb838668b984bc4f0cab203a7b3c759115cf05fe2107b21a1730dd9808feea`.

It contains repository metadata and no model weights, Human data or runtime
evidence. Portable tests consume it at the Git-read boundary because a shallow
CI checkout may omit that historical commit. The production exporter requires
the exact Git ref and fails closed if unavailable; the loader independently
validates the fixed complete proof. This fixture proves neither original
training nor actual model numerical parity.

The operational rc30 target lock is the exact lock at repository commit
`6e6d08ed81bcdb1f284a421752414d3e085d06f8`. The fixture reconstructs its bytes
from the existing rc27 original lock by replacing only the Evidence version and
revision. Its SHA256 must remain
`25e801bb0e0eb0b0fc6098ec77b1c3664fa2cdde349e5d13e3462ea3c05d8960`.
This avoids copying the full lock twice and keeps historical compatibility tests
independent of today's dependency upgrades. Production still rejects current
unapproved inference or lock changes; this is test source-read data only.
