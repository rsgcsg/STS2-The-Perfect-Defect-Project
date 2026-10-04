# Public M2 Modal result transport investigation — 2026-10-04

## Status and scope

The first real public-M2 pilot attempt failed while the Modal SDK uploaded its
return value. The App was stopped and independently observed with zero tasks and
containers. No result or checkpoint was accepted locally; the dependent second
attempt was not submitted. This record does not qualify training, recovery,
model quality, or the full six-arm experiment.

The frozen training source is `732a2d67bbd3718da62f87cd51eb3842abc23757`.
The deployment adapter is a separate private controller, not part of that frozen
model implementation. Its failed version explicitly set `block_network=True`.

## Observed failure

At 2026-10-04 06:14:10 UTC the provider log follows the normal success-output path:

`run_input_sync -> push_outputs -> output_items -> package_output ->
format_blob_data -> blob_upload -> signed object-storage PUT`

The PUT failed with `ClientConnectorDNSError` caused by
`socket.gaierror: Temporary failure in name resolution` for a Cloudflare R2
storage host. There is no evidence in this failure trace of an out-of-memory
error or a training timeout. The success-output path strongly suggests the
wrapper returned after its subprocess exit/size checks, but the unavailable
bytes cannot establish runtime or checkpoint validity.

The independent terminal record observed the App stopped at 06:14:14 UTC,
no running containers, a failed call, and zero unfinished inputs. Provisional
raw usage for the failed attempt was USD 0.03464763. A stopped App and a provisional
bill do not release outstanding budget reservations automatically.

Private evidence digests (no raw input, weights, credentials or signed URLs are
included in this repository):

- Provider log: `100d908c75b821d837b7932c2b21980d9c6e18120c3f377762c6099364a0d8a2`.
- Independent terminal/billing record: `f608b976c8cfd2ba4d1f4c5f5240f9626c3c107b7772f2e618941c8ad2e65800`.

## Why the prior M0 run differed

The completed M0 operation used frozen source
`c8d654b7aa87ad71da7c5f12e0a20815f2c16889` and the actual
`deploy/cloud-worker/m0_update_modal.py` entry. Its Function declaration did not
pass `block_network` or `restrict_modal_access`; both default to `False` in the
locked Modal SDK 1.5.5. This differs from the private M2 adapter's explicit network
block. The entry file matches its frozen commit bytes, with SHA-256
`6bd162c36b596169e34f842e6c653182c1e5a139595c0a2d39726a381d662151`.

Three historical accepted call records establish that M0 returned large results:

| Call | Request wire bytes | Result bytes | Accepted checkpoint step |
| --- | ---: | ---: | ---: |
| 001 | 41,586,613 | 129,250,254 | 3,000 |
| 002 | 126,802,905 | 129,250,378 | 6,000 |
| 026 | 127,666,227 | 129,250,380 | 76,450 |

These records bind the operation, frozen producer, image, call, runtime evidence
and terminal confirmation. The historical comparison's private metadata digest
is `87c0ba1cdfbec79f8d1ea48f8b3cd0f4e4f091e9994ff24c27b487f02032d7d3`.

Both lanes use `Function.spawn(bytes)` and retrieve the saved call's bytes through
`FunctionCall.get`. SDK 1.5.5 defaults to a 2 MiB generic inline-object limit and
an 8 KiB async limit; for this async output path the effective default is their
minimum, 8 KiB. The SDK may supply negotiated limits. The historical results
exceed both defaults and therefore strongly imply SDK-managed blob transport;
there was no separate application-owned S3 output channel. The local store's
8 MiB chunks are post-download persistence, not remote upload chunks.

The network flags above are reconstructed from the frozen deployment entry and
locked SDK defaults, not a saved historical service-side definition. A later
read-only provider inspection confirmed the exact App/function layouts, but
FunctionGet returned NotFound for both stopped Apps. It did not recover network
flags or signed blob-transfer records. Do not promote this reconstruction to a
historical provider snapshot or continue probing retired resources.

## Repair and verification boundary

The supported Function API in SDK 1.5.5 has no public domain/CIDR allowlist
parameter. Sandbox allowlists are a different interface. Disabling
`block_network` removes this Function policy's destination restriction; it does
not enforce an object-storage-only allowlist. Blob destinations and fallback
signed URLs are chosen dynamically by Modal.

The user authorized outbound access for one CPU-only synthetic transport probe
and, conditional on its acceptance, at most two replacement L4 pilot attempts.
This is scoped authorization, not a permanent default or a change to the local
computer's network. The replacement controller must:

1. Bind an explicit network-policy receipt and controller/module digests to the
   independently approved reservation and each new attempt's deployment intent.
2. Preserve the original model source/image and all existing identity/resource
   checks. Before each submission, additionally verify the deployed network
   flag, proxy and explicit mounts, including when recovering a saved target.
