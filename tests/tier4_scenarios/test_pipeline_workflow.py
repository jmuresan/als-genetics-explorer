import os
import sys
import subprocess
import yaml
import duckdb
import pytest
import pandas as pd
import xml.etree.ElementTree as ET
from src.pipeline.run_all import run_pipeline

def test_programmatic_and_cli_pipeline_workflow(tmp_path):
    # 1. Setup temporary directories and config file
    test_dir = tmp_path / "pipeline_test"
    test_dir.mkdir()
    
    cache_dir = test_dir / "cache"
    output_dir = test_dir / "outputs"
    db_path = str(test_dir / "als_genetics_test.duckdb")
    
    config_data = {
        "seed_genes": ["SOD1"],
        "api_settings": {
            "offline_mode": False,  # Programmatic run inside pytest will warm the cache using the mock layer
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
        
    # 2. Run E2E pipeline programmatically
    results = run_pipeline(config_path=config_path, db_path=db_path, output_dir=str(output_dir))
    
    # 3. Verify programmatic outputs
    assert os.path.exists(db_path)
    assert os.path.exists(results["graphml_path"])
    assert os.path.exists(results["ranked_genes_csv"])
    assert os.path.exists(results["ranked_pathways_csv"])
    assert os.path.exists(results["hypotheses_md"])
    
    # Verify DuckDB contents
    conn = duckdb.connect(db_path)
    tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
    for t in ["genes", "variants", "disease_associations", "pathways", "gene_pathways", "interactions", "papers", "hypotheses", "hypothesis_evidence", "drugs", "gene_drugs"]:
        assert t in tables
        
    # Check that data is inserted
    assert conn.execute("SELECT COUNT(*) FROM genes").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0] >= 3
    conn.close()
    
    # Verify GraphML XML structure
    try:
        tree = ET.parse(results["graphml_path"])
        root = tree.getroot()
        assert root is not None
        # GraphML namespace
        assert "graphml" in root.tag
    except ET.ParseError as e:
        pytest.fail(f"GraphML is not valid XML: {e}")
        
    # Verify Ranked Genes CSV structure
    df_genes = pd.read_csv(results["ranked_genes_csv"])
    assert "gene_symbol" in df_genes.columns
    assert "total_score" in df_genes.columns
    assert "rank" in df_genes.columns
    assert not df_genes.empty
    
    # Verify Hypotheses Markdown
    with open(results["hypotheses_md"], "r", encoding="utf-8") as f:
        md_content = f.read()
    assert "Generated Hypotheses" in md_content
    assert "HYP-001" in md_content
    
    # 4. Now modify config to use offline_mode: True for subprocess CLI execution
    config_data["api_settings"]["offline_mode"] = True
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config_data, f)
        
    # Define paths for CLI output
    cli_db_path = str(test_dir / "als_genetics_cli.duckdb")
    cli_output_dir = test_dir / "cli_outputs"
    
    # Test CLI entrypoint 1: Pipeline E2E run_all.py
    cmd_pipeline = [
        sys.executable, "-m", "src.pipeline.run_all",
        "--config", config_path,
        "--db", cli_db_path,
        "--output-dir", str(cli_output_dir)
    ]
    res_pipeline = subprocess.run(cmd_pipeline, capture_output=True, text=True)
    assert res_pipeline.returncode == 0, f"Pipeline CLI failed: {res_pipeline.stderr}"
    
    # Check files created by pipeline CLI
    assert os.path.exists(cli_db_path)
    assert os.path.exists(cli_output_dir / "als_knowledge_graph.graphml")
    assert os.path.exists(cli_output_dir / "ranked_genes.csv")
    assert os.path.exists(cli_output_dir / "ranked_pathways.csv")
    assert os.path.exists(cli_output_dir / "hypotheses.md")
    
    # Test CLI entrypoint 2: build_graph.py
    cli_graphml_path = str(cli_output_dir / "another_knowledge_graph.graphml")
    cmd_graph = [
        sys.executable, "-m", "src.graph.build_graph",
        "--db", cli_db_path,
        "--output", cli_graphml_path
    ]
    res_graph = subprocess.run(cmd_graph, capture_output=True, text=True)
    assert res_graph.returncode == 0, f"build_graph CLI failed: {res_graph.stderr}"
    assert os.path.exists(cli_graphml_path)
    
    # Test CLI entrypoint 3: gene_score.py
    cli_score_dir = cli_output_dir / "scoring_output"
    cmd_score = [
        sys.executable, "-m", "src.scoring.gene_score",
        "--db", cli_db_path,
        "--config", config_path,
        "--output-dir", str(cli_score_dir)
    ]
    res_score = subprocess.run(cmd_score, capture_output=True, text=True)
    assert res_score.returncode == 0, f"gene_score CLI failed: {res_score.stderr}"
    assert os.path.exists(cli_score_dir / "ranked_genes.csv")
    assert os.path.exists(cli_score_dir / "ranked_pathways.csv")
    
    # Test CLI entrypoint 4: hypotheses generator.py (represented as hypothesis_score/generation)
    cli_hyp_md_path = str(cli_output_dir / "another_hypotheses.md")
    cmd_hyp = [
        sys.executable, "-m", "src.hypotheses.generator",
        "--db", cli_db_path,
        "--output", cli_hyp_md_path,
        "--config", config_path
    ]
    res_hyp = subprocess.run(cmd_hyp, capture_output=True, text=True)
    assert res_hyp.returncode == 0, f"hypotheses generator CLI failed: {res_hyp.stderr}"
    assert os.path.exists(cli_hyp_md_path)


def test_ingestion_cli_subprocess(tmp_path):
    import json
    # 1. Setup temporary directories and config file
    test_dir = tmp_path / "ingest_test"
    test_dir.mkdir()
    
    cache_dir = test_dir / "cache"
    
    config_data = {
        "seed_genes": ["SOD1"],
        "api_settings": {
            "offline_mode": False,
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
        
    # Run ingestion CLI via subprocess
    cmd = [
        sys.executable, "-m", "src.ingest.run_all",
        "--config", config_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"Ingestion CLI failed: {result.stderr}\nStdout: {result.stdout}"
    
    # Check cache entries exist in the cache directory
    assert os.path.exists(cache_dir)
    cache_files = os.listdir(cache_dir)
    assert len(cache_files) > 0, "No cache files written"
    
    # Check deduplicated JSON exists
    dedup_path = os.path.join(str(test_dir), "data", "processed", "deduplicated_papers.json")
    assert os.path.exists(dedup_path), f"Deduplicated papers JSON not found at {dedup_path}"
    
    with open(dedup_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, list)

