import { z } from "zod";
import { isJsonObject, type JsonObject } from "./json.js";
import { SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL, type DecodedPlayerPayload } from "./protocol.js";
import { freezeNativeLogical } from "./nativeLogicalWire.js";

export const PLAYER_ENVIRONMENT_CLIENT_REVOCATION_ROUTE = "/api/player-environment/clients/revoke" as const;
export const PLAYER_ENVIRONMENT_CLIENT_REVOCATION_SCHEMA = "sts2.player-environment/client-revoke-1" as const;
export const PLAYER_ENVIRONMENT_CLIENT_REVOCATION_MAX_BYTES = 1024;
const identifier = z.string().min(1).max(128).regex(/^[A-Za-z0-9_.-]+$/u);
const request = z.object({ runtime_instance_id: identifier, client_session_id: identifier }).strict();
const acknowledgement = z.object({
  protocol_version: z.literal(SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL),
  schema: z.literal(PLAYER_ENVIRONMENT_CLIENT_REVOCATION_SCHEMA),
  runtime_instance_id: identifier,
  client_session_id: identifier,
  status: z.literal("client_revoked"),
  closed: z.literal(true),
  controller: z.null()
}).strict();

/** controller:null confirms only this original client's closure, not absence
 * of another client's controller. */
export type PlayerEnvironmentClientRevocation = z.infer<typeof acknowledgement>;

export function validatePlayerClientRevocationRequest(value: unknown): JsonObject {
  const parsed = request.parse(value);
  if (Buffer.byteLength(JSON.stringify(parsed), "utf8") > PLAYER_ENVIRONMENT_CLIENT_REVOCATION_MAX_BYTES)
    throw new Error("Client revocation request exceeds its 1 KiB limit");
  return parsed;
}

export function decodePlayerClientRevocation(value: unknown): DecodedPlayerPayload<PlayerEnvironmentClientRevocation> {
  if (!isJsonObject(value)) throw new Error("Client revocation acknowledgement must be an object");
  return { raw: freezeNativeLogical(value), data: freezeNativeLogical(acknowledgement.parse(value)) };
}
