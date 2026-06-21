import pytest
import duckdb
from src.db.schema import create_tables
from src.db.populate import (
    populate_uniprot,
    populate_string,
    populate_reactome,
    populate_clinvar,
    populate_opentargets,
    populate_pubmed
)

@pytest.fixture
def db_conn():
    conn = duckdb.connect(":memory:")
    create_tables(conn)
    yield conn
    conn.close()

def test_loader_missing_nested_attributes(db_conn):
    # 1. Normalizer encounters raw JSON missing expected nested attributes (must insert NULL or default, not crash).
    uniprot_data = {
        "results": [
            {
                "primaryAccession": "P00441",
                # genes and proteinDescription are missing
            }
        ]
    }
    populate_uniprot(db_conn, uniprot_data)
    
    res = db_conn.execute("SELECT gene_symbol, protein_description FROM genes").fetchone()
    assert res[0] is None
    assert res[1] is None

def test_loader_duplicate_primary_keys(db_conn):
    # 2. Insert duplicate primary keys (must raise a constraint error or upsert/ignore based on schema rules).
    uniprot_data_1 = {
        "results": [
            {
                "primaryAccession": "P00441",
                "genes": [{"geneName": {"value": "SOD1"}}],
                "proteinDescription": {"recommendedName": {"fullName": {"value": "First desc"}}}
            }
        ]
    }
    populate_uniprot(db_conn, uniprot_data_1)
    
    uniprot_data_2 = {
        "results": [
            {
                "primaryAccession": "P00441",
                "genes": [{"geneName": {"value": "SOD1"}}],
                "proteinDescription": {"recommendedName": {"fullName": {"value": "Second desc"}}}
            }
        ]
    }
    populate_uniprot(db_conn, uniprot_data_2)
    
    res = db_conn.execute("SELECT protein_description FROM genes WHERE uniprot_id = 'P00441'").fetchall()
    assert len(res) == 1
    assert res[0][0] == "Second desc"
    
    db_conn.execute("INSERT INTO papers (pmid, title) VALUES ('123', 'First Title')")
    with pytest.raises(duckdb.ConstraintException):
        db_conn.execute("INSERT INTO papers (pmid, title) VALUES ('123', 'Second Title')")

def test_loader_mismatching_data_types(db_conn):
    # 3. Handle mismatching data types (e.g., string in score column).
    with pytest.raises(duckdb.ConversionException):
        db_conn.execute("INSERT INTO disease_associations (gene_symbol, disease_id, score) VALUES ('SOD1', 'EFO_0000253', 'not_a_number')")

def test_loader_empty_payloads(db_conn):
    # 4. Populate tables using mock data containing empty lists or empty dictionaries.
    populate_uniprot(db_conn, {})
    populate_string(db_conn, [])
    populate_reactome(db_conn, "SOD1", [])
    populate_clinvar(db_conn, "SOD1", {})
    populate_opentargets(db_conn, {})
    populate_pubmed(db_conn, {}, "reason")
    
    counts = db_conn.execute("SELECT COUNT(*) FROM genes").fetchone()[0]
    assert counts == 0

def test_loader_large_text_values(db_conn):
    # 5. Truncation or conversion error handling when text exceeds expected size.
    huge_abstract = "A" * 1000000
    db_conn.execute("INSERT INTO papers (pmid, title, abstract) VALUES ('123', 'Large Paper', ?)", [huge_abstract])
    
    res = db_conn.execute("SELECT abstract FROM papers WHERE pmid = '123'").fetchone()[0]
    assert len(res) == 1000000
