import { randomUUID } from "node:crypto";
import {
  closeSync,
  copyFileSync,
  existsSync,
  fsyncSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
  writeSync
} from "node:fs";
import path from "node:path";

const PROFILE_ID = /^[a-z0-9][a-z0-9._-]{0,63}$/u;

export function validateProfileId(profileId) {
  if (!PROFILE_ID.test(profileId ?? "")) {
    throw new Error("Profile ID must contain 1-64 lowercase letters, digits, dots, underscores, or hyphens.");
  }
  return profileId;
}

function assertContained(root, target) {
  const relative = path.relative(root, target);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error(`Refusing profile operation outside the profile root: ${target}`);
  }
}

export function isolatedProfilePaths(localRoot, profileId, platform = process.platform) {
  validateProfileId(profileId);
  const profilesRoot = path.resolve(localRoot, "profiles");
  const profileRoot = path.resolve(profilesRoot, profileId);
  assertContained(profilesRoot, profileRoot);
  const home = path.join(profileRoot, "home");
  if (platform === "win32") {
    return {
      profiles_root: profilesRoot,
      profile_root: profileRoot,
      home,
      appdata: path.join(home, "AppData", "Roaming"),
      local_appdata: path.join(home, "AppData", "Local"),
      expected_user_data_root: path.join(home, "AppData", "Roaming", "SlayTheSpire2")
    };
  }
  if (platform === "darwin") {
    return {
      profiles_root: profilesRoot,
      profile_root: profileRoot,
      home,
      expected_user_data_root: path.join(home, "Library", "Application Support", "SlayTheSpire2")
    };
  }
  return {
    profiles_root: profilesRoot,
    profile_root: profileRoot,
    home,
    xdg_data_home: path.join(home, ".local", "share"),
    expected_user_data_root: path.join(home, ".local", "share", "SlayTheSpire2")
  };
}

export function prepareIsolatedProfile(localRoot, profileId, platform = process.platform) {
  const paths = isolatedProfilePaths(localRoot, profileId, platform);
  for (const directory of Object.values(paths)) mkdirSync(directory, { recursive: true });
  const generationFile = path.join(paths.profile_root, "headless-profile.json");
  if (!existsSync(generationFile)) {
    writeFileSync(generationFile, `${JSON.stringify({
      schema_version: 1,
      profile_id: profileId,
      generation_id: randomUUID(),
      created_at: new Date().toISOString()
    }, null, 2)}\n`);
  }
  return {
    ...paths,
    generation: JSON.parse(readFileSync(generationFile, "utf8"))
  };
}

export function resetIsolatedProfile(localRoot, profileId, platform = process.platform) {
  const paths = isolatedProfilePaths(localRoot, profileId, platform);
  assertContained(paths.profiles_root, paths.profile_root);
  const previousGeneration = existsSync(path.join(paths.profile_root, "headless-profile.json"))
    ? JSON.parse(readFileSync(path.join(paths.profile_root, "headless-profile.json"), "utf8"))
    : null;
  rmSync(paths.profile_root, { recursive: true, force: true });
  const prepared = prepareIsolatedProfile(localRoot, profileId, platform);
  return {
    status: "reset",
    profile_id: profileId,
    previous_generation_id: previousGeneration?.generation_id ?? null,
    generation_id: prepared.generation.generation_id,
    profile_root: prepared.profile_root,
    expected_user_data_root: prepared.expected_user_data_root
  };
}

export function isolatedProfileLaunch(localRoot, profileId, platform = process.platform) {
  const profile = prepareIsolatedProfile(localRoot, profileId, platform);
  // The installed Annotator config may contain absolute daily Human paths.
  // An isolated Host owns its output namespace as well as its game profile.
  // Keep unique evidence outside resettable profile payloads.
  const recorderRoot = path.resolve(localRoot, "evidence", "annotator", profileId,
    profile.generation.generation_id);
  const environment = {
    HOME: profile.home,
    USERPROFILE: profile.home,
    STS2_HUMAN_ANNOTATOR_RECORDING_ROOT: path.join(recorderRoot, "recordings"),
    STS2_HUMAN_ANNOTATOR_STATUS_PATH: path.join(recorderRoot, "runtime.json")
  };
  if (platform === "win32") {
    environment.APPDATA = profile.appdata;
    environment.LOCALAPPDATA = profile.local_appdata;
  } else if (platform === "linux") {
    environment.XDG_DATA_HOME = profile.xdg_data_home;
  }
  return {
    mode: "isolated_local_profile",
    isolation_status: "source_backed_experimental",
    profile_id: profileId,
    generation_id: profile.generation.generation_id,
    profile_root: profile.profile_root,
    expected_user_data_root: profile.expected_user_data_root,
    steam: "disabled_before_platform_initialization",
    client_id: "1",
    args: ["--force-steam=off", "--clientId=1"],
    environment
  };
}

