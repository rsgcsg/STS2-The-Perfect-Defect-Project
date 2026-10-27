"""Build a reviewed, immutable public vocabulary; never called by training.

Inputs are the verified public localization receipt, a metadata-only list of model
class names, and an audited public P1 shape inventory (paths/field names only).
Source game files and translated prose are never copied into the output. Existing
rows and tokenizer are preserved when --base is supplied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from tokenizers import Tokenizer, models, pre_tokenizers, trainers

from stpd.p1.input import V2_INPUT_SPEC, normalized

TABLES = ("cards", "relics", "potions", "powers", "monsters", "events", "characters",
          "card_keywords", "enchantments", "afflictions", "orbs", "intents", "ancients",
          "encounters", "acts", "modifiers")
CATEGORIES = ("Cards", "Relics", "Potions", "Powers", "Monsters", "Events", "Characters",
              "Afflictions", "Enchantments", "Orbs", "Acts", "Modifiers", "Encounters")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def build(localization: Path, model_types: Path, shapes: Path, sdk: Path, base: Path | None,
          assembly_sha256: str) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{64}", assembly_sha256) is None:
        raise ValueError("exact game assembly SHA256 required")
    receipt = json.loads((localization / "manifest.json").read_bytes())
    ids = {"Skip", "SMITH"}
    descriptions: set[str] = set()
    sources, counts = [], {}
    for table in TABLES:
        terms: set[str] = set()
        for language in ("eng", "zhs"):
            raw = (localization / language / (table + ".json")).read_bytes()
            expected = next(f["sha256"] for f in receipt["files"]
                            if f["language"] == language and f["table"] == table + ".json")
            if sha(raw) != expected:
                raise ValueError("public localization differs from its receipt")
            value = json.loads(raw)
            # IDs and named public move/option/keyword IDs, never numeric facts.
            terms.update(k.split(".")[0] for k in value)
            terms.update(part for key in value for part in key.split(".")
                         if re.fullmatch(r"[A-Z][A-Z_0-9]*", part))
            descriptions.update(normalized(text)[0] for text in value.values())
            sources.append({"language": language, "table": table, "sha256": expected})
        ids.update(terms)
        counts[table] = len(terms)
    names = model_types.read_bytes()
    categories = "|".join(CATEGORIES)
    for line in names.decode().splitlines():
        match = re.fullmatch(r"Class MegaCrit\.Sts2\.Core\.Models\.(" + categories
                             + r")\.([A-Za-z0-9]+)", line)
        if match:
            # ModelDb.GetEntry uses StringHelper.Slugify. Only the derived public
            # content identifier is retained; no source body or executable bytes.
            ids.add(re.sub(r"(?<=[A-Za-z0-9])(?=[A-Z])", "_", match[2]).upper())
    public_shapes = json.loads(shapes.read_bytes())
    fields = set(public_shapes["fields"]) | {"availability", "value"}
    kinds = set(public_shapes["kinds"]) | {"public_reference_only"}
    actions = set()
    sdk_sources = []
    for path in sorted(sdk.glob("*.ts")):
        raw = path.read_bytes()
        sdk_sources.append({"name": path.name, "sha256": sha(raw)})
        fields.update(re.findall(r"\b([A-Za-z_][A-Za-z_0-9]*)\??\s*:", raw.decode()))
        if path.name == "reducer.ts":
            actions.update(re.findall(r'"((?:combat|potion|map|treasure|reward|card_reward|selector|'
                                      r'upgrade|enchant|event|shop|rest|relic|other)\.[a-z_]+)"',
                                      raw.decode()))
    actions.update(f"{family}.{action}" for family in ("selector", "upgrade", "enchant")
                   for action in ("confirm", "cancel", "back", "finish"))
    tables = {"ids": ids, "kinds": kinds, "fields": fields, "actions": actions}
    previous = json.loads(base.read_bytes()) if base else None
    if previous:
        if previous["input_spec"] != V2_INPUT_SPEC:
            raise ValueError("base encoding differs; create a new lineage")
        tokenizer = previous["tokenizer"]
    else:
        token = Tokenizer(models.BPE(unk_token="[UNK]"))
        token.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
        token.train_from_iterator(sorted(descriptions), trainers.BpeTrainer(
            vocab_size=4096, min_frequency=2, show_progress=False,
            special_tokens=["[PAD]", "[UNK]", "<NUM>", "<MISSING>", "<REF>", "<EMPTY>"],
            initial_alphabet=pre_tokenizers.ByteLevel.alphabet()))
        tokenizer = token.to_str()
    vocabulary: dict[str, Any] = {
        "tokenizer": tokenizer, "input_spec": V2_INPUT_SPEC,
        "vocabulary_version": previous["vocabulary_version"] + 1 if previous else 1}
    for name, values in tables.items():
        old = previous[name] if previous else ["[UNK]"]
        vocabulary[name] = old + sorted(values - set(old))
    if len(vocabulary["ids"]) > 8192:
        raise ValueError("public IDs exceed the existing M-std capacity")
    vocabulary["provenance"] = {
        "schema": "stpd/public-content-vocabulary-1",
        "source_pack_sha256": receipt["source_pack_sha256"], "tables": sources,
        "public_id_counts": counts, "model_type_names_sha256": sha(names),
        "source_assembly_sha256": assembly_sha256,
        "public_shape_inventory_sha256": sha(shapes.read_bytes()), "sdk_sources": sdk_sources,
        "text": "BPE fixed once from public glossary; no per-training fitting",
        "contains": "derived technical IDs, public schema names and tokenizer only",
    }
    return vocabulary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--localization", type=Path, required=True)
    parser.add_argument("--model-types", type=Path, required=True)
    parser.add_argument("--public-shapes", type=Path, required=True)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--base", type=Path)
    parser.add_argument("--assembly-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = build(args.localization, args.model_types, args.public_shapes, args.sdk, args.base,
                  args.assembly_sha256)
    with args.output.open("x") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    print(json.dumps({"sha256": sha(args.output.read_bytes()),
                      **{key: len(value[key]) for key in ("ids", "kinds", "fields", "actions")}}))


if __name__ == "__main__":
    main()
