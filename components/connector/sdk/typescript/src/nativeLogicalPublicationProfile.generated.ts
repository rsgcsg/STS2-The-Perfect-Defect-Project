// Generated from contracts/native-logical-publication-profile-v1.json.
// Refresh with tools/generate-native-logical-profile.mjs; never edit the seam list here.
import { freezeNativeLogical } from "./nativeLogicalWire.js";

export const NATIVE_LOGICAL_PUBLICATION_PROFILE = freezeNativeLogical({
  "schema": "sts2.player-environment/native-logical-publication-profile-1",
  "profile_id": "native-logical-publication-profile-v1",
  "input_profile": "native-logical-v1",
  "delivery_mode": "full_reference",
  "eager_scope": [
    "persistent",
    "interaction",
    "referents",
    "catalog"
  ],
  "required_seams": [
    {
      "source_seam": "connector_initial_observation",
      "version": "1",
      "coverage": "complete_at_seam"
    },
    {
      "source_seam": "native_owner_ready",
      "version": "1",
      "coverage": "complete_at_seam"
    },
    {
      "source_seam": "native_target_focus",
      "version": "1",
      "coverage": "complete_at_seam"
    },
    {
      "source_seam": "native_card_preview",
      "version": "1",
      "coverage": "complete_at_seam"
    },
    {
      "source_seam": "native_inspect_preview",
      "version": "1",
      "coverage": "complete_at_seam"
    },
    {
      "source_seam": "native_input_callback",
      "version": "1",
      "coverage": "complete_at_seam"
    },
    {
      "source_seam": "connector_input_start",
      "version": "1",
      "coverage": "complete_at_seam"
    },
    {
      "source_seam": "native_terminal_entry",
      "version": "1",
      "coverage": "complete_at_seam"
    },
    {
      "source_seam": "native_information_owner",
      "version": "1",
      "coverage": "complete_at_seam"
    },
    {
      "source_seam": "native_information_content",
      "version": "1",
      "coverage": "complete_at_seam"
    },
    {
      "source_seam": "native_reward_owner",
      "version": "1",
      "coverage": "complete_at_seam"
    },
    {
      "source_seam": "native_reward_catalog",
      "version": "1",
      "coverage": "complete_at_seam"
    },
    {
      "source_seam": "native_reward_input_availability",
      "version": "1",
      "coverage": "complete_at_seam"
    }
  ]
} as const);
export const NATIVE_LOGICAL_PUBLICATION_PROFILE_SHA256 = "c060cfd354c6702e10711e6329848836ec133f2117841750e317b6ab27b244cf" as const;
