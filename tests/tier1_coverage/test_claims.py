import pytest
import duckdb
from src.db.schema import create_tables
from src.db.populate import (
    populate_string,
    populate_reactome,
    populate_clinvar,
    populate_opentargets
)

@pytest.fixture
def db_conn():
    conn = duckdb.connect(":memory:")
    create_tables(conn)
    yield conn
    conn.close()

def test_string_interaction_claims(db_conn):
    # Test that STRING interaction inserts sorted genes and generates claim linked to 'not_found'
    data = [
        {"preferredName_A": "SOD1", "preferredName_B": "CCS", "score": 0.999}
    ]
    populate_string(db_conn, data)
    
    # Check interaction sorted alphabetically
    interaction = db_conn.execute("SELECT gene_a, gene_b, confidence_score FROM interactions").fetchone()
    assert interaction == ("CCS", "SOD1", 0.999)
    
    # Check claim generated
    claim = db_conn.execute("SELECT claim_id, paper_id, subject, predicate, object, evidence_level FROM claims").fetchone()
    assert claim is not None
    assert claim[0] == "claim_string_CCS_SOD1"
    assert claim[1] == "not_found"
    assert claim[2] == "CCS"
    assert claim[3] == "interacts_with"
    assert claim[4] == "SOD1"
    assert claim[5] == "0.999"

def test_reactome_claims_with_pmids(db_conn):
    # Test Reactome populates pathway claims when PMIDs are present
    data = [
        {
            "stId": "R-HSA-9711123",
            "displayName": "Amyotrophic lateral sclerosis (ALS)",
            "literatureReference": [{"pubId": "31567890", "title": "Reactome paper"}]
        }
    ]
    populate_reactome(db_conn, "SOD1", data)
    
    claim = db_conn.execute("SELECT claim_id, paper_id, subject, predicate, object, evidence_level FROM claims").fetchone()
    assert claim is not None
    assert claim[0] == "claim_reactome_SOD1_R-HSA-9711123_31567890"
    assert claim[1] == "31567890"
    assert claim[2] == "SOD1"
    assert claim[3] == "associated_with_pathway"
    assert claim[4] == "R-HSA-9711123"
    assert claim[5] == "curated"

def test_reactome_claims_fallback(db_conn):
    # Test Reactome populates claim linked to 'not_found' when no PMIDs are found
    data = [
        {
            "stId": "R-HSA-9711123",
            "displayName": "Amyotrophic lateral sclerosis (ALS)"
        }
    ]
    populate_reactome(db_conn, "SOD1", data)
    
    claim = db_conn.execute("SELECT claim_id, paper_id, subject, predicate, object, evidence_level FROM claims").fetchone()
    assert claim is not None
    assert claim[0] == "claim_reactome_SOD1_R-HSA-9711123_not_found"
    assert claim[1] == "not_found"
    assert claim[2] == "SOD1"
    assert claim[3] == "associated_with_pathway"
    assert claim[4] == "R-HSA-9711123"

def test_clinvar_claims_with_pmids(db_conn):
    # Test ClinVar variant claims with PMIDs
    data = {
        "result": {
            "8877": {
                "uid": "8877",
                "clinical_significance": {"description": "Pathogenic"},
                "trait_set": [{"trait_name": "Amyotrophic lateral sclerosis"}],
                "pubmedid": "1234567"
            }
        }
    }
    populate_clinvar(db_conn, "SOD1", data)
    
    claim = db_conn.execute("SELECT claim_id, paper_id, subject, predicate, object, evidence_level FROM claims").fetchone()
    assert claim is not None
    assert claim[0] == "claim_clinvar_8877_1234567"
    assert claim[1] == "1234567"
    assert claim[2] == "8877"
    assert claim[3] == "associated_with_gene"
    assert claim[4] == "SOD1"
    assert claim[5] == "Pathogenic"

def test_opentargets_claims_with_pmids(db_conn):
    # Test Open Targets claims with PMIDs
    data = {
        "symbol": "SOD1",
        "ensembl_id": "ENSG00000091409",
        "association": {
            "approvedSymbol": "SOD1",
            "associatedDiseases": {
                "rows": [
                    {
                        "disease": {"id": "EFO_0000253", "name": "amyotrophic lateral sclerosis"},
                        "score": 0.85
                    }
                ]
            }
        },
        "evidences": [
            {
                "literature": ["31567890"]
            }
        ]
    }
    populate_opentargets(db_conn, data)
    
    claim = db_conn.execute("SELECT claim_id, paper_id, subject, predicate, object, evidence_level FROM claims").fetchone()
    assert claim is not None
    assert claim[0] == "claim_opentargets_SOD1_EFO_0000253_31567890"
    assert claim[1] == "31567890"
    assert claim[2] == "SOD1"
    assert claim[3] == "associated_with_disease"
    assert claim[4] == "EFO_0000253"
    assert claim[5] == "0.85"
