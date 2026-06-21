import os
import pytest
import duckdb
import pandas as pd
from src.db.schema import create_tables
from src.db.populate import (
    populate_uniprot,
    populate_clinvar,
    populate_opentargets,
    populate_pubmed
)
from src.config import Config
from src.graph.build_graph import build_graph
from src.scoring.gene_score import (
    calculate_scores,
    calculate_pathway_scores,
    export_scores
)

@pytest.fixture
def scoring_db(tmp_path):
    db_file = os.path.join(str(tmp_path), "test.duckdb")
    conn = duckdb.connect(db_file)
    create_tables(conn)
    
    # Gene SOD1
    populate_uniprot(conn, {
        "results": [
            {
                "primaryAccession": "P00441",
                "uniProtkbId": "SODC_HUMAN",
                "genes": [{"geneName": {"value": "SOD1"}}],
                "proteinDescription": {"recommendedName": {"fullName": {"value": "Superoxide dismutase"}}}
            }
        ]
    })
    
    # Pathogenic variant
    populate_clinvar(conn, "SOD1", {
        "result": {
            "8877": {
                "uid": "8877",
                "clinical_significance": {"description": "Pathogenic"},
                "trait_set": [{"trait_name": "Amyotrophic lateral sclerosis"}]
            }
        }
    })
    
    # Open Targets association score 0.85
    populate_opentargets(conn, {
        "data": {
            "target": {
                "approvedSymbol": "SOD1",
                "associatedDiseases": {
                    "rows": [
                        {
                            "disease": {"id": "EFO_0000253", "name": "amyotrophic lateral sclerosis"},
                            "score": 0.85
                        }
                    ]
                }
            }
        }
    })
    
    # Pathway mapping
    conn.execute("INSERT INTO pathways VALUES ('R-HSA-9711123', 'Amyotrophic lateral sclerosis (ALS)')")
    conn.execute("INSERT INTO gene_pathways VALUES ('SOD1', 'R-HSA-9711123')")
    
    # Paper
    populate_pubmed(conn, {
        "result": {
            "31567890": {
                "uid": "31567890",
                "title": "SOD1 study",
                "articleids": [],
                "sortpubdate": "2019-10-01"
            }
        }
    }, "seed_gene")
    
    conn.close()
    return db_file

def test_calculate_open_targets_score(scoring_db):
    # 1. Calculate Open Targets disease association score using config weights.
    config = Config()
    G = build_graph(scoring_db)
    df = calculate_scores(scoring_db, G, config)
    
    sod1_row = df[df["gene_symbol"] == "SOD1"]
    assert not sod1_row.empty
    assert sod1_row.iloc[0]["open_targets_score"] == 0.85

def test_calculate_clinvar_score(scoring_db):
    # 2. Add ClinVar pathogenicity support to target gene score.
    config = Config()
    G = build_graph(scoring_db)
    df = calculate_scores(scoring_db, G, config)
    
    sod1_row = df[df["gene_symbol"] == "SOD1"]
    assert not sod1_row.empty
    assert sod1_row.iloc[0]["clinvar_score"] == 0.5

def test_calculate_centrality_score(scoring_db):
    # 3. Calculate network centrality (degree/betweenness) from NetworkX and scale appropriately.
    config = Config()
    G = build_graph(scoring_db)
    df = calculate_scores(scoring_db, G, config)
    
    sod1_row = df[df["gene_symbol"] == "SOD1"]
    assert not sod1_row.empty
    assert "centrality_score" in sod1_row.columns
    assert isinstance(sod1_row.iloc[0]["centrality_score"], float)

def test_export_ranked_genes_csv(scoring_db, tmp_path):
    # 4. Write explainable scoring outputs to outputs/ranked_genes.csv containing all subscores.
    config = Config()
    G = build_graph(scoring_db)
    gene_df = calculate_scores(scoring_db, G, config)
    pathway_df = calculate_pathway_scores(scoring_db, gene_df)
    
    output_dir = os.path.join(str(tmp_path), "outputs")
    export_scores(gene_df, pathway_df, output_dir)
    
    genes_csv = os.path.join(output_dir, "ranked_genes.csv")
    assert os.path.exists(genes_csv)
    df_loaded = pd.read_csv(genes_csv)
    assert "gene_symbol" in df_loaded.columns
    assert "open_targets_score" in df_loaded.columns
    assert "total_score" in df_loaded.columns
    assert "rank" in df_loaded.columns

