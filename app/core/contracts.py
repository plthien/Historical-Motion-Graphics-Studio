from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ContractPathResolver:
    root_dir: Path

    @classmethod
    def from_project_root(cls, root_dir: Path | None = None) -> "ContractPathResolver":
        project_root = (root_dir or Path(__file__).resolve().parents[2]).resolve()
        return cls(root_dir=project_root)

    @property
    def contracts_dir(self) -> Path:
        return self.root_dir / "contracts"

    @property
    def schemas_dir(self) -> Path:
        return self.contracts_dir / "schemas"

    @property
    def plan_schemas_dir(self) -> Path:
        return self.schemas_dir / "plans"

    @property
    def valid_examples_dir(self) -> Path:
        return self.contracts_dir / "examples" / "valid"

    @property
    def invalid_examples_dir(self) -> Path:
        return self.contracts_dir / "examples" / "invalid"

    @property
    def config_dir(self) -> Path:
        return self.contracts_dir / "config"

    @property
    def samples_dir(self) -> Path:
        return self.root_dir / "samples"

    def openapi_path(self) -> Path:
        return self.contracts_dir / "openapi" / "youtube_ai_studio_api.openapi.yaml"

    def error_response_schema_path(self) -> Path:
        return self.schemas_dir / "core" / "error_response.schema.json"

    def validation_report_schema_path(self) -> Path:
        return self.schemas_dir / "core" / "validation_report.schema.json"

    def channel_dna_schema_path(self) -> Path:
        return self.schemas_dir / "settings" / "channel_dna.schema.json"

    def content_edit_allowlist_schema_path(self) -> Path:
        return self.schemas_dir / "config" / "content_edit_allowlist.schema.json"

    def content_edit_allowlist_path(self) -> Path:
        return self.config_dir / "content_edit_allowlist.yaml"


    def schema_files(self) -> list[Path]:
        return sorted(self.schemas_dir.rglob("*.schema.json"))

    def valid_plan_example_paths(self) -> list[Path]:
        return [path for path in sorted(self.valid_examples_dir.glob("*.valid.json")) if self._example_contains_plan_type(path)]

    def invalid_plan_example_paths(self) -> list[Path]:
        return sorted(self.invalid_examples_dir.glob("*.invalid.json"))

    @staticmethod
    def _example_contains_plan_type(path: Path) -> bool:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        payload = document.get("payload") if isinstance(document, dict) and isinstance(document.get("payload"), dict) else document
        return isinstance(payload, dict) and isinstance(payload.get("plan_type"), str)
