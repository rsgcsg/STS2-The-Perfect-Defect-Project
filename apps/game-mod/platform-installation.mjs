// Resolve the one production Mod from the game's actual Steam library.
// A candidate is installed bytes, never evidence that this process loaded it.
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { STS2_APP_ID } from "../../components/host-runtime/src/game-installation.mjs";

export const PLATFORM_WORKSHOP_ITEM_ID = "3806646116";
const modId = "STS2_PLATFORM";

function regular(file) {
  return fs.existsSync(file) && fs.statSync(file).isFile();
}

function installedWorkshopItem(acf, itemId) {
  // Only the installed section grants a local candidate. Details/subscription do not.
  const section = /"WorkshopItemsInstalled"\s*\{([\s\S]*?)\n\s*\}\s*"WorkshopItemDetails"/u.exec(acf)?.[1];
  return section !== undefined && new RegExp(`"${itemId}"\\s*\\{`, "u").test(section);
}

function matchesGameApp(acf, gameDirectory) {
  const appId = /"appid"\s*"([^"]+)"/u.exec(acf)?.[1];
  const installDir = /"installdir"\s*"([^"]+)"/u.exec(acf)?.[1];
  return appId === STS2_APP_ID && installDir === path.basename(gameDirectory);
}

export function readPeIdentity(dll, tool) {
  if (!regular(tool)) throw new Error("identity_reader_unavailable");
  const result = spawnSync("dotnet", [tool, "identity", dll], { encoding: "utf8", timeout: 10000 });
  if (result.status !== 0 || result.error) throw new Error("identity_reader_failed");
  const identity = JSON.parse(result.stdout);
  if (!/^[a-f0-9]{64}$/u.test(identity.sha256 ?? "")
      || !/^[a-f0-9-]{36}$/u.test(identity.module_version_id ?? ""))
    throw new Error("identity_reader_invalid");
  return { sha256: identity.sha256, module_version_id: identity.module_version_id };
}

export function resolvePlatformInstallation(installation, {
  itemId = PLATFORM_WORKSHOP_ITEM_ID,
  identityTool,
  identity = readPeIdentity,
  workshopRoot
} = {}) {
  if (!installation?.game_dir || !installation?.mods_dir) throw new Error("installation_unavailable");
  const manual = installation.mods_dir;
  const manualDll = path.join(manual, `${modId}.dll`);
  const manualManifest = path.join(manual, `${modId}.json`);
  if (regular(manualDll) !== regular(manualManifest)) throw new Error("incomplete_manual_mod_installation");
  const library = path.resolve(installation.game_dir, "../../..");
  const appManifest = path.join(library, "steamapps", `appmanifest_${STS2_APP_ID}.acf`);
  const metadata = path.join(library, "steamapps", "workshop", `appworkshop_${STS2_APP_ID}.acf`);
  const workshop = workshopRoot ?? path.join(library, "steamapps", "workshop", "content", STS2_APP_ID, itemId);
  const itemInstalled = regular(appManifest) && matchesGameApp(fs.readFileSync(appManifest, "utf8"), installation.game_dir)
    && regular(metadata)
    && installedWorkshopItem(fs.readFileSync(metadata, "utf8"), itemId);
  const workshopDll = path.join(workshop, `${modId}.dll`);
  const workshopManifest = path.join(workshop, `${modId}.json`);
  if (itemInstalled && regular(workshopDll) !== regular(workshopManifest))
    throw new Error("incomplete_workshop_mod_installation");
  const manualPresent = regular(manualDll);
  const workshopPresent = itemInstalled && regular(workshopDll);
  if (manualPresent && workshopPresent) throw new Error("ambiguous_mod_installation");
  if (!manualPresent && !workshopPresent) throw new Error("platform_mod_not_installed");
  const kind = workshopPresent ? "workshop" : "manual";
  const directory = workshopPresent ? workshop : manual;
  const dll = workshopPresent ? workshopDll : manualDll;
  const manifestPath = workshopPresent ? workshopManifest : manualManifest;
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  if (manifest.id !== modId || manifest.has_dll !== true || manifest.has_pck !== false)
    throw new Error("unified_mod_manifest_mismatch");
  return { kind, directory, dll, manifest_path: manifestPath, manifest,
    item_id: workshopPresent ? itemId : null, artifact: identity(dll, identityTool) };
}
