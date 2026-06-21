import os
import pytest
import logging
from src.ingest.cache import DiskCache

def test_generate_identical_cache_keys():
    # 1. Generate identical cache keys for queries with identical parameters but different parameter order.
    cache = DiskCache(cache_dir="tmp_cache")
    params1 = {"gene": "SOD1", "limit": 10}
    params2 = {"limit": 10, "gene": "SOD1"}
    key1 = cache.generate_cache_key("uniprot", "search", params1)
    key2 = cache.generate_cache_key("uniprot", "search", params2)
    assert key1 == key2

def test_write_raw_api_payload(tmp_path):
    # 2. Write raw API JSON payloads to cache dir using resolved cache key.
    cache = DiskCache(cache_dir=str(tmp_path))
    payload = {"results": [{"id": 1}]}
    key = cache.generate_cache_key("uniprot", "search", {"gene": "SOD1"})
    cache.write("uniprot", "search", {"gene": "SOD1"}, payload)
    
    expected_path = os.path.join(str(tmp_path), f"{key}.json")
    assert os.path.exists(expected_path)

def test_retrieve_cached_content(tmp_path):
    # 3. Retrieve cached content on identical query and verify content match.
    cache = DiskCache(cache_dir=str(tmp_path))
    payload = {"results": [{"id": 1}]}
    params = {"gene": "SOD1"}
    cache.write("uniprot", "search", params, payload)
    retrieved = cache.read("uniprot", "search", params)
    assert retrieved == payload

def test_log_cache_hit_miss(tmp_path, caplog):
    # 4. Log a cache "MISS" on first query and a cache "HIT" on subsequent calls.
    with caplog.at_level(logging.INFO):
        cache = DiskCache(cache_dir=str(tmp_path))
        params = {"gene": "SOD1"}
        payload = {"data": "test"}
        
        # Miss
        val1 = cache.read("uniprot", "search", params)
        assert val1 is None
        assert any("[CACHE MISS]" in record.message for record in caplog.records)
        
        # Write
        cache.write("uniprot", "search", params, payload)
        
        # Hit
        caplog.clear()
        val2 = cache.read("uniprot", "search", params)
        assert val2 == payload
        assert any("[CACHE HIT]" in record.message for record in caplog.records)

def test_multiple_sources_no_collision(tmp_path):
    # 5. Handle multiple API sources without key collisions.
    cache = DiskCache(cache_dir=str(tmp_path))
    params = {"gene": "SOD1"}
    key_uniprot = cache.generate_cache_key("uniprot", "search", params)
    key_clinvar = cache.generate_cache_key("clinvar", "search", params)
    assert key_uniprot != key_clinvar
