import os
import pytest
import logging
import socket
import yaml
from src.ingest.cache import DiskCache, OfflineCacheMissError
from src.ingest.client import IngestionClient
from src.ingest.run_all import run_ingest
from src.config import Config

def test_execute_query_cache_hit_offline(tmp_path):
    # 1. Execute client query with cache hit in offline mode (runs successfully).
    # Warm the cache in online mode (using mock interceptor)
    cache_online = DiskCache(cache_dir=str(tmp_path), offline_mode=False)
    client_online = IngestionClient(cache_online)
    res_online = client_online.fetch_uniprot("SOD1")
    
    # Read in offline mode
    cache_offline = DiskCache(cache_dir=str(tmp_path), offline_mode=True)
    client_offline = IngestionClient(cache_offline)
    res_offline = client_offline.fetch_uniprot("SOD1")
    assert res_offline == res_online

def test_execute_query_cache_miss_offline(tmp_path):
    # 2. Execute client query with cache miss in offline mode (raises OfflineCacheMissError).
    cache = DiskCache(cache_dir=str(tmp_path), offline_mode=True)
    client = IngestionClient(cache)
    with pytest.raises(OfflineCacheMissError):
        client.fetch_uniprot("SOD1")

def test_no_socket_connection_offline(tmp_path, monkeypatch):
    # 3. Verify no outgoing socket connection is created during offline execution.
    cache_online = DiskCache(cache_dir=str(tmp_path), offline_mode=False)
    client_online = IngestionClient(cache_online)
    client_online.fetch_uniprot("SOD1")
    
    cache_offline = DiskCache(cache_dir=str(tmp_path), offline_mode=True)
    client_offline = IngestionClient(cache_offline)
    
    called = False
    def mock_connect(self, address):
        nonlocal called
        called = True
        raise Exception("Forbidden live network connection!")
        
    monkeypatch.setattr(socket.socket, "connect", mock_connect)
    
    # Reading should not trigger socket connection
    client_offline.fetch_uniprot("SOD1")
    assert not called
    
    # Miss should raise OfflineCacheMissError directly, without socket
    with pytest.raises(OfflineCacheMissError):
        client_offline.fetch_uniprot("C9orf72")
    assert not called

def test_perform_ingestion_run_offline(tmp_path, caplog):
    # 4. Perform ingestion run offline with pre-cached seed gene files (completes without warning).
    config_data_online = {
        "seed_genes": ["SOD1"],
        "api_settings": {
            "offline_mode": False,
            "cache_dir": str(tmp_path),
            "string_db": {"confidence_threshold": 0.7, "partner_limit": 10},
            "pubmed": {"limit_per_gene": 10}
        },
        "scoring_weights": {"open_targets_association": 1.0}
    }
    
    config_file = tmp_path / "test_config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(config_data_online, f)
        
    # Run once in online mode to populate all necessary cache keys
    run_ingest(str(config_file))
    
    # Now switch config to offline mode
    config_data_offline = dict(config_data_online)
    config_data_offline["api_settings"]["offline_mode"] = True
    with open(config_file, "w") as f:
        yaml.dump(config_data_offline, f)
        
    with caplog.at_level(logging.INFO):
        run_ingest(str(config_file))
        for record in caplog.records:
            if record.levelname in ("WARNING", "ERROR"):
                assert "Offline cache miss" not in record.message

def test_offline_execution_log(tmp_path, caplog):
    # 5. Check that log files explicitly record the offline execution flag.
    config_data_online = {
        "seed_genes": ["SOD1"],
        "api_settings": {
            "offline_mode": False,
            "cache_dir": str(tmp_path),
            "string_db": {"confidence_threshold": 0.7, "partner_limit": 10},
            "pubmed": {"limit_per_gene": 10}
        },
        "scoring_weights": {"open_targets_association": 1.0}
    }
    
    config_file = tmp_path / "test_config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(config_data_online, f)
        
    run_ingest(str(config_file))
    
    config_data_offline = dict(config_data_online)
    config_data_offline["api_settings"]["offline_mode"] = True
    with open(config_file, "w") as f:
        yaml.dump(config_data_offline, f)
        
    with caplog.at_level(logging.INFO):
        run_ingest(str(config_file))
        assert any("Offline mode: True" in record.message for record in caplog.records)
