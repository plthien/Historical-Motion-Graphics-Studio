import pytest
import yaml
from pathlib import Path
from tools.validate_contracts import load_yaml

def test_yaml_loader_rejects_duplicate_keys(tmp_path: Path):
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("a: 1\nb: 2\na: 3\n")
    
    with pytest.raises(yaml.constructor.ConstructorError, match="found duplicate key 'a'"):
        load_yaml(yaml_file)

def test_yaml_loader_accepts_valid_yaml(tmp_path: Path):
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("a: 1\nb: 2\nc: 3\n")
    
    result = load_yaml(yaml_file)
    assert result == {"a": 1, "b": 2, "c": 3}
