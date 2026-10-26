# Q stop visibility and recount failure isolation (2026-10-26)

Scope: G1 Workbench implementation and its replaceable summary. The importer,
Store, approved Tool policy, Curation and immutable source/projection identities
remain the authorities. This is source/test evidence; it does not update an
installed Workbench, Mod or Annotator.

The 13:13 incident's retained failure identifies projection
`7ad22366e47d73c00ad63768e02697af45dad732669238932b109115daefe66d`, but the
preparation layer replaced ValueError/KeyError/TypeError/OSError with
`invalid_projection`. The original type, errno and traceback were not retained.
Later successful inspection does not recover that lost fact. No particular errno
is established for the historical incident, and the old projection is not replaced.

## Reproduced defects and decisions

- A real synthetic Store/Curation fixture repeatedly recounts unchanged inputs
  because an empty SQLite WAL's mtime changes when connections open/close. Ignore
  empty WAL files in the change signature; continue tracking the main database and
  nonempty WAL. Actual ledger-use changes must still trigger a recount. Capture
  each stat once to avoid an exists/stat race.
- A projection inspection exception erased its producing type/message. Log the
  original exception and traceback with its projection ID at that owning catch;
  retain the stable conflict response and all existing checks.
- A failed derived recount disabled all ingestion. Keep the last complete cohort,
  mark it stale with the error, and continue the existing import/preservation path.
  Do not silently exclude bad projections to manufacture a partial cohort. Do not
  repeatedly recount the same failed generation; new inputs or the existing
  explicit refresh command may trigger a new recount.
- Existing HTTP rendering reads live maintenance state, and the next successful
  tick can update the static failure page. Thus “failure is never rendered” is not
  established. The actual visibility gaps are same-color statuses, missing
  duration/freshness, outdated pending counts after early returns, and page-write
  failures sharing ingestion's fatal path. Publish health independently of successful
  recount, preserve metadata-only queue observation while stopped/unknown, and
  render a prominent alert from the same persisted `_maintenance` object.

## Chain and failure boundaries

| Step | Requires / provides | Failure behavior |
|---|---|---|
| Discover | Native owner, Close metadata / scoped candidates | Observe metadata during play; do not dispatch new heavy work |
| Import | Exact Tool and closed original / immutable Store source | Existing publication unknown remains blocked; no automatic retry |
| Preserve | Store source / peer full-readback receipt | Existing operation owns result and reconciliation |
| Derive | Approved Tool, source and run / immutable projection | Identity/hash/version checks remain mandatory |
| Recount | Verified projections and Curation / complete preview and summary | Keep prior complete view and visible error; intake remains enabled |
| Present | Existing maintenance state and saved summary / HTTP and static HTML | View-write errors are logged without disabling preservation; browser freshness warning makes stale saved pages visible |

The normal status freshness threshold is 90 seconds. An active step uses ten
minutes, and a step lasting over ten minutes gets a diagnostic warning. Neither
threshold is an operation timeout or authorization to cancel/retry anything.
All runtime state stays in the existing operation file; there is no new service,
ledger, monitor, notification channel, automatic freeze or training.

Regression coverage uses the real synthetic Store/ledger/cache, a deliberate
inspection error, persisted failure/reopen/recovery, actual HTTP routing,
original import and peer readback, unknown fencing, page-write failure and the
shipped browser clock. See `python/docs/RECOUNT-SOP.zh-CN.md` for operation and
recovery; exact candidate checks belong to its PR and CI, not this date alone.
