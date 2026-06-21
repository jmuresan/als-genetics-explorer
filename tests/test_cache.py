import pytest
from src.ingest.cache import DiskCache, OfflineCacheMissError

def test_cache_key_generation(temp_cache_dir):
    cache = DiskCache(temp_cache_dir)
    
    # Check that key generation is deterministic and param ordering doesn't affect key
    key1 = cache.generate_cache_key("test_source", "test_endpoint", {"a": 1, "b": 2})
    key2 = cache.generate_cache_key("test_source", "test_endpoint", {"b": 2, "a": 1})
    assert key1 == key2
    
    # Check that key is different for different sources/endpoints/params
    key3 = cache.generate_cache_key("test_source2", "test_endpoint", {"a": 1, "b": 2})
    assert key1 != key3
    
    key4 = cache.generate_cache_key("test_source", "test_endpoint_diff", {"a": 1, "b": 2})
    assert key1 != key4

def test_cache_read_write(temp_cache_dir):
    cache = DiskCache(temp_cache_dir)
    
    source = "uniprot"
    endpoint = "search"
    params = {"query": "gene:SOD1"}
    data = {"accession": "P00441", "name": "SOD1"}
    
    # Initial read should be None (cache miss)
    assert cache.read(source, endpoint, params) is None
    
    # Write to cache
    cache.write(source, endpoint, params, data)
    
    # Subsequent read should hit cache and return the data
    cached_data = cache.read(source, endpoint, params)
    assert cached_data == data

def test_offline_mode_behavior(temp_cache_dir):
    # Setup cache in offline mode
    cache = DiskCache(temp_cache_dir, offline_mode=True)
    
    source = "uniprot"
    endpoint = "search"
    params = {"query": "gene:SOD1"}
    
    # Reading missing cache entry in offline mode must raise OfflineCacheMissError
    with pytest.raises(OfflineCacheMissError):
        cache.read(source, endpoint, params)
    
    # Setup cache in online mode to write data first
    cache_online = DiskCache(temp_cache_dir, offline_mode=False)
    data = {"test": "data"}
    cache_online.write(source, endpoint, params, data)
    
    # Read again with offline cache, it should hit now
    assert cache.read(source, endpoint, params) == data
