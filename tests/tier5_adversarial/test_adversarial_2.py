import os
import pytest
import duckdb
import networkx as nx
from src.db.schema import create_tables
from src.db.populate import insert_or_merge_paper
from src.graph.build_graph import build_graph
from src.scoring.gene_score import calculate_scores
from src.scoring.hypothesis_score import score_hypotheses
from src.hypotheses.generator import generate_hypotheses
from src.config import Config

@pytest.fixture
def temp_db(tmp_path):
    db_path = os.path.join(tmp_path, "test.duckdb")
    conn = duckdb.connect(db_path)
    create_tables(conn)
    conn.close()
    return db_path

def test_deduplication_pmid_mismatch_reference_integrity(temp_db, tmp_path):
    """
    Gap 1: Paper Deduplication Discards PMIDs Reference Integrity Violation.
    If two papers with different PMIDs but same title/DOI are merged, one PMID is discarded.
    But claims may still reference the discarded PMID, leading to ValueError in hypothesis evaluation.
    """
    conn = duckdb.connect(temp_db)
    
    # 1. Insert a claim referencing PMID "22222" (the paper that will be merged/discarded)
    conn.execute("""
    INSERT INTO claims (claim_id, paper_id, subject, predicate, object, evidence_level)
    VALUES ('claim_reactome_SOD1_R-HSA-9711_22222', '22222', 'SOD1', 'associated_with_pathway', 'R-HSA-9711', 'curated')
    """)
    
    # 2. Insert duplicate papers: they share the same DOI/title, but have different PMIDs (11111 vs 22222).
    # Since 22222 has the same title/DOI as 11111, insert_or_merge_paper will resolve them to canonical PMID 11111.
    insert_or_merge_paper(conn, "11111", "10.1001/als", "ALS Study", "Abstract for ALS Study", "2020-01-01", "seed_gene")
    insert_or_merge_paper(conn, "22222", "10.1001/als", "ALS Study", "Abstract for ALS Study", "2020-01-01", "seed_gene")
    
    # 3. Seed other required data for the hypothesis generator to run
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol) VALUES ('ENSG_SOD1', 'SOD1')")
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol) VALUES ('ENSG_CCS', 'CCS')")
    conn.execute("INSERT INTO pathways (pathway_id, pathway_name) VALUES ('R-HSA-9711', 'Amyotrophic lateral sclerosis (ALS)')")
    conn.execute("INSERT INTO gene_pathways (gene_symbol, pathway_id) VALUES ('SOD1', 'R-HSA-9711')")
    conn.execute("INSERT INTO gene_pathways (gene_symbol, pathway_id) VALUES ('CCS', 'R-HSA-9711')")
    
    # Insert disease associations so both genes are in allowed_genes (since score >= 0.5)
    conn.execute("INSERT INTO disease_associations (gene_symbol, disease_id, disease_name, score) VALUES ('SOD1', 'EFO_0000253', 'ALS', 1.0)")
    conn.execute("INSERT INTO disease_associations (gene_symbol, disease_id, disease_name, score) VALUES ('CCS', 'EFO_0000253', 'ALS', 1.0)")
    conn.close()
    
    # 4. Run hypothesis generator. It will construct a convergence hypothesis and use PMID '22222' because of the claim.
    output_md = os.path.join(tmp_path, "hypotheses.md")
    
    # We expect that generating hypotheses or scoring them will not crash but succeed,
    # updating claims referencing PMID 22222 to point to canonical PMID 11111.
    generate_hypotheses(temp_db, output_md)
    
    conn = duckdb.connect(temp_db)
    claims_count_22222 = conn.execute("SELECT COUNT(*) FROM claims WHERE paper_id = '22222'").fetchone()[0]
    claims_count_11111 = conn.execute("SELECT COUNT(*) FROM claims WHERE paper_id = '11111'").fetchone()[0]
    conn.close()
    
    assert claims_count_22222 == 0
    assert claims_count_11111 == 1

