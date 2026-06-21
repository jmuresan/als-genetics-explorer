import os
import pytest
import duckdb
import math
from src.db.schema import create_tables
from src.ingest.client import PaperDeduplicator
from src.scoring.gene_score import calculate_scores
from src.scoring.hypothesis_score import score_hypotheses
from src.hypotheses.generator import generate_hypotheses
from src.config import Config

@pytest.fixture
def clean_db(tmp_path):
    db_file = os.path.join(str(tmp_path), "test_adversarial.duckdb")
    conn = duckdb.connect(db_file)
    create_tables(conn)
    conn.close()
    return db_file

def test_adversarial_title_collision_deduplication():
    """
    Challenge: Title collision in PaperDeduplicator.
    If two papers have different PMIDs and DOIs, but their titles normalize to the same string
    (e.g., generic titles like "Editorial" or "Reply"), the deduplicator will treat them
    as the same paper, merging the second into the first and dropping its distinct paper details.
    """
    dedup = PaperDeduplicator()
    
    paper_1 = {
        "pmid": "111111",
        "doi": "10.1000/xyz1",
        "title": "Editorial",
        "abstract": "First editorial",
        "authors": ["Author A"],
        "journal": "Nature",
        "pubdate": "2020-01-01"
    }
    paper_2 = {
        "pmid": "222222",
        "doi": "10.1000/xyz2",
        "title": "Editorial",
        "abstract": "Second editorial",
        "authors": ["Author B"],
        "journal": "Science",
        "pubdate": "2020-02-01"
    }
    
    dedup.add_paper(paper_1, "seed_gene", "SOD1")
    dedup.add_paper(paper_2, "seed_gene", "TARDBP")
    
    unique_papers = dedup.get_unique_papers()
    
    # Due to conflict checks on PMID/DOI, they should not be merged.
    # We expect 2 unique papers.
    assert len(unique_papers) == 2
    pmids = {p["pmid"] for p in unique_papers}
    assert pmids == {"111111", "222222"}

def test_adversarial_evidence_level_nan_injection(clean_db):
    """
    Challenge: Lack of boundary validation on 'evidence_level' string parsing.
    If a claim contains evidence_level = 'NaN' (or 'inf'), the float conversion succeeds,
    propagating NaN/inf through the entire scoring calculation and polluting total_score.
    """
    conn = duckdb.connect(clean_db)
    
    # Populate a single gene
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol) VALUES ('ENSG_SOD1', 'SOD1')")
    # Insert paper
    conn.execute("INSERT INTO papers (pmid, title, abstract) VALUES ('123', 'Paper Title', 'Abstract')")
    # Insert claim with 'NaN' evidence level
    conn.execute("""
    INSERT INTO claims (claim_id, paper_id, subject, predicate, object, evidence_level)
    VALUES ('claim_1', '123', 'SOD1', 'associated_with_disease', 'EFO_0000253', 'NaN')
    """)
    conn.close()
    
    from unittest.mock import MagicMock
    mock_config = MagicMock()
    mock_config.scoring_weights = {
        "open_targets_association": 0.0,
        "clinvar_pathogenicity": 0.0,
        "string_centrality": 0.0,
        "literature_volume": 0.0,
        "citation_quality": 1.0,  # Focus on citation quality weight
        "contradiction_penalty": 0.0
    }
    
    import networkx as nx
    G = nx.MultiDiGraph()
    G.add_node("SOD1", type="gene")
    
    df = calculate_scores(clean_db, G, mock_config)
    assert not df.empty
    
    # The citation quality score and total score should NOT be NaN, but rather fallback to 0.8
    score = df.loc[df["gene_symbol"] == "SOD1", "total_score"].values[0]
    assert not math.isnan(score)
    assert score == 0.8

