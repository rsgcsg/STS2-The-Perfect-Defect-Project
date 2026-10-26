import { P1G_RESULT_SCHEMA, type P1Result } from "@rsgcsg/sts2-connector-client/p1-global";

/** Shared wire construction only. The executing owner still decides status,
 * native steps and commitment facts; this factory adds no execution semantics. */
export function buildP1Result(fields: Omit<P1Result, "schema">): P1Result {
  return { schema: P1G_RESULT_SCHEMA, ...fields };
}