export function resolveLaunchProfile({
  localRoot,
  isolatedProfileId = null,
  sharedProfileAcknowledged = false,
  platform = process.platform
}) {
  if (isolatedProfileId && sharedProfileAcknowledged) {
    throw new Error("Choose either an isolated profile or the shared Steam profile, not both.");
  }
  if (isolatedProfileId) return isolatedProfileLaunch(localRoot, isolatedProfileId, platform);
  if (sharedProfileAcknowledged) {
    return {
      mode: "shared_steam_profile",
      isolation_status: "not_isolated",
      profile_id: null,
      generation_id: null,
      profile_root: null,
      expected_user_data_root: null,
      steam: "enabled",
      client_id: null,
      args: [],
      environment: {}
    };
  }
  throw new Error("Choose --isolated-profile <id>, or explicitly acknowledge --shared-profile.");
}

export function publicProfileDescriptor(profile) {
  return {
    mode: profile.mode,
    isolation_status: profile.isolation_status,
    profile_id: profile.profile_id,
    generation_id: profile.generation_id,
    profile_root: profile.profile_root,
    expected_user_data_root: profile.expected_user_data_root,
    steam: profile.steam,
    client_id: profile.client_id
  };
}

export function enableConnectorModLoading(
  localRoot,
  profileId,
  {
    expectedSettingsSchema,
    acknowledgeEarlyAccessDisclaimer = false,
    platform = process.platform
  } = {}
) {
  if (!Number.isSafeInteger(expectedSettingsSchema) || expectedSettingsSchema < 1) {
    throw new Error("An exact expected settings schema is required.");
  }
  const paths = isolatedProfilePaths(localRoot, profileId, platform);
  const settingsFile = path.join(paths.expected_user_data_root, "default", "1", "settings.save");
  assertContained(paths.profile_root, settingsFile);
  if (!existsSync(settingsFile)) {
    throw new Error("The isolated profile has not completed one native bootstrap; settings.save is absent.");
  }
  const settings = JSON.parse(readFileSync(settingsFile, "utf8"));
  if (settings.schema_version !== expectedSettingsSchema) {
    throw new Error(
      `Settings schema mismatch: expected ${expectedSettingsSchema}, observed ${settings.schema_version}.`
    );
  }
  if (settings.mod_settings != null
      && (typeof settings.mod_settings !== "object" || Array.isArray(settings.mod_settings))) {
    throw new Error("Unexpected mod_settings shape; refusing to rewrite the native settings file.");
  }
  const modLoadingAlreadyEnabled = settings.mod_settings?.mods_enabled === true;
  const disclaimerAlreadyAcknowledged = settings.seen_ea_disclaimer === true;
  if (modLoadingAlreadyEnabled
      && (!acknowledgeEarlyAccessDisclaimer || disclaimerAlreadyAcknowledged)) {
    return { status: "already_enabled", profile_id: profileId, settings_file: settingsFile };
  }
  const updated = {
    ...settings,
    mod_settings: { ...(settings.mod_settings ?? {}), mods_enabled: true },
    ...(acknowledgeEarlyAccessDisclaimer ? { seen_ea_disclaimer: true } : {})
  };
  const backup = `${settingsFile}.before-headless-mod-consent`;
  const temporary = `${settingsFile}.headless-${randomUUID()}.tmp`;
  copyFileSync(settingsFile, backup);
  const fd = openSync(temporary, "wx");
  try {
    writeSync(fd, `${JSON.stringify(updated, null, 2)}\n`, null, "utf8");
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
  renameSync(temporary, settingsFile);
  return {
    status: "enabled_cold_start_required",
    profile_id: profileId,
    settings_schema: expectedSettingsSchema,
    acknowledgements: {
      connector_mod_loading: true,
      early_access_disclaimer: acknowledgeEarlyAccessDisclaimer
        || disclaimerAlreadyAcknowledged
    },
    settings_file: settingsFile,
    backup_file: backup
  };
}
