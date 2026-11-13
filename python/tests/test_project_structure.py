from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


class ProjectStructureTest(unittest.TestCase):
    def test_canonical_documents_and_memory_exist(self):
        required = [
            "AGENT.md",
            "AGENTS.md",
            "CONTRIBUTING.md",
            "docs/DOCUMENT_MAP.md",
            "docs/STATUS.md",
            "docs/ROADMAP.md",
            "docs/ARCHITECTURE.md",
            "docs/PROJECT_SYSTEM.md",
            "docs/CODE_STYLE.md",
            "docs/INTERFACES.md",
            "docs/DATA_AND_PROVENANCE.md",
            "docs/DATA_LIFECYCLE.md",
            "docs/QWEN_INTEGRATION.md",
            "docs/BENCHMARKS.md",
            "docs/V0_EXECUTION_PLAN.md",
            "docs/PRE_QWEN_OPERATIONS.md",
            "docs/memory/README.md",
            "docs/memory/CURRENT.md",
            "docs/memory/DECISIONS.md",
            "docs/memory/OPEN_QUESTIONS.md",
            "docs/memory/HANDOFF.md",
        ]
        for relative in required:
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_document_map_links_every_canonical_document(self):
        document_map = (ROOT / "docs/DOCUMENT_MAP.md").read_text(encoding="utf-8")
        for filename in (
            "STATUS.md",
            "ROADMAP.md",
            "ARCHITECTURE.md",
            "PROJECT_SYSTEM.md",
            "CODE_STYLE.md",
            "INTERFACES.md",
            "DATA_AND_PROVENANCE.md",
            "DATA_LIFECYCLE.md",
            "HUMAN_CORPUS.md",
            "QWEN_INTEGRATION.md",
            "BENCHMARKS.md",
            "V0_EXECUTION_PLAN.md",
            "PRE_QWEN_OPERATIONS.md",
        ):
            self.assertIn(filename, document_map)

    def test_machine_readable_schemas_parse_and_have_unique_ids(self):
        schema_paths = sorted((ROOT / "schemas").glob("*.schema.json"))
        self.assertEqual(
            {path.name for path in schema_paths},
            {
                "developer-combination-v1.schema.json",
                "developer-project-v1.schema.json",
                "experiment-manifest-v0.schema.json",
                "data-manifest-v0.schema.json",
                "frozen-joint-feature-manifest-v1.schema.json",
                "human-collection-campaign-v1.schema.json",
                "human-collection-profile-v1.schema.json",
                "human-corpus-combination-v1.schema.json",
                "model-artifact-manifest-v0.schema.json",
                "research-action-v0.schema.json",
                "research-state-v0.schema.json",
                "research-transition-v0.schema.json",
                "rl-inference-4.schema.json",
                "training-input-manifest-v1.schema.json",
            },
        )
        ids = set()
        for path in schema_paths:
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(value["$schema"], "https://json-schema.org/draft/2020-12/schema")
            Draft202012Validator.check_schema(value)
            self.assertNotIn(value["$id"], ids)
            ids.add(value["$id"])

    def test_readme_states_current_maturity_without_discarding_smoke(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
        self.assertIn("pre-alpha", readme)
        self.assertIn("not the final stpd v0 model", readme)
        self.assertIn("reusable qualification baseline", readme)


if __name__ == "__main__":
    unittest.main()
