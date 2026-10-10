# Runtime-backed program demonstration collection

`python -m spireagent.workbench project collect-source3` is one bounded application
attempt. It composes the existing public Agent Runtime with the explicit pure
`stpd-native-public-program-teacher` Agent. Runtime owns Current/read/catalog,
consumption/ACK, Act/Await, controller, original Results, budgets and Stop. The
application owns isolated Host bootstrap and the genuine Workbench admission
fence; it does not implement another native action loop.

The existing project configuration, Node dependencies, built Connector SDK and
Policy Runtime facades, private `defect-a0-s0` template and exact authorized game
installation must already be available. This command installs none of them.

```sh
python -m spireagent.workbench project collect-source3 \
  --config /ABS/project.json --game-directory /ABS/authorized-game \
  --host-local-root /ABS/private-host-state --output /ABS/new-private-attempt \
  --seed EXPLICITSEED --target-choices 100 --max-submissions 100 \
  --deadline-ms 900000 --plan-only
```

`--plan-only` validates read-only metadata without Application, child, SDK or game
launch. Remove it only for the explicitly authorized attempt. Experimental Host
candidates retain their separate qualification and acknowledgement flags.
Paths must be absolute, contain no symlink, and output must be new/private.
The pilot admits at most 100 submissions and 1200 policy calls within 15 minutes.
All module/executable choices are fixed by the trusted application; manifests and
teacher replies never select an arbitrary module, shell or native operand.

An `unknown` or pending predecessor blocks the default command. A distinct fresh
episode after unresolved **input consumption** always requires three explicit options:
`--predecessor-report-path`, `--predecessor-report-sha256`,
`--predecessor-marker-sha256`. If the old attempt recorded Source3, also supply the
pair `--predecessor-source3-bundle` and `--predecessor-source3-content-id`; the pair
is forbidden when that old attempt explicitly opted out. The supplied bundle must already be packed by
the registered CollectionTool; preflight only reads and verifies it. Both owning
typed Agent/Source3 verifiers, exact report/full-final hashes, original Source
seals, known native deliveries and known Source/controller/child/Host cleanup
must agree for a Source-on predecessor. A new `--no-source3` request does not omit the previous Source-on
closure obligation. This route never resolves unknown native delivery or input,
resumes an old operation, restores its state, or admits old data for training.

A Source3-off predecessor consumes the same typed Agent/native-closure and owner
cleanup proof plus exact original request/options/Node/quiesced opt-out agreement.
Its report Start/Close remain null and child `source_closed=true` represents the
known `not_requested` lane, not a recording seal. A boundary for this lane carries
no Source session/content/seal fields, and no dummy bundle is accepted. New Source
ID comparisons apply only when both old recording and new recording exist.

The same Application instance lock rechecks the exact predecessor before
admission. The new private output retains the old marker's exact unknown bytes
and a sealed boundary receipt; files and directory entries are durably synced
before the existing active pointer advances to the new pending operation. A crash
or replay cannot use old authorization past that new pointer. Prior reports and
recordings are unchanged; the old unknown fact remains immutable history.

The fixed reviewed child delegates startup to the existing Host. That Host
chooses a fresh UUID profile name and `instantiateProfileTemplate` clears the
chosen target before copying the checked private template. The absent generation
file then makes `prepareIsolatedProfile` create a new generation. This owning
construction is the fresh profile/state basis; a transported `instantiated`
string alone is not proof. Older reports lack profile IDs, so no old profile ID is
fabricated. Existing Ready checks additionally require distinct runtime identity
and actual profile/generation fields, new Source session/segment/epoch, and a new
Agent run/continuity/stream with state0/no consumption/no used budget before any
Runtime permission. Fresh startup grants no prior execution/effect/Commit claim.

Source3 is an optional default-on overlay. `--no-source3` explicitly omits it while
the genuine Application/instance lock and direct Agent evidence remain. A running
Workbench or nonquiescent Model blocks collection. With Source3 enabled, original
`control_native_recording` Start/Close retain shared Source/Model admission.
Unknown Start admits no Runtime steps; unknown offered Close is never repeated.
Diagnostic request-write failure cannot suppress the genuine Close call. Normal
and exceptional paths stop Runtime, observe its exact Node quiescence, then Close
Source, then close the Host. A bounded fallback Close without confirmed quiescence
is explicitly reported; Close is never proof an in-flight native effect settled.

The Agent has a real code-closure artifact, its own AgentSpec/InputSpec and
`model_bindings=[]`, `scores=null`, `state_recovery=none`. It is unlearned, has no
weights or inference backend, and cannot be a hidden fallback in learned evaluation.
The code identity includes executed qualifiers/package initializers; lock and
interpreter provenance are explicit. Teacher policy version 1.0.4 preserves the
native joins and adds explicit bounded Await for a pending public owner/focus.
A Return delivery does not prove owner arrival. If Inspect still exposes only
Return while closing, the Agent Awaits; it never repeats that action to pad N.
A native map with explicit public `traveling=true` also Awaits even when its
complete catalog retains information actions. An exact public enabled map with an empty route list may also trigger the
Teacher waiting policy described below; missing or malformed facts remain fail-closed. Unsupported public owners close with an honest reason.

Each Next obtains complete fresh Current and complete ordered C through Runtime
and the public SDK. Attachment is scoped with empty eager event fields and the
fixed publication profile's required seams. Advisory events/gaps create neither
full-history claims nor backfill. The pure native qualifier and occurrence law
reject incomplete/incoherent input. Unchanged and empty nonterminal readiness
queries do not consume or advance state. A changed eligible input stages a copy
of disclosed script state; only the exact ConsumeACK commits it. Its next
Act/Await/Close retains that acknowledged watermark. A qualified ready summary
may be consumed without a new action label; clocks and later frames prove no
prior Commit, effect or closure.