def test_pathway_name_protect_substring_crashes_generator(temp_db, tmp_path):
    """
    Gap 2: Pathway Name Substring 'protect' Triggers Invalid Protective Hypothesis Crash.
    If a pathway name contains 'protect', it sets is_protective_claim = True.
    If papers do not contain protective keywords, it raises a ValueError and crashes the pipeline.
    """
    conn = duckdb.connect(temp_db)
    
    # 1. Create a pathway containing 'Protection'
    conn.execute("INSERT INTO pathways (pathway_id, pathway_name) VALUES ('R-HSA-1234', 'Protection from oxidative stress')")
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol) VALUES ('ENSG_SOD1', 'SOD1')")
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol) VALUES ('ENSG_CCS', 'CCS')")
    conn.execute("INSERT INTO gene_pathways (gene_symbol, pathway_id) VALUES ('SOD1', 'R-HSA-1234')")
    conn.execute("INSERT INTO gene_pathways (gene_symbol, pathway_id) VALUES ('CCS', 'R-HSA-1234')")
    
    # Insert disease associations so both genes are in allowed_genes
    conn.execute("INSERT INTO disease_associations (gene_symbol, disease_id, disease_name, score) VALUES ('SOD1', 'EFO_0000253', 'ALS', 1.0)")
    conn.execute("INSERT INTO disease_associations (gene_symbol, disease_id, disease_name, score) VALUES ('CCS', 'EFO_0000253', 'ALS', 1.0)")
    
    # 2. Add a claim and a paper (but paper does NOT have any protective keywords like 'delayed onset' etc.)
    conn.execute("INSERT INTO papers (pmid, title, abstract) VALUES ('31567890', 'SOD1 study', 'A normal study of SOD1 in ALS without keywords.')")
    conn.execute("""
    INSERT INTO claims (claim_id, paper_id, subject, predicate, object, evidence_level)
    VALUES ('claim_reactome_SOD1_R-HSA-1234_31567890', '31567890', 'SOD1', 'associated_with_pathway', 'R-HSA-1234', 'curated')
    """)
    conn.close()
    
    output_md = os.path.join(tmp_path, "hypotheses.md")
    
    # Verify that the pipeline does not raise ValueError but downgrades hypothesis to candidate mechanism and Low confidence
    generate_hypotheses(temp_db, output_md)
    
    conn = duckdb.connect(temp_db)
    hyps = conn.execute("SELECT hypothesis_type, confidence FROM hypotheses").fetchall()
    conn.close()
    
    assert len(hyps) > 0
    for h_type, confidence in hyps:
        assert h_type == 'candidate mechanism'
        assert confidence == 'Low'

def test_clinvar_conflicting_pathogenicity_misclassified(temp_db):
    """
    Gap 3: ClinVar Pathogenicity Scoring Misclassifies 'Conflicting' Interpretations as Pathogenic.
    'conflicting interpretations of pathogenicity' is treated as fully pathogenic and receives zero conflict penalty.
    """
    conn = duckdb.connect(temp_db)
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol) VALUES ('ENSG_SOD1', 'SOD1')")
    # Insert variant with 'conflicting interpretations' clinical significance
    conn.execute("""
    INSERT INTO variants (variant_id, gene_symbol, clinical_significance, disease_name)
    VALUES ('VC1', 'SOD1', 'conflicting interpretations of pathogenicity', 'Amyotrophic lateral sclerosis')
    """)
    conn.close()
    
    # Create empty graph
    G = nx.MultiDiGraph()
    G.add_node("SOD1", type="gene")
    
    # Create config with default scoring weights
    cfg = Config()
    
    # Calculate scores
    df = calculate_scores(temp_db, G, cfg)
    
    # Check SOD1's scores
    sod1_row = df[df["gene_symbol"] == "SOD1"].iloc[0]
    
    # Since clinical significance is conflicting, clinvar_score is counted as 0.0
    assert sod1_row["clinvar_score"] == 0.0
    
    # And contradiction_penalty is 0.0 because it does not contain 'benign', 'vus', or 'uncertain'
    assert sod1_row["contradiction_penalty"] == 0.0

def test_empty_database_build_graph_raises_catalog_exception(tmp_path):
    """
    Gap 4: Streamlit Dashboard Graph Loading on Missing/Uninitialized Database CatalogException.
    If database lacks tables, build_graph raises duckdb.CatalogException.
    """
    db_path = os.path.join(tmp_path, "uninitialized.duckdb")
    
    # We connect to database but do NOT create any tables
    conn = duckdb.connect(db_path)
    conn.close()
    
    # Calling build_graph should not raise duckdb.CatalogException but return an empty MultiDiGraph instead of crashing
    G = build_graph(db_path)
    assert isinstance(G, nx.MultiDiGraph)
    assert len(G.nodes) == 0
