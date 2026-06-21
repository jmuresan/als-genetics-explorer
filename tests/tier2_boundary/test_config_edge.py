import os
import yaml
import pytest
import logging
from src.config import Config

def test_missing_config_yaml_fallback(tmp_path, caplog):
    # 1. Missing config.yaml redirects to default fallbacks and logs a warning.
    missing_path = os.path.join(tmp_path, "missing_config.yaml")
    with caplog.at_level(logging.WARNING):
        config = Config(config_path=missing_path)
        assert config.seed_genes == []
        assert config.offline_mode is False
        assert config.string_confidence_threshold == 0.7
        assert config.string_partner_limit == 10
        assert config.pubmed_limit_per_gene == 10
        assert "open_targets_association" in config.scoring_weights
        assert any("Config file not found" in record.message for record in caplog.records)

def test_config_weights_normalization(tmp_path):
    # 2. Configuration weights summing to values not equal to 1.0 (verify normalized handling).
    config_data = {
        "scoring_weights": {
            "open_targets_association": 2.0,
            "clinvar_pathogenicity": 3.0
        }
    }
    config_file = os.path.join(tmp_path, "config.yaml")
    with open(config_file, "w") as f:
        yaml.dump(config_data, f)
    
    config = Config(config_path=config_file)
    assert config.scoring_weights["open_targets_association"] == pytest.approx(0.4)
    assert config.scoring_weights["clinvar_pathogenicity"] == pytest.approx(0.6)

def test_config_invalid_weights(tmp_path):
    # 3. Negative values or non-numeric types for scoring weights (assert configuration validation error).
    config_file = os.path.join(tmp_path, "config_neg.yaml")
    
    # Negative weight
    with open(config_file, "w") as f:
        yaml.dump({"scoring_weights": {"weight_a": -0.5}}, f)
    with pytest.raises(ValueError, match="cannot be negative"):
        Config(config_path=config_file)
        
    # Non-numeric weight
    with open(config_file, "w") as f:
        yaml.dump({"scoring_weights": {"weight_a": "invalid"}}, f)
    with pytest.raises(TypeError, match="must be numeric"):
        Config(config_path=config_file)

def test_config_out_of_bounds_limits(tmp_path):
    # 4. Out-of-bounds limits (e.g., STRING limit <= 0 or > 1000).
    config_file = os.path.join(tmp_path, "config_limits.yaml")
    
    # STRING limit <= 0
    with open(config_file, "w") as f:
        yaml.dump({"api_settings": {"string_db": {"partner_limit": 0}}}, f)
    with pytest.raises(ValueError, match="STRING partner limit must be between"):
        Config(config_path=config_file)
        
    # STRING limit > 1000
    with open(config_file, "w") as f:
        yaml.dump({"api_settings": {"string_db": {"partner_limit": 1001}}}, f)
    with pytest.raises(ValueError, match="STRING partner limit must be between"):
        Config(config_path=config_file)

def test_config_malformed_yaml(tmp_path):
    # 5. Malformed YAML file syntax (must raise a clear parsing exception).
    config_file = os.path.join(tmp_path, "config_malformed.yaml")
    with open(config_file, "w") as f:
        f.write("api_settings:\n  string_db:\n    partner_limit: : invalid")
        
    with pytest.raises(yaml.YAMLError):
        Config(config_path=config_file)
