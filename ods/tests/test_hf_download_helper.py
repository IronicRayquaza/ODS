from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


HELPER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "download-hf-artifact.py"


def _load_helper():
    spec = importlib.util.spec_from_file_location("download_hf_artifact", HELPER_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_parse_huggingface_resolve_url_with_nested_filename():
    helper = _load_helper()

    repo_id, revision, filename = helper.parse_huggingface_resolve_url(
        "https://huggingface.co/unsloth/Llama-4-Scout-GGUF/resolve/main/"
        "Q4_K_M/model-00001-of-00002.gguf"
    )

    assert repo_id == "unsloth/Llama-4-Scout-GGUF"
    assert revision == "main"
    assert filename == "Q4_K_M/model-00001-of-00002.gguf"


def test_parse_huggingface_resolve_url_rejects_non_hf_url():
    helper = _load_helper()

    with pytest.raises(ValueError, match="not a Hugging Face URL"):
        helper.parse_huggingface_resolve_url("https://example.com/model.gguf")