def test_export_ranked_pathways_csv(scoring_db, tmp_path):
    # 5. Write pathway scores and ranks to outputs/ranked_pathways.csv.
    config = Config()
    G = build_graph(scoring_db)
    gene_df = calculate_scores(scoring_db, G, config)
    pathway_df = calculate_pathway_scores(scoring_db, gene_df)
    
    output_dir = os.path.join(str(tmp_path), "outputs")
    export_scores(gene_df, pathway_df, output_dir)
    
    pathways_csv = os.path.join(output_dir, "ranked_pathways.csv")
    assert os.path.exists(pathways_csv)
    df_loaded = pd.read_csv(pathways_csv)
    assert "pathway_id" in df_loaded.columns
    assert "score" in df_loaded.columns
    assert "rank" in df_loaded.columns

def test_contradiction_penalty_and_likely_pathogenic(tmp_path):
    db_file = os.path.join(str(tmp_path), "test_cp.duckdb")
    conn = duckdb.connect(db_file)
    create_tables(conn)
    
    # 1. Populate UniProt gene
    populate_uniprot(conn, {
        "results": [
            {
                "primaryAccession": "P00441",
                "uniProtkbId": "SODC_HUMAN",
                "genes": [{"geneName": {"value": "SOD1"}}],
                "proteinDescription": {"recommendedName": {"fullName": {"value": "Superoxide dismutase"}}}
            }
        ]
    })
    
    # 2. Populate 3 ClinVar variants: 1 Likely pathogenic, 1 Benign, 1 Uncertain significance
    populate_clinvar(conn, "SOD1", {
        "result": {
            "1001": {
                "uid": "1001",
                "clinical_significance": {"description": "Likely pathogenic"},
                "trait_set": [{"trait_name": "Amyotrophic lateral sclerosis"}]
            },
            "1002": {
                "uid": "1002",
                "clinical_significance": {"description": "Benign"},
                "trait_set": []
            },
            "1003": {
                "uid": "1003",
                "clinical_significance": {"description": "Uncertain significance"},
                "trait_set": []
            }
        }
    })
    
    # 3. Add a low confidence hypothesis containing "SOD1"
    conn.execute("INSERT INTO hypotheses (hypothesis_id, title, description, confidence, hypothesis_type) VALUES ('H_low', 'Low SOD1 hypothesis', 'Hypothesis containing SOD1 symbol', 'Low', 'candidate mechanism')")
    
    conn.close()
    
    config = Config()
    G = build_graph(db_file)
    df = calculate_scores(db_file, G, config)
    
    sod1_row = df[df["gene_symbol"] == "SOD1"].iloc[0]
    # cv_score should be min(1 * 0.5, 1.0) = 0.5
    assert sod1_row["clinvar_score"] == 0.5
    # variant conflict = 2 (Benign + Uncertain significance) / 3 (total variants) = 2/3 ≈ 0.6667
    # hypothesis conflict = 1 (Low confidence) / 1 (total hypotheses) = 1.0
    # contradiction_penalty = variant_conflict + hypothesis_conflict = 2/3 + 1.0 = 5/3 ≈ 1.6667
    assert sod1_row["contradiction_penalty"] == pytest.approx(5.0 / 3.0)

def test_calculate_druggability_score(tmp_path):
    db_file = os.path.join(str(tmp_path), "test_druggability.duckdb")
    conn = duckdb.connect(db_file)
    create_tables(conn)
    
    # 1. Populate UniProt gene
    populate_uniprot(conn, {
        "results": [
            {
                "primaryAccession": "P00441",
                "uniProtkbId": "SODC_HUMAN",
                "genes": [{"geneName": {"value": "SOD1"}}],
                "proteinDescription": {"recommendedName": {"fullName": {"value": "Superoxide dismutase"}}}
            }
        ]
    })
    
    # 2. Populate drug with max_clinical_phase = 3.0
    conn.execute("INSERT INTO drugs (drug_id, name, mechanism_of_action, max_clinical_phase) VALUES ('CHEMBL999', 'Riluzole', 'Inhibitor', 3.0)")
    conn.execute("INSERT INTO gene_drugs (gene_symbol, drug_id) VALUES ('SOD1', 'CHEMBL999')")
    
    conn.close()
    
    config = Config()
    config.scoring_weights = {
        "open_targets_association": 0.0,
        "clinvar_pathogenicity": 0.0,
        "druggability": 1.0
    }
    G = build_graph(db_file)
    df = calculate_scores(db_file, G, config)
    
    sod1_row = df[df["gene_symbol"] == "SOD1"].iloc[0]
    # Max phase is 3.0, mapping should be 0.75
    assert sod1_row["druggability_score"] == 0.75
    # Total score should be 0.75 since druggability weight is 1.0 (other weights are 0.0)
    assert sod1_row["total_score"] == 0.75

