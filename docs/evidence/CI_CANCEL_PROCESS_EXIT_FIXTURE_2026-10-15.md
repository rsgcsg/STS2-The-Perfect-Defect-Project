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
uses only this fixture's actual Popen handles.

Ordinary structured completion and HTTP cancellation retain their default child
entry coverage. There are no mocked exits/clocks, deadline extensions, deleted
assertions or skipped tests. Focused tests and independent review assess this
fixture boundary; the selected Python source/test gate and hosted terminal
evidence remain separate before merge. Removing default interpreter teardown
from the positive fixture does not guarantee that durable cancellation writes
finish within one second under every Windows load, or eliminate every flake.

Rollback reverts these test/helper changes and this summary together. Original
failed Hosted artifacts stay immutable. No release, runtime, GPU, Human or
model-quality claim follows from this test-only candidate.