def test_adversarial_regex_false_positive_gene_set(clean_db):
    """
    Challenge: False-positive regex match for genes sharing names with common English words (e.g. 'SET').
    If a gene is named 'SET', and a low-confidence hypothesis contains the word 'set' (lowercase, as in
    'a set of genes') in its description, the hypothesis conflict check will match 'SET', causing a
    false hypothesis conflict penalty.
    """
    conn = duckdb.connect(clean_db)
    # Insert gene named 'SET'
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol) VALUES ('ENSG_SET', 'SET')")
    # Insert a low-confidence hypothesis containing the common English word 'set' but NOT referring to the gene SET
    conn.execute("""
    INSERT INTO hypotheses (hypothesis_id, title, description, confidence, hypothesis_type)
    VALUES ('HYP-001', 'Mechanism of ALS', 'This hypothesis describes a set of genes and pathways involved in ALS.', 'Low', 'candidate mechanism')
    """)
    conn.close()
    
    from unittest.mock import MagicMock
    mock_config = MagicMock()
    mock_config.scoring_weights = {
        "open_targets_association": 0.0,
        "clinvar_pathogenicity": 0.0,
        "string_centrality": 0.0,
        "literature_volume": 0.0,
        "citation_quality": 0.0,
        "contradiction_penalty": 1.0  # Focus on contradiction penalty
    }
    
    import networkx as nx
    G = nx.MultiDiGraph()
    G.add_node("SET", type="gene")
    
    df = calculate_scores(clean_db, G, mock_config)
    assert not df.empty
    
    row = df.loc[df["gene_symbol"] == "SET"].iloc[0]
    # The penalty should be 0.0 because the search regex is case-sensitive
    assert row["contradiction_penalty"] == 0.0
    assert row["total_score"] == 0.0

def test_adversarial_protective_keyword_mismatch_crash(clean_db, tmp_path):
    """
    Challenge: Mismatched protective keyword checks between hypothesis title/desc and paper text.
    If the hypothesis title contains 'protect' (e.g. 'SOD1 protects neurons'), the label checker sets
    is_protective=True. But if the supporting paper only contains 'protects' or 'protect' in its text,
    since they are not in the strict list of PROTECTIVE_KEYWORDS, has_protective_kw evaluates to False.
    This raises a ValueError, crashing the scoring pipeline instead of handling it gracefully.
    """
    conn = duckdb.connect(clean_db)
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol) VALUES ('ENSG_SOD1', 'SOD1')")
    
    # Insert paper containing 'protects' (NOT in PROTECTIVE_KEYWORDS)
    conn.execute("""
    INSERT INTO papers (pmid, title, abstract, ingestion_reason) 
    VALUES ('111111', 'SOD1 study', 'This study shows how the protein protects motor neurons.', 'seed_gene')
    """)
    
    # Insert hypothesis containing 'protects' or 'protection' or 'protective' in its title
    conn.execute("""
    INSERT INTO hypotheses (hypothesis_id, title, description, confidence, hypothesis_type)
    VALUES ('HYP-001', 'SOD1 protects motor neurons', 'Description', 'Medium', 'candidate mechanism')
    """)
    conn.execute("INSERT INTO hypothesis_evidence (hypothesis_id, pmid) VALUES ('HYP-001', '111111')")
    conn.close()
    
    output_md = os.path.join(str(tmp_path), "hypotheses.md")
    
    # It should not raise ValueError, but downgrade the hypothesis type and confidence
    score_hypotheses(clean_db, output_md=output_md)
    
    conn = duckdb.connect(clean_db)
    hyp = conn.execute("SELECT hypothesis_type, confidence FROM hypotheses WHERE hypothesis_id = 'HYP-001'").fetchone()
    conn.close()
    assert hyp[0] == "candidate mechanism"
    assert hyp[1] == "Low"

def test_adversarial_empty_papers_generator_delete(clean_db, tmp_path):
    """
    Challenge: Database deletion without repopulation on empty papers.
    In generate_hypotheses, the function first deletes all hypotheses from the database.
    If the database contains no papers, the function returns early without raising an error
    but leaving the hypotheses tables completely empty.
    """
    conn = duckdb.connect(clean_db)
    # Insert a hypothesis manually
    conn.execute("INSERT INTO hypotheses (hypothesis_id, title, description, confidence, hypothesis_type) VALUES ('HYP-001', 'Pre-existing', 'Desc', 'Low', 'candidate mechanism')")
    # Verify it exists
    assert conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0] == 1
    conn.close()
    
    output_md = os.path.join(str(tmp_path), "hypotheses.md")
    
    # Run the generator with no papers in DB
    generate_hypotheses(clean_db, output_md)
    
    conn = duckdb.connect(clean_db)
    # The pre-existing hypothesis should not be deleted (generator returns early before deleting tables)
    assert conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0] == 1
    conn.close()
