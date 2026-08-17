import json

import pytest

from algorithm.training.verify_adapter_reload import validate_adapter_directory


def test_adapter_directory_requires_config_and_safetensors(tmp_path):
    with pytest.raises(FileNotFoundError, match="adapter_config.json"):
        validate_adapter_directory(tmp_path)

    (tmp_path / "adapter_config.json").write_text(json.dumps({"r": 16}), encoding="utf-8")
    (tmp_path / "adapter_model.safetensors").write_bytes(b"adapter")
    validate_adapter_directory(tmp_path)
