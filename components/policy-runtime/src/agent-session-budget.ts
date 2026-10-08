import { AgentSessionError, sessionInteger } from "./agent-session-contracts.js";

export interface AgentByteReservation { readonly bytes: number; release(): void }
/** One owner budget shared by SDK assembly, retained inputs and pending query replies. */
export class AgentByteBudget {
  private charged = 0;
  constructor(readonly maximum: number) { sessionInteger(maximum, true, 256 * 1024 * 1024); }
  get used(): number { return this.charged; }
  reserve(bytes: number): AgentByteReservation {
    sessionInteger(bytes);
    if (this.charged + bytes > this.maximum) throw new AgentSessionError("retained_acquisition_byte_capacity");
    this.charged += bytes;
    let released = false;
    return { bytes, release: () => { if (!released) { released = true; this.charged -= bytes; } } };
  }
}
