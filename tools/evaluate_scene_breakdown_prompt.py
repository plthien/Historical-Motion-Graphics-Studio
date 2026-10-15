from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROMPT_ROOT = ROOT / "contracts" / "prompts" / "v4" / "scene_breakdown"
SCHEMA_ROOT = ROOT / "contracts" / "schemas" / "v4"


def main() -> int:
    prompts = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(PROMPT_ROOT.glob("*.md"))
    }
    output_schema = json.loads(
        (SCHEMA_ROOT / "scene_breakdown_section_llm_output_v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    forbidden = (
        "candidate_boundaries",
        "feasible_beat_transitions",
        "annotated_narration",
        "end_boundary_id",
    )
    prompt_text = "\n".join(prompts.values())
    payload = {
        "status": "not_executed",
        "reason": "No real provider call is performed by this harness.",
        "prompt_files": sorted(prompts),
        "response_schema_id": output_schema.get("$id"),
        "forbidden_terms_present": [
            value for value in forbidden if value in prompt_text
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if payload["forbidden_terms_present"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
