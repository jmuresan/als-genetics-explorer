import os
import pytest
import duckdb
from src.db.schema import create_tables
from src.db.populate import populate_pubmed
from src.ingest.client import IngestionClient
from src.ingest.cache import DiskCache

def test_construct_entrez_query(tmp_path):
    # 1. Construct valid Entrez query using targeted gene search terms.
    cache = DiskCache(cache_dir=str(tmp_path), offline_mode=False)
    client = IngestionClient(cache)
    
    res = client.fetch_pubmed_search("SOD1[gene] AND ALS")
    assert res is not None
    assert "esearchresult" in res
    assert "idlist" in res["esearchresult"]
    
    key = cache.generate_cache_key("pubmed", "esearch", {"db": "pubmed", "term": "SOD1[gene] AND ALS", "retmode": "json"})
    assert os.path.exists(cache.get_filepath(key))

def test_load_pmids_from_clinvar(tmp_path):
    # 2. Load PMIDs explicitly referenced in ClinVar records.
    cache = DiskCache(cache_dir=str(tmp_path), offline_mode=False)
    client = IngestionClient(cache)
    
    clinvar_search_res = client.fetch_clinvar_search("SOD1")
    clinvar_ids = clinvar_search_res["esearchresult"]["idlist"]
    assert clinvar_ids == ["8877"]
    
    clinvar_sum = client.fetch_clinvar_summary(clinvar_ids)
    assert "8877" in clinvar_sum["result"]
    assert clinvar_sum["result"]["8877"]["clinical_significance"]["description"] == "Pathogenic"

def test_deduplicate_pmids():
    # 3. Deduplicate exact match PMIDs (keep first record).
    conn = duckdb.connect(":memory:")
    create_tables(conn)
    
    payload = {
        "result": {
            "31567890": {
                "uid": "31567890",
                "title": "First study of C9orf72",
                "articleids": [{"idtype": "doi", "value": "10.1016/j.neuron.2019.08.010"}],
                "sortpubdate": "2019-10-01"
            }
        }
    }
    
    populate_pubmed(conn, payload, "reason1")
    
    payload_dup = {
        "result": {
            "31567890": {
                "uid": "31567890",
                "title": "Duplicate study of C9orf72",
                "articleids": [{"idtype": "doi", "value": "10.1016/j.neuron.2019.08.010"}],
                "sortpubdate": "2019-10-01"
            }
        }
    }
    populate_pubmed(conn, payload_dup, "reason2")
    
    res = conn.execute("SELECT title, ingestion_reason FROM papers WHERE pmid = '31567890'").fetchall()
    assert len(res) == 1
    assert res[0][0] == "First study of C9orf72"
    assert res[0][1] == "reason1,reason2"
    conn.close()

def test_deduplicate_dois():
    # 4. Deduplicate articles with identical DOIs.
    conn = duckdb.connect(":memory:")
    create_tables(conn)
    
    payload1 = {
        "result": {
            "11111111": {
                "uid": "11111111",
                "title": "Study 1",
                "articleids": [{"idtype": "doi", "value": "10.1016/j.neuron.2019.08.010"}],
                "sortpubdate": "2019-10-01"
            }
        }
    }
    
    payload2 = {
        "result": {
            "22222222": {
                "uid": "22222222",
                "title": "Study 2",
                "articleids": [{"idtype": "doi", "value": "10.1016/j.neuron.2019.08.010"}],
                "sortpubdate": "2019-10-01"
            }
        }
    }
    
    populate_pubmed(conn, payload1, "reason1")
    populate_pubmed(conn, payload2, "reason2")
    
    res = conn.execute("SELECT pmid, title FROM papers").fetchall()
    assert len(res) == 1
    assert res[0][0] == "11111111"
    conn.close()

def test_deduplicate_normalized_titles():
    # 5. Deduplicate articles with identical normalized titles (ignore capitalization and spacing).
    conn = duckdb.connect(":memory:")
    create_tables(conn)
    
    payload1 = {
        "result": {
            "11111111": {
                "uid": "11111111",
                "title": "C9orf72 Pathology in Amyotrophic Lateral Sclerosis",
                "articleids": [{"idtype": "doi", "value": "10.1016/j.doi1"}],
                "sortpubdate": "2019-10-01"
            }
        }
    }
    
    payload2 = {
        "result": {
            "22222222": {
                "uid": "22222222",
                "title": "  c9orf72 pathology   in amyotrophic lateral sclerosis ",
                "articleids": [{"idtype": "doi", "value": "10.1016/j.doi2"}],
                "sortpubdate": "2019-10-01"
            }
        }
    }
    
    populate_pubmed(conn, payload1, "reason1")
    populate_pubmed(conn, payload2, "reason2")
    
    res = conn.execute("SELECT pmid, title FROM papers").fetchall()
    assert len(res) == 1
    assert res[0][0] == "11111111"
    conn.close()
