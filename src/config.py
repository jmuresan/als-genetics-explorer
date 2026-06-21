import os
import yaml
import logging
from typing import List, Dict, Any

class Config:
    """Configuration parser and interface for ALS Genetic Mechanism Explorer."""
    def __init__(self, config_path: str = None):
        logger = logging.getLogger("als_explorer.config")
        if config_path is None:
            # Resolve to project root config.yaml
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(base_dir, "config.yaml")
        
        self.config_path = config_path
        self.data: Dict[str, Any] = {}
        if not os.path.exists(config_path):
            logger.warning(f"Config file not found at {config_path}. Using default fallbacks.")
        else:
            with open(config_path, "r", encoding="utf-8") as f:
                try:
                    self.data = yaml.safe_load(f) or {}
                except yaml.YAMLError as e:
                    logger.error(f"Malformed YAML syntax in {config_path}")
                    raise e
        
        # Load parameters
        self.seed_genes: List[str] = self.data.get("seed_genes", [])
        
        api_settings = self.data.get("api_settings", {})
        self.offline_mode: bool = api_settings.get("offline_mode", False)
        
        # Handle relative/absolute cache directory path
        raw_cache_dir = api_settings.get("cache_dir", "data/raw/cache")
        if not os.path.isabs(raw_cache_dir):
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.cache_dir = os.path.abspath(os.path.join(base_dir, raw_cache_dir))
        else:
            self.cache_dir = raw_cache_dir

        string_db = api_settings.get("string_db", {})
        self.string_confidence_threshold: float = string_db.get("confidence_threshold", 0.7)
        self.string_partner_limit: int = string_db.get("partner_limit", 10)
        
        # Boundary validation for STRING partner limit
        if self.string_partner_limit <= 0 or self.string_partner_limit > 1000:
            raise ValueError(f"STRING partner limit must be between 1 and 1000, got {self.string_partner_limit}")
            
        pubmed = api_settings.get("pubmed", {})
        self.pubmed_limit_per_gene: int = pubmed.get("limit_per_gene", 10)
        if self.pubmed_limit_per_gene <= 0:
            raise ValueError(f"PubMed limit per gene must be greater than 0, got {self.pubmed_limit_per_gene}")
        
        raw_weights = self.data.get("scoring_weights", {})
        self.scoring_weights = {}
        if raw_weights:
            # Validate non-negative and numeric first
            for k, v in raw_weights.items():
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    raise TypeError(f"Weight for {k} must be numeric, got {type(v)}")
                if v < 0:
                    raise ValueError(f"Weight for {k} cannot be negative, got {v}")
            
            total = sum(raw_weights.values())
            if total <= 0:
                raise ValueError("Sum of scoring weights must be greater than 0")
            
            # Normalise so they sum to 1.0
            self.scoring_weights = {k: float(v) / total for k, v in raw_weights.items()}
        else:
            # Default weights
            self.scoring_weights = {
                "open_targets_association": 0.25,
                "clinvar_pathogenicity": 0.20,
                "string_centrality": 0.15,
                "literature_volume": 0.15,
                "druggability": 0.10
            }

    def get(self, key: str, default: Any = None) -> Any:
        """Utility method to get raw config values by key."""
        return self.data.get(key, default)
