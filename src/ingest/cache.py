import os
import hashlib
import json
import tempfile
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("als_explorer.cache")

class OfflineCacheMissError(Exception):
    """Raised when an API request is made in offline mode but the cache is empty."""
    pass

class DiskCache:
    """Disk-backed caching system for API response payloads."""
    def __init__(self, cache_dir: str, offline_mode: bool = False):
        self.cache_dir = os.path.abspath(cache_dir)
        self.offline_mode = offline_mode
        os.makedirs(self.cache_dir, exist_ok=True)

    def generate_cache_key(self, source_name: str, endpoint: str, query_params: Optional[Dict[str, Any]] = None) -> str:
        """
        Generates a deterministic 64-character SHA-256 hash representation of an API request.
        Query parameters are sorted by key to guarantee duplicate calls produce the same hash.
        """
        serialized_params = ""
        if query_params is not None:
            serialized_params = json.dumps(query_params, sort_keys=True)
        
        raw_key = f"{source_name.lower().strip()}:{endpoint.lower().strip()}:{serialized_params}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    def get_filepath(self, cache_key: str) -> str:
        """Returns the absolute path to the cached JSON file."""
        return os.path.join(self.cache_dir, f"{cache_key}.json")

    def read(self, source_name: str, endpoint: str, query_params: Optional[Dict[str, Any]] = None) -> Optional[Any]:
        """
        Reads cached raw data from disk. If the cache is empty and offline_mode is True,
        raises OfflineCacheMissError. If offline_mode is False, returns None to allow network fetch.
        """
        key = self.generate_cache_key(source_name, endpoint, query_params)
        path = self.get_filepath(key)
        
        if os.path.exists(path):
            logger.info(f"[CACHE HIT] Source: {source_name}, Endpoint: {endpoint}, Key: {key}")
            with open(path, "r", encoding="utf-8") as f:
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    return None
        
        if self.offline_mode:
            logger.error(f"[OFFLINE ERROR] Cache miss for {source_name}/{endpoint} with params {query_params}")
            raise OfflineCacheMissError(
                f"Offline cache miss for {source_name} - {endpoint}. Network calls are blocked."
            )
            
        logger.info(f"[CACHE MISS] Source: {source_name}, Endpoint: {endpoint}, Key: {key}")
        return None

    def write(self, source_name: str, endpoint: str, query_params: Optional[Dict[str, Any]], data: Any) -> None:
        """Writes raw API response data to a cache file atomically."""
        key = self.generate_cache_key(source_name, endpoint, query_params)
        path = self.get_filepath(key)
        
        # Safely write to temp first, then rename (atomic write)
        temp_dir = os.path.dirname(path)
        os.makedirs(temp_dir, exist_ok=True)
        
        fd, temp_path = tempfile.mkstemp(dir=temp_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, path)
            logger.info(f"[CACHE STORED] Saved raw data to {path}")
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise e