3. Inject no user credential secrets or data volumes. Account for SDK 1.5.5's
   encoding of declared nonsecret environment variables as one anonymous Secret;
   its ID count alone does not prove the values. Image mount layers are not
   extra data volumes.
4. Use one new attempt/App per call, one writer, zero application retries, an
   absolute outer deadline and reserved cleanup time. A single stop delivery is
   followed by read-only terminal checks. Unknown delivery never permits another
   submission under the same attempt.
5. First return exactly 8 MiB of deterministic synthetic bytes from one bounded
   CPU Function and validate length/hash through saved-call retrieval. No training
   inputs, weights, GPU or owner-loader pass are needed for this check.

The initial small GPU recovery probe used a Sandbox stdout path. It did not test
large Function return transport, so its success could not qualify this boundary.
Small generator chunks could use inline SDK transport, but SDK 1.5.5 rejects
`spawn` on generators. Switching to synchronous `remote_gen` would change the
current durable call/poll/recovery contract and is not this repair.

The CPU probe ran at 07:04 UTC and returned exactly 8,388,608 bytes with SHA-256
`7d212b9c884f5c77896de960ae17cc341cda43b14d6a971f34ca29ebd4badf7f`.
The accepted local receipt is
`b0bae311e9e9044900722ce12ee74656d1a709e261b568fb72be0b0c5d9cbe91`.
A separate read-only check confirmed the App stopped at 07:04:18 UTC, zero
containers, a successful call, and zero unfinished inputs. Its evidence digest is
`c040aa33526a2a6d3362ab5dd0fd7fb268835296c4ad5bcb1cfc8c964fe5e839`;
provisional raw usage was USD 0.00003071. This accepts the synthetic CPU transport
check only. An 8 MiB CPU result establishes that this transport configuration
works at that size; it cannot prove public egress is universally necessary,
qualify the larger real result envelope, or replace actual L4 checkpoint and
resume acceptance.

## First real replacement result

The reviewed replacement retained the exact frozen source, image, input, model,
seed and runtime. A new attempt-group journal references the preserved failed
journal, its typed saved call, stop receipt and independent terminal evidence.
It neither marks the old attempt accepted nor replays its unknown return.

The first replacement submitted at 07:33:19 UTC. It returned 108,079,559 bytes,
SHA-256 `44e9fc58737fea8f834ef977083cea5eda54ecb785309c7c535a1111c0d6ec0a`.
The App was stopped before local acceptance. At 07:36:23 UTC the checkpoint
passed acceptance after 16 supervised decisions and five optimizer updates,
at a chain boundary. The private accepted-marker digest is
`a9c56e028cf0dea1b707ea40a5a32f18432d63122f4f234fec91e207c972e9e0`.

The second attempt restored that checkpoint, processed the remaining 347
windows, and returned 144,429,053 bytes with SHA-256
`c7e1ad3825da1b6c1ba63b12efbf294d9e88fc79868e0527012ddee290e5bda1`.
At 07:42:58 UTC the controller accepted epoch one, its dev evaluation, model
export and CPU weight load, then exited normally. This completed 998 supervised
decision exposures and 352 optimizer updates. Both Apps had stop confirmations
before acceptance. Independent checks subsequently confirmed both Apps stopped,
zero active containers in the environment, and successful calls with zero
unfinished inputs. The second independent record digest is
`aa72867ae359d3fe13522c0467ab82071bb7187f299bb2685ae3cb44ff645313`. The final
execution receipt digest is `93d4ff6de55e50ae358ae47be7ea2899e0e75f9dd2ba8f1302e3185c0dbdd7b0`.

The 13-label engineering dev set yielded 4 correct choices and mean loss
1.8546198423092182. This tiny selected set does not establish generalization or
memory benefit. The run deliberately remains paused after epoch one: no
five-epoch completion, six-arm experiment, real-game deployment, or equivalence
to an uninterrupted real-data GPU run is claimed. The earlier synthetic GPU
recovery parity test has its own narrower evidence.

The controller took about 15 minutes 52 seconds including local owner admission,
transport, stop and acceptance. Independent provisional App-level metering is
USD 0.03355110 plus USD 0.05964622 for the two replacements, USD 0.09319732 total.
Known metered usage across the seven linked batch Apps is USD 0.14357585; final
invoice confirmation remains pending. The controller's zero known-actual subtotal
denotes missing attached billing,
not zero cost. The approved conservative exposure remains USD 1.70, comprising
USD 1.00 in prior holds, USD 0.60 for the two replacement attempts and USD 0.10
for metering/stop tail, all within the same USD 20 batch. Holds are not released
by a successful checkpoint or delayed meter.

## References

- [Modal restricted Functions](https://modal.com/docs/guide/restricted-access)
- [Modal invocation methods and durability](https://modal.com/docs/guide/function-invocation-methods)
- [Sandbox networking controls](https://modal.com/docs/guide/sandbox-networking)
- [Modal resource pricing](https://modal.com/pricing)
