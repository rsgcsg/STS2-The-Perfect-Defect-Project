import { nativeScenario } from "./nativeLogicalFixtures.js";
import { EnvironmentControllerSession, NativeLogicalSession, PlayerEnvironmentRestClient } from "../src/index.js";
import { openRealStoreTransport } from "./realStoreTransport.mjs";

/** Test-only HTTP envelope around a separately built game-free executable. Its
 * Current/Read/C/catalog/retain/release bytes and accounting are production C#
 * Store/Projector output. The control/capability/Await responses are synthetic. */
export async function realStoreBridge(options = {}) {
  const source = nativeScenario(1);
  const transport = await openRealStoreTransport({ ...options, fallback: async (url, body) => {
    const value = options.fallback ? await options.fallback(url, body) : source.route(url, body);
    if (url.pathname.split("/").at(-1) === "capabilities") {
      value.supported_methods.push("current_owned"); value.implemented_mechanisms.push("native_current_reader_owned_v1"); value.limits.max_captures = 4;
    }
    return value;
  } });
  const rest = new PlayerEnvironmentRestClient(transport.endpoint, 3000);
  const controller = new EnvironmentControllerSession(rest, { productId: "test", productName: "Test", productVersion: "1", clientInstanceId: "sdk-fixture" });
  const session = new NativeLogicalSession(rest, controller);
  try {
    const capabilities = (await session.capabilities()).data;
    await controller.register(capabilities.session, capabilities.control_policy);
    return { ...transport, rest, session, controller, capabilities,
      close: async () => { try { await controller.close(); } finally { await transport.close(); } } };
  } catch (error) { await transport.close(); throw error; }
}
