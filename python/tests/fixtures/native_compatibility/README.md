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
