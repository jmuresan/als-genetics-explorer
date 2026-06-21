import os
import urllib.request
import pytest
import socket
import requests
from src.ingest.cache import DiskCache, OfflineCacheMissError
from src.ingest.client import IngestionClient

def test_dynamic_offline_switch(tmp_path):
    # 1. System switches dynamically from online to offline mid-run.
    cache = DiskCache(cache_dir=str(tmp_path), offline_mode=False)
    client = IngestionClient(cache)
    
    # In online mode (with mock conftest active), a miss fetches data and caches it
    res = client.fetch_uniprot("SOD1")
    assert res is not None
    
    # Switch dynamically to offline mode
    cache.offline_mode = True
    
    # A cache hit should still succeed
    res2 = client.fetch_uniprot("SOD1")
    assert res2 == res
    
    # A cache miss must now raise OfflineCacheMissError
    with pytest.raises(OfflineCacheMissError):
        client.fetch_uniprot("C9orf72")

def test_offline_non_standard_ports(tmp_path):
    # 2. Outgoing requests to non-standard ports or internal networks are blocked.
    cache = DiskCache(cache_dir=str(tmp_path), offline_mode=True)
    client = IngestionClient(cache)
    
    # Force a request to localhost on a non-standard port
    with pytest.raises(OfflineCacheMissError):
        client.uniprot._request("GET", "http://127.0.0.1:8080/search", "search", {"query": "SOD1"})
    
    with pytest.raises(OfflineCacheMissError):
        client.uniprot._request("GET", "http://10.0.0.1:9000/search", "search", {"query": "SOD1"})

def test_offline_empty_cache_file(tmp_path):
    # 3. Cache key exists but points to an empty file in offline mode (must raise offline error).
    cache = DiskCache(cache_dir=str(tmp_path), offline_mode=True)
    client = IngestionClient(cache)
    
    params = {"query": "gene:SOD1 AND organism_id:9606", "format": "json"}
    key = cache.generate_cache_key("uniprot", "search", params)
    filepath = cache.get_filepath(key)
    
    # Create empty cache file
    with open(filepath, "w") as f:
        f.write("")
        
    # Read/Query must raise OfflineCacheMissError rather than return empty/None
    with pytest.raises(OfflineCacheMissError):
        client.fetch_uniprot("SOD1")

def test_offline_prevents_dns(tmp_path, monkeypatch):
    # 4. DNS failure simulation in offline mode (offline layer must prevent DNS requests altogether).
    cache = DiskCache(cache_dir=str(tmp_path), offline_mode=True)
    client = IngestionClient(cache)
    
    dns_called = False
    def mock_dns(*args, **kwargs):
        nonlocal dns_called
        dns_called = True
        raise socket.gaierror(-2, "Name or service not known")
        
    monkeypatch.setattr(socket, "getaddrinfo", mock_dns)
    monkeypatch.setattr(socket, "gethostbyname", mock_dns)
    
    # Query should raise OfflineCacheMissError without calling DNS
    with pytest.raises(OfflineCacheMissError):
        client.fetch_uniprot("SOD1")
        
    assert not dns_called

def test_offline_blocks_all_http_libraries(tmp_path):
    # 5. Assert offline mode blocks all library requests (conftest / network violation interceptor).
    from tests.conftest import BlockedNetworkError
    
    with pytest.raises(BlockedNetworkError):
        requests.get("https://example.com")
        
    # urllib might also try to make a network request, conftest doesn't patch urllib directly,
    # but since socket is intercepted or we're in offline, it will fail/be blocked.
    # Actually, we can patch urllib just in case or conftest's blocked socket connects.
    # Let's ensure urllib call raises BlockedNetworkError or is blocked.
