from src.ingest.client import (
    normalize_title,
    normalize_doi,
    PaperDeduplicator,
    parse_pubmed_xml,
    parse_esummary_pubmed
)

def test_title_normalization():
    title1 = "  ALS   Genetics and <i>SOD1</i> mutation. "
    title2 = "als genetics and sod1 mutation"
    assert normalize_title(title1) == title2
    
    title3 = "ALS: Genetics, Mechanisms, & Treatment."
    title4 = "als genetics mechanisms treatment"
    assert normalize_title(title3) == title4
    
    assert normalize_title(None) == ""
    assert normalize_title("") == ""

def test_doi_normalization():
    assert normalize_doi("https://doi.org/10.1038/nature123") == "10.1038/nature123"
    assert normalize_doi("http://dx.doi.org/10.1000/xyz") == "10.1000/xyz"
    assert normalize_doi("doi:10.1000/xyz") == "10.1000/xyz"
    assert normalize_doi("  10.1000/XYZ  ") == "10.1000/xyz"
    assert normalize_doi(None) == ""
    assert normalize_doi("") == ""

def test_paper_deduplication():
    dedup = PaperDeduplicator()
    
    paper1 = {
        "pmid": "11111",
        "doi": "10.1000/abc",
        "title": "First Paper on SOD1",
        "abstract": "Abstract 1"
    }
    
    paper2 = {
        "pmid": "11111", # Same PMID
        "doi": "10.1000/abc-diff",
        "title": "First Paper on SOD1 (variant)",
        "abstract": ""
    }
    
    dedup.add_paper(paper1, "clinvar", "SOD1")
    dedup.add_paper(paper2, "targeted_search", "SOD1")
    
    unique = dedup.get_unique_papers()
    assert len(unique) == 1
    assert len(unique[0]["ingestion_reasons"]) == 2
    assert unique[0]["abstract"] == "Abstract 1" # preserved metadata
    
    # Check DOI deduplication
    paper3 = {
        "pmid": "22222",
        "doi": "10.1000/abc", # Same DOI as paper1
        "title": "Different Title",
        "abstract": "Abstract 3"
    }
    dedup.add_paper(paper3, "reactome", "SOD1")
    unique = dedup.get_unique_papers()
    assert len(unique) == 1 # still 1 because of DOI match!
    
    # Check Title deduplication
    dedup2 = PaperDeduplicator()
    paper_a = {
        "pmid": "44444",
        "doi": "10.1000/doi-a",
        "title": "Title with <i>HTML</i>!",
        "abstract": "A"
    }
    paper_b = {
        "pmid": "",
        "doi": "",
        "title": "title with html", # Same normalized title
        "abstract": "B"
    }
    dedup2.add_paper(paper_a, "clinvar", "SOD1")
    dedup2.add_paper(paper_b, "reactome", "SOD1")
    
    unique2 = dedup2.get_unique_papers()
    assert len(unique2) == 1

def test_pubmed_parsers():
    # Test esummary parsing
    mock_esummary = {
        "result": {
            "uids": ["12345"],
            "12345": {
                "title": "ALS Study",
                "authors": [{"name": "Smith J"}],
                "source": "Nature",
                "pubdate": "2020 Jan",
                "articleids": [{"idtype": "doi", "value": "10.1038/nature123"}]
            }
        }
    }
    parsed = parse_esummary_pubmed(mock_esummary["result"])
    assert len(parsed) == 1
    assert parsed[0]["pmid"] == "12345"
    assert parsed[0]["doi"] == "10.1038/nature123"
    assert parsed[0]["title"] == "ALS Study"
    assert parsed[0]["authors"] == ["Smith J"]
    
    # Test XML parsing for abstracts
    mock_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <PubmedArticleSet>
      <PubmedArticle>
        <MedlineCitation>
          <PMID>12345</PMID>
        </MedlineCitation>
        <Article>
          <Abstract>
            <AbstractText Label="BACKGROUND">Background info.</AbstractText>
            <AbstractText Label="METHODS">Method info.</AbstractText>
          </Abstract>
        </Article>
      </PubmedArticle>
    </PubmedArticleSet>
    """
    pmid_to_abstract = parse_pubmed_xml(mock_xml)
    assert pmid_to_abstract["12345"] == "BACKGROUND: Background info. METHODS: Method info."

def test_transitive_deduplication():
    dedup = PaperDeduplicator()
    # Add Paper 1 (PMID only)
    dedup.add_paper({"pmid": "111", "doi": "", "title": "Paper 1"}, "clinvar", "SOD1")
    # Add Paper 2 (DOI only)
    dedup.add_paper({"pmid": "", "doi": "10.1038/nature123", "title": "Paper 2"}, "reactome", "FUS")
    # Add Paper 3 (Bridge: has both PMID and DOI)
    dedup.add_paper({"pmid": "111", "doi": "10.1038/nature123", "title": "Paper 1 and 2 Bridge"}, "targeted_search", "C9orf72")
    
    unique = dedup.get_unique_papers()
    # Should merge all 3 papers into 1 canonical record
    assert len(unique) == 1
    record = unique[0]
    assert record["pmid"] == "111"
    assert record["doi"] == "10.1038/nature123"
    reasons = record["ingestion_reasons"]
    assert len(reasons) == 3
    genes = {r["gene"] for r in reasons}
    assert genes == {"SOD1", "FUS", "C9orf72"}

def test_title_normalization_edge_cases():
    # Underscores
    assert normalize_title("SOD1_mutation") == normalize_title("SOD1 mutation")
    # Hyphens
    assert normalize_title("alpha-synuclein") == normalize_title("alpha synuclein")
    # HTML entities
    assert normalize_title("ALS &amp; SOD1") == normalize_title("ALS & SOD1")
    # Mixed formatting
    assert normalize_title("ALS &amp; SOD1-mutation_test") == "als sod1 mutation test"

def test_doi_normalization_resolver_prefixes():
    assert normalize_doi("https://doi.org/10.1038/nature123") == "10.1038/nature123"
    assert normalize_doi("http://dx.doi.org/10.1038/nature123") == "10.1038/nature123"
    assert normalize_doi("dx.doi.org/10.1038/nature123") == "10.1038/nature123"
    assert normalize_doi("doi.org/10.1038/nature123") == "10.1038/nature123"
    assert normalize_doi("doi:10.1038/nature123") == "10.1038/nature123"
    assert normalize_doi("DOI:10.1038/nature123") == "10.1038/nature123"

def test_cache_key_batch_determinism(temp_cache_dir):
    from unittest.mock import MagicMock
    from src.ingest.client import PubMedClient
    from src.ingest.cache import DiskCache

    cache = DiskCache(temp_cache_dir, offline_mode=False)
    client = PubMedClient(cache)
    
    # We mock _request to capture how the IDs were formatted
    client._request = MagicMock(return_value={})
    
    # Call fetch_summaries with two different orderings
    client.fetch_summaries(["456", "123"])
    client.fetch_summaries(["123", "456"])
    
    # Verify both calls format the 'id' parameter identically
    calls = client._request.call_args_list
    assert len(calls) == 2
    
    params_1 = calls[0].kwargs.get("params") or calls[0].args[3]
    params_2 = calls[1].kwargs.get("params") or calls[1].args[3]
    
    assert params_1["id"] == "123,456"
    assert params_2["id"] == "123,456"
    
    # Check key determinism with generate_cache_key directly
    k1 = cache.generate_cache_key("pubmed", "esummary", params_1)
    k2 = cache.generate_cache_key("pubmed", "esummary", params_2)
    assert k1 == k2
