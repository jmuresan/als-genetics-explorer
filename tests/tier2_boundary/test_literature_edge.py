import os
import pytest
import logging
import requests
from src.ingest.client import PaperDeduplicator, parse_pubmed_xml, PubMedClient
from src.ingest.cache import DiskCache

def test_deduplicate_pmids_different_dois_titles(caplog):
    # 1. Ingest papers with duplicate PMIDs but different DOIs or titles (log warning, resolve by PMID).
    dedup = PaperDeduplicator()
    paper1 = {"pmid": "12345", "doi": "10.1001/a", "title": "First Paper"}
    paper2 = {"pmid": "12345", "doi": "10.1001/b", "title": "Second Paper"}
    
    with caplog.at_level(logging.WARNING, logger="als_explorer.client"):
        dedup.add_paper(paper1, "reason1", "SOD1")
        dedup.add_paper(paper2, "reason2", "SOD1")
        
        assert any("Duplicate paper found" in record.message for record in caplog.records)
        
    unique = dedup.get_unique_papers()
    assert len(unique) == 1
    assert unique[0]["pmid"] == "12345"
    assert unique[0]["title"] == "First Paper"

def test_abstracts_special_characters():
    # 2. Handle abstracts containing special HTML characters, XML entities, or non-ASCII characters.
    xml_content = """<?xml version="1.0" encoding="UTF-8"?>
    <PubmedArticleSet>
      <PubmedArticle>
        <MedlineCitation>
          <PMID>12345</PMID>
        </MedlineCitation>
        <Article>
          <Abstract>
            <AbstractText Label="AIM">&lt;b&gt;Aim:&lt;/b&gt; Investigate ALS mechanisms in patients with C9orf72 mutations &amp;amp; SOD1 mutations (Café-au-lait ©).</AbstractText>
          </Abstract>
        </Article>
      </PubmedArticle>
    </PubmedArticleSet>
    """
    
    res = parse_pubmed_xml(xml_content)
    assert "12345" in res
    abstract = res["12345"]
    assert "Aim:" in abstract
    assert "mutation" in abstract
    assert "Café-au-lait" in abstract

def test_missing_dois_and_pubdates():
    # 3. Handle cases where the API returns missing DOIs or missing publication dates.
    from src.ingest.client import parse_esummary_pubmed
    summary_data = {
        "12345": {
            "uid": "12345",
            "title": "A paper",
            "authors": [],
            "source": "Nature",
            "articleids": [{"idtype": "pubmed", "value": "12345"}],
        }
    }
    
    res = parse_esummary_pubmed(summary_data)
    assert len(res) == 1
    assert res[0]["doi"] == ""
    assert res[0]["pubdate"] == ""

def test_ingestion_reasons_tracking():
    # 4. Ingestion reasons tracking when a paper is fetched for multiple reasons.
    dedup = PaperDeduplicator()
    paper = {"pmid": "12345", "doi": "10.1001/a", "title": "ALS study"}
    
    dedup.add_paper(paper, "seed_gene_search", "SOD1")
    dedup.add_paper(paper, "clinvar_reference", "C9orf72")
    
    unique = dedup.get_unique_papers()
    assert len(unique) == 1
    reasons = unique[0]["ingestion_reasons"]
    assert len(reasons) == 2
    assert reasons[0] == {"reason": "seed_gene_search", "gene": "SOD1"}
    assert reasons[1] == {"reason": "clinvar_reference", "gene": "C9orf72"}

def test_rate_limit_429_retry(tmp_path, monkeypatch):
    # 5. Entrez API rate limits (simulate HTTP 429 status code and verify retry logic).
    cache = DiskCache(cache_dir=str(tmp_path))
    client = PubMedClient(cache)
    
    call_count = 0
    class MockResponse:
        def __init__(self, status_code, json_data):
            self.status_code = status_code
            self.json_data = json_data
            self.text = ""
        def json(self):
            return self.json_data
        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError("Error")
                
    def mock_get(url, params=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return MockResponse(429, {})
        return MockResponse(200, {"esearchresult": {"idlist": ["12345"]}})
        
    monkeypatch.setattr(requests, "get", mock_get)
    
    res = client.search_pubmed("SOD1", limit=1)
    assert res == ["12345"]
    assert call_count == 3
