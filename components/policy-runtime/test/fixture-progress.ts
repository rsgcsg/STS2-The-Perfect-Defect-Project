/** Bounded phase observations distinguish a runner's whole-test timeout from
 * product quotas or an incomplete real file/child operation. No retries. */
export function fixtureProgress(fixture: string): (phase: string, counts?: Record<string, number>) => void {
  const started = performance.now(); let previous = started;
  return (phase, counts = {}) => {
    const current = performance.now();
    console.info(JSON.stringify({ fixture_diagnostic: fixture, phase,
      elapsed_ms: Math.round(current - started), phase_ms: Math.round(current - previous), ...counts }));
    previous = current;
  };
}
