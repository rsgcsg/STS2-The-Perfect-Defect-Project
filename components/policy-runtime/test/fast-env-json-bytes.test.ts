import { describe, expect, it } from "vitest";
import { canonicalJson } from "../src/evidence.js";
import { pyJsonBytes, pyJsonSha256 } from "../src/fast-env/json-bytes.js";

// Golden bytes and digests from Python `stpd.p1.data.json_bytes(json.loads(JSON.stringify(value)))`:
// the encoder hashes what it parses from the decision line.
const value = { b: 1, B: 2, a_b: 3, aB: 4, ab: 5, Z: [1.5, -0, 0.1, 1e21, 123456789.125, -7, true, false, null],
  "é": "café 中 😀   \u007f \u0001 \"q\" \\ \n\t", "": 1, "😀": 2, "": { y: [], x: {} },
  catalog: [{ choice_id: "c1", kind: "play_card", card: { Name: "Zap", name: "zap", cost: 1 } }] };
const PYTHON = "{\"\":{\"x\":{},\"y\":[]},\"B\":2,\"Z\":[1.5,0,0.1,1e+21,123456789.125,-7,true,false,null],\"aB\":4,\"a_b\":3,\"ab\":5,"
  + "\"b\":1,\"catalog\":[{\"card\":{\"Name\":\"Zap\",\"cost\":1,\"name\":\"zap\"},\"choice_id\":\"c1\",\"kind\":\"play_card\"}],"
  + "\"é\":\"café 中 😀   \u007f \\u0001 \\\"q\\\" \\\\ \\n\\t\",\"\":1,\"😀\":2}";

describe("fast-env json_bytes", () => {
  it("equals Python json_bytes byte for byte, keys by code point", () => {
    expect(pyJsonBytes(value).toString("utf8")).toBe(PYTHON);
    expect(pyJsonSha256(value)).toBe("01a92855d0add2665d1b9715ccdf9a65d3409d2c6f9c1f16d44542f5c5120c4c");
    expect(pyJsonSha256(value.catalog)).toBe("a5f7c48026122d63cc1111ed2e7d1c085289b22b25229575d037048ad3beb53e");
    // The localeCompare canonical form is a different byte string: it is not this definition.
    expect(canonicalJson(value.catalog)).not.toBe(pyJsonBytes(value.catalog).toString("utf8"));
  });

  it("refuses values whose Python spelling would differ instead of hashing them wrong", () => {
    expect(() => pyJsonSha256({ x: 0.00001 })).toThrow("py_json_float_exponent");
    expect(() => pyJsonSha256({ x: -0.000099 })).toThrow("py_json_float_exponent");
    expect(() => pyJsonSha256({ x: Number.NaN })).toThrow("py_json_nonfinite_number");
    expect(() => pyJsonSha256({ x: "\ud800" })).toThrow("py_json_lone_surrogate");
    expect(() => pyJsonSha256({ x: undefined })).toThrow("py_json_non_json_value");
    // Python and JS agree from 1e-4 up, and on 1e+21 and above (both exponent forms).
    expect(pyJsonBytes({ x: [0.0001, 1.5e300] }).toString("utf8")).toBe("{\"x\":[0.0001,1.5e+300]}");
  });
});
