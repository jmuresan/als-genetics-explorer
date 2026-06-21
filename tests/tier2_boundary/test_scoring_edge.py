import os
import pytest
import duckdb
import networkx as nx
import pandas as pd
from src.db.schema import create_tables
from src.config import Config
from src.scoring.gene_score import calculate_scores

@pytest.fixture
def base_db(tmp_path):
    db_path = os.path.join(tmp_path, "scoring_test.duckdb")
    conn = duckdb.connect(db_path)
    create_tables(conn)
    
    # Standard seed genes
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol) VALUES ('ENSG_SOD1', 'SOD1')")
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol) VALUES ('ENSG_C9orf72', 'C9orf72')")
    
    conn.close()
    return db_path

def test_scoring_zero_values(base_db):
    # 1. Scoring engine handles zero values for all components.
    G = nx.MultiDiGraph()
    config = Config()
    
    df = calculate_scores(base_db, G, config)
    assert len(df) == 2
    assert df.loc[df["gene_symbol"] == "SOD1", "total_score"].values[0] == 0.0
    assert df.loc[df["gene_symbol"] == "C9orf72", "total_score"].values[0] == 0.0

def test_scoring_weights_sum_zero(base_db):
    # 2. Config weights sum to 0.0 (assert calculation handles normalization dynamically).
    G = nx.MultiDiGraph()
    config = Config()
    config.scoring_weights = {
        "open_targets_association": 0.0,
        "clinvar_pathogenicity": 0.0
    }
    
    df = calculate_scores(base_db, G, config)
    assert len(df) == 2

def test_scoring_negative_values(base_db):
    # 3. Input database contains negative values (e.g., negative centrality scores or penalties).
    conn = duckdb.connect(base_db)
    conn.execute("INSERT INTO disease_associations (gene_symbol, disease_id, score) VALUES ('SOD1', 'EFO_0000253', -0.5)")
    conn.close()
    
    G = nx.MultiDiGraph()
    config = Config()
    
    df = calculate_scores(base_db, G, config)
    sod1_score = df.loc[df["gene_symbol"] == "SOD1", "total_score"].values[0]
    expected = -0.5 * config.scoring_weights["open_targets_association"]
    assert sod1_score == pytest.approx(expected)

def test_scoring_missing_null_subscores(base_db):
    # 4. Handle missing/null subscores (subscore defaults to 0.0, other scores calculate).
    conn = duckdb.connect(base_db)
    conn.execute("INSERT INTO disease_associations (gene_symbol, disease_id, score) VALUES ('C9orf72', 'EFO_0000253', NULL)")
    conn.close()
    
    G = nx.MultiDiGraph()
    config = Config()
    
    df = calculate_scores(base_db, G, config)
    c9_row = df[df["gene_symbol"] == "C9orf72"].iloc[0]
    assert c9_row["open_targets_score"] == 0.0
    assert c9_row["total_score"] == 0.0

def test_scoring_tie_resolution(base_db):
    # 5. Verify outputs when ranking is a tie (ranks resolved alphabetically by gene symbol).
    G = nx.MultiDiGraph()
    config = Config()
    
    df = calculate_scores(base_db, G, config)
    # C9orf72 is alphabetically first, so it must be rank 1, and SOD1 must be rank 2
    assert df.loc[0, "gene_symbol"] == "C9orf72"
    assert df.loc[0, "rank"] == 1
    assert df.loc[1, "gene_symbol"] == "SOD1"
    assert df.loc[1, "rank"] == 2
