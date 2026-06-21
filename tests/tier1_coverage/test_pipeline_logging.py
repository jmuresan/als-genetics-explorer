import os
import yaml
import duckdb
import pytest
from src.pipeline.run_all import run_pipeline

def test_pipeline_ingestion_logging(tmp_path):
    # Setup test directory
    test_dir = tmp_path / "logging_test"
    test_dir.mkdir()
    
    cache_dir = test_dir / "cache"
    output_dir = test_dir / "outputs"
    db_path = str(test_dir / "als_genetics_logging.duckdb")
    
    # Write a config file
    config_data = {
        "seed_genes": ["SOD1"],
        "api_settings": {
            "offline_mode": False, # Warm cache using mock requests layer
            "cache_dir": str(cache_dir),
            "string_db": {
                "confidence_threshold": 0.7,
                "partner_limit": 10
            },
            "pubmed": {
                "limit_per_gene": 10
            }
        },
        "scoring_weights": {
            "open_targets_association": 0.25,
            "clinvar_pathogenicity": 0.20,
            "string_centrality": 0.15,
            "literature_volume": 0.15
        }
    }
    
    config_path = str(test_dir / "config_test.yaml")
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config_data, f)
        
    # Run the pipeline
    run_pipeline(config_path=config_path, db_path=db_path, output_dir=str(output_dir))
    
    # Check ingestion_log in DuckDB
    conn = duckdb.connect(db_path)
    logs = conn.execute("SELECT source_name, status, record_count, cache_path, error_message FROM ingestion_log").fetchall()
    conn.close()
    
    # Verify we logged attempts for all 6 sources
    logged_sources = {log[0] for log in logs}
    expected_sources = {"uniprot", "reactome", "open_targets", "clinvar", "string", "pubmed"}
    assert expected_sources.issubset(logged_sources)
    
    # Verify status is SUCCESS (since mock requests layer provides valid mock data)
    for source_name, status, record_count, cache_path, error_message in logs:
        assert status in ("SUCCESS", "ZERO_RESULTS")
        assert error_message is None
        assert cache_path is not None
        assert os.path.exists(cache_path)
