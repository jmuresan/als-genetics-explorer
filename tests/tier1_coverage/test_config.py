import os
import pytest
from src.config import Config

def test_load_valid_default_config():
    # 1. Load valid default config.yaml successfully.
    config = Config()
    assert config is not None
    assert isinstance(config.seed_genes, list)
    assert len(config.seed_genes) > 0

def test_parse_scoring_weights():
    # 2. Parse scoring weights as floats.
    config = Config()
    assert isinstance(config.scoring_weights, dict)
    for k, v in config.scoring_weights.items():
        assert isinstance(v, float)
    assert "open_targets_association" in config.scoring_weights

def test_load_string_confidence():
    # 3. Load and parse the STRING confidence threshold (default 0.7).
    config = Config()
    assert isinstance(config.string_confidence_threshold, float)
    assert config.string_confidence_threshold == 0.7

def test_load_pubmed_limits_and_genes():
    # 4. Load PubMed search limits and gene target lists correctly.
    config = Config()
    assert isinstance(config.pubmed_limit_per_gene, int)
    assert config.pubmed_limit_per_gene > 0
    assert "C9orf72" in config.seed_genes

def test_resolve_default_paths():
    # 5. Resolve default paths for caching, DuckDB, and outputs.
    config = Config()
    assert config.cache_dir is not None
    assert os.path.isabs(config.cache_dir)
