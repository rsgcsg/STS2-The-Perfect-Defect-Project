# Cancellation fixture: main return and OS exit

This is a test-only candidate from develop
`458ae23e4d9fc543f50e54b302015853f4d5c024`. It changes only the checkpoint
cancellation fixture and this summary. Product child entry, process runner,
one-second cancellation grace and unknown-result semantics are unchanged.

## Original failure remains failed

PR179 at `37a2db4d` produced a real Windows assertion failure in run
`38587773043`, job `115934361074`. The same child reported `cancelled` and
returned from `main()` with code zero 0.454 seconds after cancellation. The
parent had not observed OS exit at 1.125 seconds and forcibly terminated it;
the actual wait receipt was exit one, forced true. `interrupted_unknown` was
therefore the correct conservative result. The later 55-minute job cap also
interrupted Connector, but does not explain away this earlier assertion.

The exact Python shutdown/finalizer/scheduling cause is still unknown. This
candidate does not turn that failed run into a pass or qualify default Windows
interpreter shutdown within one second.

## Explicit process-exit preconditions

The safe-checkpoint and explicit-resume test retains its original assertions.
Only its first cancelled child opts into a cooperative exit fixture: actual
`child.main()` must return zero, and the original successfully emitted terminal
must name the same operation/attempt with state `cancelled`. Diagnostics must
close and stdout/stderr must flush before the child voluntarily exits using its
actual code. Parent Popen/readers/wait still prove real exit zero, non-forced.
Resume restores the original adapter and Popen, using the default module entry.

The new opposite case lets actual main return zero after the same terminal,
then keeps that child alive at a fixture shutdown fence. The original product
cancellation timer must kill it. The test binds operation/attempt/PID and phase
ordering, and requires actual nonzero wait, forced true, unknown domain result,
retained checkpoint, no selected result and no automatic retry. Final cleanup
uses only this fixture's actual Popen and pre-bound worker handles.

Ordinary structured completion and HTTP cancellation retain their default child
entry coverage. There are no mocked exits/clocks, deadline extensions or skipped
tests. Original cancellation/resume and unknown/no-retry assertions remain;
the incorrect single-PID assertion is replaced by launcher/worker identity and
termination checks. Focused tests and independent review assess this
fixture boundary; the selected Python source/test gate and hosted terminal
evidence remain separate before merge. Removing default interpreter teardown
from the positive fixture does not guarantee that durable cancellation writes
finish within one second under every Windows load, or eliminate every flake.

Rollback reverts these test/helper changes and this summary together. Original
failed Hosted artifacts stay immutable. No release, runtime, GPU, Human or
model-quality claim follows from this test-only candidate.

## Windows launcher and worker identities

PR182 at `d1a2dbb6`, run `38602247594`, exposed a second fixture error:
the new negative case required worker PID5944 to equal Popen PID7900.
Windows venv redirectors may launch a separate interpreter. The original
artifact did not retain its full phase JSON or worker-handle observations,
so this run proves neither a surviving worker nor its absence. Its assertions
after the PID mismatch were not executed and remain unqualified.

The fixture now binds the worker's real PID/PPID, run, attempt, event and
checkpoint before cancellation. It preserves Popen's separate launcher identity
and verifies the direct or observed Windows parent/child relationship. Windows
holds that worker's native process handle before cancel using CPython's native
`_winapi` binding, which preserves Win64 HANDLE width. After the original SUT
stop, an immediate zero-timeout observation retains its actual value. The
positive cooperative case still requires the real worker handle already
signaled. The negative forced-unknown case checks all original unknown,
forced-exit, no-result and no-retry assertions first, then performs one separate
read-only wait of at most five seconds on that same pre-held handle. A timeout
still fails; later fixture cleanup cannot rescue it. This is an observation of
asynchronous worker exit without fixture intervention, not a promise that every
worker handle was signaled at SUT return or within the product cancellation
window. No PID is reopened after the kill, and worker exit code is not assumed
to equal the launcher's code.

Any fixture cleanup is separately labelled and cannot become SUT stop evidence.
Bounded phase, ownership and liveness observations are also retained in JUnit
properties, including on failure, so a temporary diagnostic pathname is no
longer the only record. Production deadlines, clocks, runner and unknown
semantics remain unchanged. A new head still needs its own Windows/Hosted gate;
this source correction does not qualify the original failed run.

## Asynchronous forced exit and cleanup evidence

PR182 at `a4cf9a3d`, run `38613654545`, failed its added immediate-signaled
negative assertion. The original property records the bound Windows worker
handle as Wait258 then exit0, while the owned launcher's receipt is forced
exit1 and the operation correctly retains unknown/no-result/no-retry. Cleanup
then attempted TerminateProcess and raised WinError5 before its final native
wait, masking the first assertion. The worker's final handle state is unknown
in that record; exit0, AccessDenied and a flag assigned after the failing call
do not establish no intervention or final termination.

The immediate-signaled condition was stronger than the original forced-unknown
contract. Windows termination of another process is asynchronous. The revised
negative test retains the immediate snapshot and separately bounds its native
handle observation. This does not alter the product grace or promote the old
failed run to success. Cleanup records termination attempted/succeeded/error
separately and always remains distinct from observer acceptance. AccessDenied
can be resolved as an exit race only by a subsequent actual signaled wait on
the same handle. An original assertion remains primary when cleanup also fails;
cleanup failure without an original error still fails, and both are retained
in bounded JUnit properties. Unit branch checks exercise these diagnostic
rules without claiming actual Windows execution. A new exact-head Windows
source/test run remains required.
