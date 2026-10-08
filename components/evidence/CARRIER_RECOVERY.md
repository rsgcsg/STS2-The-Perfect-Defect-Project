# Explicit Human bundle carrier recovery

`recover_human_bundle_carrier(source_directory, destination_store, expected=None)`
in `sts2_platform_evidence.carrier_recovery` creates a new exact-inventory carrier
for an existing Human bundle. This is an explicit local operation. Opening,
listing, verifying, importing or downloading a bundle never invokes it implicitly.

The first source form is a directory. Archive extraction is not part of this API.
It supports only the explicit Human bundle schemas already handled by the
unchanged versioned verifier. Source-session and Agent bundles are not converted
to Human bundles. The function has no training, use, Gold or native-game operation.

The original `checksums.sha256` and every file it names are copied byte-for-byte.
The original bundle manifest, embedded profile, IDs and Human attestation are
never regenerated. Missing/corrupt listed files, unsafe paths, links, portable
path aliases, duplicate checksum paths and unknown extra files fail closed. The
only extra-file metadata name admitted by policy `macos-finder-ds-store-1` is
`.DS_Store`; a listed file with that name remains evidence and is not excluded.
Every excluded file's relative path, byte count and SHA-256 are retained.

Before copying, a complete inventory of the original carrier is hashed using the
existing DirectoryTransferManifest codec. After copying, the entire source is
rechecked; changes reject recovery. Fixed bounds are 50,000 entries, 512 MiB total
file bytes and 8 MiB each for checksum text and the bundle manifest. The destination must be outside the source and
must not be a link. Only new destination/staging/receipt files are written.

The staged directory must pass the existing versioned Human bundle verifier.
Its verified content ID must equal the original manifest's ID. The existing
DirectoryTransferManifest and DirectoryReceiver then recheck all bytes and the
typed bundle before atomic promotion. Existing destination content is never
replaced. Promotion failures remain failures; no verifier is weakened.

The external immutable receipt uses `sts2.evidence/carrier-recovery-1`. It records
the original complete transfer inventory hash, original bundle-manifest and
checksum hashes, every excluded metadata file, the verified bundle schema/content
ID, the recovered carrier's transfer identity and explicit non-claims. Receipt
files live below the destination's `recovery-receipts/`, outside the verified
`objects/<bundle_content_id>/` directory. Receipt identity is SHA-256 of its
canonical bytes; repeated identical recovery can reuse both carrier and receipt.
A receipt storage failure does not delete or rewrite a promoted carrier and is
reported as incomplete recovery.

Bundle content identity is the producer's manifest/content identity, not the
containing directory or archive's entire byte stream. Removing only unlisted
metadata from a new carrier can preserve that bundle identity while changing the
carrier/transfer hash. An original owner attestation remains an attestation with
`machine_verifiable=false`; it is not a cryptographic signature or new Human
origin proof. New carrier aliases must retain original occurrence/source links
and existing use/Gold protections. Recovery adds no observation, native terminal,
causal proof, complete history, research admission or extra training example.

Normal examples are a valid archival bundle with an unlisted `.DS_Store`, a
valid clean bundle, and idempotent promotion. Negative examples include corrupt
listed bytes, missing files, unknown extras, symlink roots/files/directories,
case/Unicode or hard-link aliases, duplicate/traversing checksum paths, source
mutation during copy, failed Human attestation, destination collisions and
receipt-write failure. Portable tests use synthetic production-shaped bundles;
they do not qualify real Human data.
