import pytest
from unittest.mock import MagicMock
from src.ingest.cache import DiskCache, OfflineCacheMissError
from src.ingest.client import (
    BaseClient,
    UniProtClient,
    ReactomeClient,
    OpenTargetsClient,
    ClinVarClient,
    StringClient,
    PubMedClient
)

def test_base_client_online_success(temp_cache_dir, monkeypatch, mock_response_fixture):
    cache = DiskCache(temp_cache_dir, offline_mode=False)
    client = BaseClient("test_source", cache, rate_limit_delay=0)
    
    mock_data = {"result": "ok"}
    mock_get = MagicMock(return_value=mock_response_fixture(mock_data))
    monkeypatch.setattr("requests.get", mock_get)
    
    url = "https://api.example.com/data"
    endpoint = "endpoint_name"
    params = {"q": "test"}
    
    # 1. First call: Cache miss, calls network, writes to cache
    res1 = client._request("GET", url, endpoint, params=params)
    assert res1 == mock_data
    assert mock_get.call_count == 1
    
    # 2. Second call: Cache hit, does not call network
    res2 = client._request("GET", url, endpoint, params=params)
    assert res2 == mock_data
    assert mock_get.call_count == 1 # still 1!

def test_base_client_offline_block_and_hit(temp_cache_dir, monkeypatch, mock_response_fixture):
    cache = DiskCache(temp_cache_dir, offline_mode=True)
    client = BaseClient("test_source", cache, rate_limit_delay=0)
    
    # Network call mock (should never be called)
    mock_get = MagicMock(side_effect=RuntimeError("Should not call network!"))
    monkeypatch.setattr("requests.get", mock_get)
    
    url = "https://api.example.com/data"
    endpoint = "endpoint_name"
    params = {"q": "test"}
    
    # Cache miss in offline mode -> raises OfflineCacheMissError
    with pytest.raises(OfflineCacheMissError):
        client._request("GET", url, endpoint, params=params)
        
    assert mock_get.call_count == 0
    
    # Now write data to cache online
    cache_online = DiskCache(temp_cache_dir, offline_mode=False)
    mock_data = {"cached": "yes"}
    cache_online.write("test_source", endpoint, params, mock_data)
    
    # Read again in offline mode -> returns cached data, no network call
    res = client._request("GET", url, endpoint, params=params)
    assert res == mock_data
    assert mock_get.call_count == 0

def test_string_client_params(temp_cache_dir, monkeypatch, mock_response_fixture):
    cache = DiskCache(temp_cache_dir, offline_mode=False)
    client = StringClient(cache, confidence_threshold=0.8, limit=5)
    client.rate_limit_delay = 0
    
    mock_data = [{"p1": "A", "p2": "B", "score": 0.85}]
    mock_get = MagicMock(return_value=mock_response_fixture(mock_data))
    monkeypatch.setattr("requests.get", mock_get)
    
    interactions = client.get_interactions("SOD1")
    assert interactions == mock_data
    assert mock_get.call_count == 1
    
    # Check parameters passed to requests
    args, kwargs = mock_get.call_args
    assert kwargs["params"]["required_score"] == 800
    assert kwargs["params"]["limit"] == 5
    assert kwargs["params"]["identifiers"] == "SOD1"

def test_open_targets_client(temp_cache_dir, monkeypatch, mock_response_fixture):
    cache = DiskCache(temp_cache_dir, offline_mode=False)
    client = OpenTargetsClient(cache)
    client.rate_limit_delay = 0
    
    # Mock GraphQL post responses
    search_mock = {"data": {"search": {"hits": [{"id": "ENSG00000091409", "entity": "target"}]}}}
    association_mock = {"data": {"target": {"id": "ENSG00000091409", "approvedSymbol": "SOD1"}}}
    evidence_mock = {"data": {"target": {"evidences": {"rows": [{"literature": ["12345"]}]}}}}
    
    # Setup mock post handler
    calls = []
    def mock_post(url, json, **kwargs):
        calls.append((url, json))
        if "targetSearch" in json["query"]:
            return mock_response_fixture(search_mock)
        elif "targetAssociations" in json["query"]:
            return mock_response_fixture(association_mock)
        elif "targetDiseaseEvidences" in json["query"]:
            return mock_response_fixture(evidence_mock)
        return mock_response_fixture({})
        
    monkeypatch.setattr("requests.post", mock_post)
    
    # Test resolve
    ens_id = client.resolve_ensembl_id("SOD1")
    assert ens_id == "ENSG00000091409"
    
    # Test associations
    assoc = client.get_target_associations("ENSG00000091409")
    assert assoc["id"] == "ENSG00000091409"
    
    # Test full fetch
    full_data = client.fetch_gene_data("SOD1")
    assert full_data["symbol"] == "SOD1"
    assert full_data["ensembl_id"] == "ENSG00000091409"
    assert len(full_data["evidences"]) == 1