`AgentRunEvidence` retains immutable original sample bytes/C, query/proposal/ACK
and later directive watermarks, original submission intent/result, and scoped
failure/Stop records. Unknown or pending original delivery is handed off by the
Runtime without an automatic Result lookup or resubmit. Some transport-unknown
request IDs are retained in canonical evidence but not exposed by public status;
the collection report declares that projection absence. It never reconstructs
control by parsing JSONL or fabricates a pending request.

A failed Current keeps the Runtime's original fail-closed behavior. The application
observes its first failed public SDK reply once and retains the full JSON values,
HTTP status and SDK-observed encoded byte count in an exclusive private file of
at most 64 KiB, within the aggregate diagnostic budget. Its SHA reference appears
inside the existing failure-detail file. The saved JSON is reserialized and is
neither original HTTP bytes nor a native capture or training input. Optional
diagnostic I/O does not delay the reply or owner cleanup; write failure does not
request another Current, retry delivery or suppress control/Source/Host closure.

Private application pipe/report v2 keeps Init, permissions and compact final
within 16 KiB. Explicitly named `runtime_gate`, `runtime_tick` and `quiesced`
envelopes preserve complete public Runtime status under the bounded 96 MiB
transport ceiling and one-message queue. Exact canceled application permission
reply IDs may be retired; Native Results remain exclusively Runtime-owned.
Large diagnostics stay in bounded private files. A compact final carries counts,
known disposition, actual teacher PID/code/signal, Source/control/Host cleanup and
SHA reference to the complete immutable private final. Hydration failure is a
reporting error and cannot erase received compact facts or turn an unverified
file into evidence. Runtime's public port normally SIGKILLs its owned adapter on
Stop; the actual signal is recorded, never relabeled exit 0.

`actual_choices` counts distinct received public original Result projections;
`submissions` comes from Runtime's actual submission-admission budget. Known
full delivery, native execution/rejection, effect and cancellation remain
separate. A delivered native rejection is a censored stop, even at the target.
`eligible_unique_N`/`N_exclusions` stay unknown and `admission=not_run`.
Source begins after Host bootstrap, so the prefix is partial. Collection neither
uploads nor trains and no count proves Human origin or scientific qualification.

Only Recorder-owned Source3 originals use the existing import/verification/
partition/reservation/training bridge. Its sampled training view remains an
explicit reexpression, not the teacher's actual consumption. Direct native
AgentRun-to-student admission/projection is a separate versioned owner; this
collector creates the trace but no converter or additional action ledger.

Focused source tests exercise real public SDK + Runtime + actual Python stdio,
genuine Application admission, exact ACK staging/readiness, native closing,
unknown/pending originals, optional overlay, cancellation and bounded reporting:

```sh
node --test tools/test/native-source3-collector.test.mjs \
  tools/test/native-source3-collector-sdk.test.mjs
python -m pytest -q python/tests/test_native_public_teacher.py \
  python/tests/test_native_teacher_agent.py python/tests/test_native_source3_collection.py
```

These tests use synthetic public fixtures and owned temporary files/processes.
They do not qualify an installed game, actual data, learning or complete G2/V1.

## Reward-page readiness (Teacher 1.0.7 / owned-stale 1.0.8)

The entered card-reward page can explicitly report `stage=settling` while the
whole observation is interactive because information or Skip actions exist.
For the exact public card-reward surface schema/kind, the Teacher now Awaits
that page's published settling state. A fresh acknowledged ready input enables
selection from its original catalog. Missing Select without that published
progress remains fail-closed. No clock, delivery, guessed effect or fabricated
native readiness is used. Agent versions 1.3.0/1.4.0 declare this timing change;
InputSpec and the learned Agent remain unchanged. Historical producer relations
remain frozen and do not qualify these new code bytes for training.

## Enabled map with no current route (Teacher 1.0.9 / owned-stale 1.0.10)

A complete `native_map` / `native_information_page` with exact map-navigation
schema and surface may remain interactive through independent information leaves
while no route is currently offered. With `travel_enabled=true`, `traveling=false`,
`drawing_mode=none` and `next_options=[]`, this Teacher explicitly chooses Await.
That is a bounded strategy decision to seek another public input, not an assertion
of native progress, future readiness, effect completion or legality. Runtime's
unchanged deadline/policy-call budget bounds the wait. Nonempty route lists still
need exact visible public bindings; disabled, absent, malformed, annotation or
other-owner inputs do not become a waiting fallback. Agent1.5.0/1.6.0 declares
this policy; the shared learned-Agent timing and InputSpec remain unchanged.

This is the reviewed correction to repeated confusion between a globally usable
input and a strategy's next desired operation. Existing complete public fields
suffice, so no new environment readiness authority or native contract is added.

The same new Agent pair declares a250ms minimum interval from a successfully
flushed Act reply to its next fresh Current acquisition. This process-local
monotonic cadence reduces speculative sampling pressure; it does not prove an
effect settled or a child is ready. There is no delay between an acquired basis
and its Act. First Current is unpaced, elapsed intervals add no delay, and no
consumption/Teacher state advances while pacing. Runtime still owns deadline,
Stop and unknown fencing; a paced child remains terminable by its public port.
All subsequent Current/catalog/ACK/native checks and8/3 refusal limits remain.
This is a prospective timing experiment, not a claim of reduced stale results.
