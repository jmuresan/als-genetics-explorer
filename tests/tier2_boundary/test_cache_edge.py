import os
import stat
import json
import pytest
import threading
from src.ingest.cache import DiskCache

def test_cache_read_only_mode(tmp_path):
    # 1. Read from cache when cache files are write-protected (read-only mode).
    cache = DiskCache(cache_dir=str(tmp_path))
    params = {"gene": "SOD1"}
    payload = {"data": "read_only_test"}
    cache.write("uniprot", "search", params, payload)
    
    # Make cache file read-only
    key = cache.generate_cache_key("uniprot", "search", params)
    filepath = cache.get_filepath(key)
    os.chmod(filepath, stat.S_IREAD) # Set to read-only (0o400)
    
    try:
        # Should be able to read successfully
        retrieved = cache.read("uniprot", "search", params)
        assert retrieved == payload
    finally:
        # Restore permissions for cleanup
        os.chmod(filepath, stat.S_IWRITE | stat.S_IREAD)

def test_cache_long_query_strings(tmp_path):
    # 2. Handle extremely long query strings or parameter lists (verify hash generation does not fail).
    cache = DiskCache(cache_dir=str(tmp_path))
    long_gene_list = "A" * 10000
    params = {"genes": long_gene_list, "limit": 100000}
    
    key = cache.generate_cache_key("string", "network", params)
    assert len(key) == 64
    # Ensure it writes and reads fine
    payload = {"status": "ok"}
    cache.write("string", "network", params, payload)
    assert cache.read("string", "network", params) == payload

def test_cache_invalidation_on_config_changes(tmp_path):
    # 3. Cache invalidation on configuration weight or ingestion threshold changes.
    cache = DiskCache(cache_dir=str(tmp_path))
    
    # Simulate queries with different STRING confidence thresholds
    params_low = {"identifiers": "SOD1", "required_score": 400}
    params_high = {"identifiers": "SOD1", "required_score": 700}
    
    key_low = cache.generate_cache_key("string", "interaction_partners", params_low)
    key_high = cache.generate_cache_key("string", "interaction_partners", params_high)
    
    assert key_low != key_high

def test_cache_zero_byte_or_corrupted(tmp_path, caplog):
    # 4. Handle zero-byte or corrupted cache files.
    cache = DiskCache(cache_dir=str(tmp_path))
    params = {"gene": "SOD1"}
    key = cache.generate_cache_key("uniprot", "search", params)
    filepath = cache.get_filepath(key)
    
    # Create zero-byte file
    with open(filepath, "w") as f:
        f.write("")
        
    # Read should return None (miss)
    assert cache.read("uniprot", "search", params) is None
    
    # Write corrupted JSON
    with open(filepath, "w") as f:
        f.write("{invalid json")
        
    # Read should return None (miss)
    assert cache.read("uniprot", "search", params) is None

def test_cache_concurrency(tmp_path):
    # 5. Concurrent attempts to read/write the same cache file.
    cache = DiskCache(cache_dir=str(tmp_path))
    params = {"gene": "SOD1"}
    payload = {"data": "concurrent_test_data"}
    
    errors = []
    
    def writer():
        try:
            for _ in range(50):
                cache.write("uniprot", "search", params, payload)
        except Exception as e:
            errors.append(e)

    def reader():
        try:
            for _ in range(50):
                val = cache.read("uniprot", "search", params)
                # It might be None initially, but if it returns a value, it must be correct
                if val is not None:
                    assert val == payload
        except Exception as e:
            errors.append(e)
            
    t1 = threading.Thread(target=writer)
    t2 = threading.Thread(target=reader)
    
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    assert len(errors) == 0
