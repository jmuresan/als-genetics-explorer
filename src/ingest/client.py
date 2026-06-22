import re
import xml.etree.ElementTree as ET
import time
import requests
import logging
import html
from typing import Dict, Any, Optional, List, Set
from src.ingest.cache import DiskCache, OfflineCacheMissError

logger = logging.getLogger("als_explorer.client")

# --- Normalization Helpers ---

def normalize_title(title: Optional[str]) -> str:
    """Normalizes titles by downcasing, stripping whitespace, and removing punctuation and HTML tags."""
    if not title:
        return ""
    # Decode HTML entities using html.unescape
    title = html.unescape(title)
    # Replace underscores (_) and hyphens (-) with a space before running regex or lowercasing
    title = title.replace("_", " ").replace("-", " ")
    # Strip HTML tags
    clean = re.sub(r"<[^>]*>", "", title)
    # Remove punctuation and lowercase
    clean = re.sub(r"[^\w\s]", "", clean).lower()
    # Normalize whitespace
    return " ".join(clean.split())

def normalize_doi(doi: Optional[str]) -> str:
    """Normalizes DOIs to lowercase and removes typical resolver prefixes."""
    if not doi:
        return ""
    # Strip resolver prefixes regardless of protocol presence case-insensitively
    doi = re.sub(r"^(?:https?://)?(?:(?:dx\.)?doi\.org/|doi:)", "", doi.strip(), flags=re.IGNORECASE)
    return doi.lower().strip()

# --- Extraction Helpers ---

def extract_pmids_from_dict(data: Any) -> Set[str]:
    """Recursively search for fields matching 'pmid' or 'pubid' containing numeric values."""
    pmids = set()
    if isinstance(data, dict):
        for k, v in data.items():
            if k.lower() in ("pmid", "pubid") and isinstance(v, (str, int)):
                val = str(v).strip()
                if val.isdigit():
                    pmids.add(val)
            elif isinstance(v, (dict, list)):
                pmids.update(extract_pmids_from_dict(v))
    elif isinstance(data, list):
        for item in data:
            pmids.update(extract_pmids_from_dict(item))
    return pmids

def extract_pmids_regex(json_str: str) -> Set[str]:
    """Uses regex on raw JSON/XML string to find references to PMIDs."""
    pmids = set()
    matches = re.findall(r'"(?:pmid|pubid)"\s*:\s*"?(\d+)"?', json_str, re.IGNORECASE)
    for m in matches:
        pmids.add(m)
    return pmids

# --- XML and Summary Parsers ---

def parse_pubmed_xml(xml_content: str) -> Dict[str, str]:
    """Parses PubMed XML and maps PMID to Abstract text."""
    pmid_to_abstract = {}
    if not xml_content:
        return pmid_to_abstract
    try:
        root = ET.fromstring(xml_content.encode("utf-8"))
        for article in root.findall(".//PubmedArticle"):
            pmid_el = article.find(".//MedlineCitation/PMID")
            pmid = pmid_el.text if pmid_el is not None else ""
            if not pmid:
                continue
            
            abstract_texts = []
            abstract_el = article.find(".//Article/Abstract")
            if abstract_el is not None:
                for text_el in abstract_el.findall("AbstractText"):
                    label = text_el.get("Label")
                    if label:
                        abstract_texts.append(f"{label}: {text_el.text or ''}")
                    else:
                        abstract_texts.append(text_el.text or "")
            abstract = " ".join([t for t in abstract_texts if t])
            pmid_to_abstract[pmid] = abstract
    except Exception as e:
        logger.error(f"Error parsing PubMed XML: {e}")
    return pmid_to_abstract

def parse_esummary_pubmed(summary_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parses PubMed esummary JSON and returns basic paper metadata."""
    papers = []
    for pmid, details in summary_result.items():
        if pmid == "uids" or not isinstance(details, dict):
            continue
        title = details.get("title", "")
        authors = [a.get("name") for a in details.get("authors", []) if a.get("name")]
        journal = details.get("source", "")
        pubdate = details.get("pubdate", "")
        
        doi = ""
        for aid in details.get("articleids", []):
            if aid.get("idtype") == "doi":
                doi = aid.get("value", "")
                break
        
        papers.append({
            "pmid": pmid,
            "doi": doi,
            "title": title,
            "authors": authors,
            "journal": journal,
            "pubdate": pubdate
        })
    return papers

# --- Deduplicator ---

class PaperDeduplicator:
    """Deduplicates scientific papers by PMID, DOI, and Normalized Title, merging ingestion reasons."""
    def __init__(self):
        self.pmid_map: Dict[str, Dict[str, Any]] = {}
        self.doi_map: Dict[str, Dict[str, Any]] = {}
        self.title_map: Dict[str, Dict[str, Any]] = {}

    def add_paper(self, paper: Dict[str, Any], reason: str, gene: str) -> None:
        def clean_pmid(pmid_val: Any) -> str:
            if pmid_val is None:
                return ""
            s = str(pmid_val).strip()
            if s.lower() == "none" or not s.isdigit():
                return ""
            return s

        def clean_doi(doi_val: Any) -> str:
            if doi_val is None:
                return ""
            s = normalize_doi(doi_val)
            if s.lower() == "none" or not s:
                return ""
            return s

        def clean_title(title_val: Any) -> str:
            if title_val is None:
                return ""
            s = normalize_title(title_val)
            if s.lower() == "none" or not s:
                return ""
            return s

        pmid = clean_pmid(paper.get("pmid"))
        doi = clean_doi(paper.get("doi"))
        title_norm = clean_title(paper.get("title"))

        matching_records = []
        if pmid and pmid in self.pmid_map:
            matching_records.append(self.pmid_map[pmid])
        if doi and doi in self.doi_map:
            matching_records.append(self.doi_map[doi])
        if title_norm and title_norm in self.title_map:
            matched_title_record = self.title_map[title_norm]
            r_pmid = clean_pmid(matched_title_record.get("pmid"))
            r_doi = clean_doi(matched_title_record.get("doi"))
            pmid_conflict = (pmid and r_pmid and pmid != r_pmid)
            doi_conflict = (doi and r_doi and doi != r_doi)
            if not (pmid_conflict or doi_conflict):
                matching_records.append(matched_title_record)

        # Filter to unique record instances
        unique_matching = []
        seen_ids = set()
        for r in matching_records:
            if id(r) not in seen_ids:
                seen_ids.add(id(r))
                unique_matching.append(r)

        new_reason = {"reason": reason, "gene": gene}

        def merge_records(target: Dict[str, Any], source: Dict[str, Any]) -> None:
            for k in ["pmid", "doi", "title", "abstract", "authors", "journal", "pubdate"]:
                target_val = target.get(k)
                source_val = source.get(k)
                if not target_val and source_val:
                    if k == "pmid":
                        cleaned = clean_pmid(source_val)
                    elif k == "doi":
                        cleaned = clean_doi(source_val)
                    else:
                        cleaned = source_val
                    if cleaned:
                        target[k] = cleaned
            target_reasons = target.setdefault("ingestion_reasons", [])
            for r in source.get("ingestion_reasons", []):
                if r not in target_reasons:
                    target_reasons.append(r)

        if unique_matching:
            logger.warning(f"Duplicate paper found. PMID: {pmid}, DOI: {doi}. Resolving by merging reasons.")
            canonical = unique_matching[0]
            for other in unique_matching[1:]:
                merge_records(canonical, other)
            merge_records(canonical, {
                "pmid": pmid,
                "doi": doi,
                "title": paper.get("title", ""),
                "abstract": paper.get("abstract", ""),
                "authors": paper.get("authors", []),
                "journal": paper.get("journal", ""),
                "pubdate": paper.get("pubdate", ""),
                "ingestion_reasons": [new_reason]
            })
        else:
            canonical = {
                "pmid": pmid,
                "doi": doi,
                "title": paper.get("title", ""),
                "abstract": paper.get("abstract", ""),
                "authors": paper.get("authors", []),
                "journal": paper.get("journal", ""),
                "pubdate": paper.get("pubdate", ""),
                "ingestion_reasons": [new_reason]
            }

        # Collect all keys mapping to any of the matched records and the canonical record keys
        all_pmids = set()
        all_dois = set()
        all_titles_norm = set()

        if pmid:
            all_pmids.add(pmid)
        if doi:
            all_dois.add(doi)
        if title_norm:
            all_titles_norm.add(title_norm)

        for r in unique_matching:
            r_pmid = clean_pmid(r.get("pmid"))
            r_doi = clean_doi(r.get("doi"))
            r_title = clean_title(r.get("title"))
            if r_pmid:
                all_pmids.add(r_pmid)
            if r_doi:
                all_dois.add(r_doi)
            if r_title:
                all_titles_norm.add(r_title)

        for k, r in list(self.pmid_map.items()):
            if id(r) in seen_ids:
                all_pmids.add(k)
        for k, r in list(self.doi_map.items()):
            if id(r) in seen_ids:
                all_dois.add(k)
        for k, r in list(self.title_map.items()):
            if id(r) in seen_ids:
                all_titles_norm.add(k)

        # Update canonical record's pmid/doi if they are missing
        if not canonical.get("pmid") and all_pmids:
            canonical["pmid"] = sorted(all_pmids)[0]
        if not canonical.get("doi") and all_dois:
            canonical["doi"] = sorted(all_dois)[0]

        # Register all keys to point to the canonical record
        for k in all_pmids:
            if k:
                self.pmid_map[k] = canonical
        for k in all_dois:
            if k:
                self.doi_map[k] = canonical
        for k in all_titles_norm:
            if k:
                self.title_map[k] = canonical

    def get_unique_papers(self) -> List[Dict[str, Any]]:
        seen = set()
        unique = []
        for m in [self.pmid_map, self.doi_map, self.title_map]:
            for paper in m.values():
                if id(paper) not in seen:
                    seen.add(id(paper))
                    unique.append(paper)
        return unique

# --- Clients ---

class BaseClient:
    """Base API client logic with disk caching and rate limiting."""
    def __init__(self, source_name: str, cache: DiskCache, rate_limit_delay: float = 0.35):
        self.source_name = source_name
        self.cache = cache
        self.rate_limit_delay = rate_limit_delay
        self.last_cache_path = None

    def _request(self, method: str, url: str, endpoint: str, 
                 params: Optional[Dict[str, Any]] = None, 
                 json_data: Optional[Dict[str, Any]] = None,
                 headers: Optional[Dict[str, str]] = None,
                 is_xml: bool = False) -> Any:
        query_params = {}
        if params:
            query_params.update(params)
        if json_data:
            query_params.update({"_post_body": json_data})

        key = self.cache.generate_cache_key(self.source_name, endpoint, query_params)
        self.last_cache_path = self.cache.get_filepath(key)

        # Try to read from cache first
        cached = self.cache.read(self.source_name, endpoint, query_params)
        if cached is not None:
            return cached

        if self.cache.offline_mode:
            raise OfflineCacheMissError(
                f"Offline mode is active. Cache miss for {self.source_name} {endpoint}."
            )

        max_attempts = 4
        backoff_delay = 0.05  # start small to keep tests fast
        
        response = None
        for attempt in range(max_attempts):
            try:
                if self.rate_limit_delay > 0 and attempt == 0:
                    time.sleep(self.rate_limit_delay)

                logger.info(f"Live request: {method} {url} for endpoint {endpoint} (attempt {attempt + 1})")
                if method.upper() == "POST":
                    response = requests.post(url, json=json_data, headers=headers, timeout=30)
                else:
                    response = requests.get(url, params=params, headers=headers, timeout=30)

                response.raise_for_status()
                break
            except requests.exceptions.RequestException as e:
                is_transient = False
                if e.response is not None:
                    status_code = e.response.status_code
                    if status_code == 429 or (500 <= status_code < 600):
                        is_transient = True
                else:
                    is_transient = True

                if is_transient and attempt < max_attempts - 1:
                    logger.warning(f"Transient error occurred on attempt {attempt + 1}: {e}. Retrying in {backoff_delay}s...")
                    time.sleep(backoff_delay)
                    backoff_delay *= 2
                else:
                    raise e
        
        assert response is not None
        if is_xml:
            data = response.text
        else:
            try:
                data = response.json()
            except ValueError:
                data = response.text

        # Write to cache
        self.cache.write(self.source_name, endpoint, query_params, data)
        return data

BaseIngestClient = BaseClient

class OpenTargetsClient(BaseClient):
    """Client for Open Targets Platform GraphQL API."""
    def __init__(self, cache: DiskCache):
        super().__init__("open_targets", cache)
        self.graphql_url = "https://api.platform.opentargets.org/api/v4/graphql"

    def resolve_ensembl_id(self, gene_symbol: str) -> Optional[str]:
        query = """
        query targetSearch($queryString: String!) {
          search(queryString: $queryString, entityNames: ["target"]) {
            hits {
              id
              entity
            }
          }
        }
        """
        try:
            res = self._request(
                "POST", self.graphql_url, "graphql_search",
                json_data={"query": query, "variables": {"queryString": gene_symbol}}
            )
            hits = res.get("data", {}).get("search", {}).get("hits", [])
            for hit in hits:
                if hit.get("entity") == "target":
                    return hit.get("id")
        except Exception as e:
            logger.error(f"Error resolving Ensembl ID for {gene_symbol}: {e}")
        return None

    # ALS disease ids in Open Targets (EFO + MONDO, incl. familial/sporadic subtypes).
    ALS_DISEASE_IDS = {"EFO_0000253", "MONDO_0004976", "EFO_0001356", "EFO_0001357"}

    def get_target_associations(self, ensembl_id: str, disease_id: str = "EFO_0000253") -> Dict[str, Any]:
        # Open Targets v4: Query.target now takes `ensemblId`, and Target.associatedDiseases
        # exposes scored {rows{disease{id name} score}} with no per-disease argument. We pull a
        # page of top associations; populate_opentargets filters to the ALS ids above.
        query = """
        query targetAssociations($geneId: String!) {
          target(ensemblId: $geneId) {
            id
            approvedSymbol
            approvedName
            associatedDiseases(page: {index: 0, size: 200}) {
              count
              rows {
                disease { id name }
                score
              }
            }
          }
        }
        """
        res = self._request(
            "POST", self.graphql_url, "graphql_association",
            json_data={"query": query, "variables": {"geneId": ensembl_id}}
        )
        return res.get("data", {}).get("target", {}) or {}

    def get_evidences(self, ensembl_id: str, disease_id: str = "EFO_0000253") -> List[Dict[str, Any]]:
        # Literature (PMIDs) from europepmc evidence rows for ALS, queried target-side.
        query = """
        query targetDiseaseEvidences($geneId: String!, $efoIds: [String!]!) {
          target(ensemblId: $geneId) {
            evidences(efoIds: $efoIds, datasourceIds: ["europepmc"], size: 50) {
              rows {
                literature
              }
            }
          }
        }
        """
        res = self._request(
            "POST", self.graphql_url, "graphql_evidences",
            json_data={"query": query, "variables": {"geneId": ensembl_id, "efoIds": sorted(self.ALS_DISEASE_IDS)}}
        )
        return res.get("data", {}).get("target", {}).get("evidences", {}).get("rows", [])

    def get_known_drugs(self, ensembl_id: str) -> List[Dict[str, Any]]:
        # Target.knownDrugs was removed; drugAndClinicalCandidates is the replacement.
        # maximumClinicalStage is a STRING enum (e.g. APPROVAL, PHASE_III); the numeric
        # mapping happens in db.populate.
        query = """
        query targetDrugs($geneId: String!) {
          target(ensemblId: $geneId) {
            drugAndClinicalCandidates {
              count
              rows {
                id
                maxClinicalStage
                drug {
                  id
                  name
                  maximumClinicalStage
                  mechanismsOfAction { rows { mechanismOfAction } }
                }
              }
            }
          }
        }
        """
        try:
            res = self._request(
                "POST", self.graphql_url, "graphql_drugs",
                json_data={"query": query, "variables": {"geneId": ensembl_id}}
            )
            return res.get("data", {}).get("target", {}).get("drugAndClinicalCandidates", {}).get("rows", [])
        except Exception as e:
            logger.error(f"Error fetching drugs for target {ensembl_id}: {e}")
            return []

    def fetch_gene_data(self, gene_symbol: str) -> Dict[str, Any]:
        ensembl_id = self.resolve_ensembl_id(gene_symbol)
        if not ensembl_id:
            return {"symbol": gene_symbol, "ensembl_id": None, "association": None, "evidences": [], "drugs": []}
        
        association = self.get_target_associations(ensembl_id)
        evidences = self.get_evidences(ensembl_id)
        drugs = self.get_known_drugs(ensembl_id)
        return {
            "symbol": gene_symbol,
            "ensembl_id": ensembl_id,
            "association": association,
            "evidences": evidences,
            "drugs": drugs
        }

class ClinVarClient(BaseClient):
    """Client for NCBI ClinVar API using Entrez e-utilities."""
    def __init__(self, cache: DiskCache):
        super().__init__("clinvar", cache)
        self.esearch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        self.esummary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

    def get_variants(self, gene_symbol: str, disease_term: str = "amyotrophic lateral sclerosis") -> Dict[str, Any]:
        # ClinVar has no [disease] field index; the [disease] tag yields zero hits. The
        # disease phrase is matched across default fields, which correctly returns ALS variants.
        term = f"{gene_symbol}[gene] AND {disease_term}"
        params = {
            "db": "clinvar",
            "term": term,
            "retmode": "json",
            "retmax": 100
        }
        res = self._request("GET", self.esearch_url, "esearch", params=params)
        id_list = res.get("esearchresult", {}).get("idlist", [])
        if not id_list:
            return {"gene": gene_symbol, "variants": []}
        
        # Sort ClinVar variant IDs before batching to ensure deterministic cache key
        sorted_id_list = sorted(str(i).strip() for i in id_list if i)
        summary_params = {
            "db": "clinvar",
            "id": ",".join(sorted_id_list),
            "retmode": "json"
        }
        summary_res = self._request("GET", self.esummary_url, "esummary", params=summary_params)
        return {
            "gene": gene_symbol,
            "variants": summary_res.get("result", {})
        }

class UniProtClient(BaseClient):
    """Client for UniProt REST API."""
    def __init__(self, cache: DiskCache):
        super().__init__("uniprot", cache)
        self.search_url = "https://rest.uniprot.org/uniprotkb/search"

    def get_gene_details(self, gene_symbol: str) -> Dict[str, Any]:
        params = {
            "query": f"gene:{gene_symbol} AND organism_id:9606",
            "format": "json"
        }
        res = self._request("GET", self.search_url, "search", params=params)
        results = res.get("results", [])
        return results[0] if results else {}

class ReactomeClient(BaseClient):
    """Client for Reactome Content Service API."""
    def __init__(self, cache: DiskCache):
        super().__init__("reactome", cache)
        self.base_url = "https://reactome.org/ContentService"

    def get_pathways_for_uniprot(self, uniprot_id: str) -> List[Dict[str, Any]]:
        # Reactome ContentService: map a UniProt accession to its human pathways.
        # The /data/pathways/for/entity/{id} path 404s; the supported route is
        # /data/mapping/UniProt/{accession}/pathways?species=9606 which returns a JSON
        # list of pathway objects carrying stId (R-HSA...) and displayName.
        url = f"{self.base_url}/data/mapping/UniProt/{uniprot_id}/pathways"
        endpoint = f"mapping_uniprot_pathways_{uniprot_id}"
        params = {"species": 9606}
        try:
            res = self._request("GET", url, endpoint, params=params)
            if isinstance(res, list):
                return res
            return []
        except Exception as e:
            logger.error(f"Error fetching Reactome pathways for {uniprot_id}: {e}")
            return []

class StringClient(BaseClient):
    """Client for STRING-DB protein interaction partners API."""
    def __init__(self, cache: DiskCache, confidence_threshold: float = 0.7, limit: int = 10):
        super().__init__("string", cache)
        self.api_url = "https://string-db.org/api/json/interaction_partners"
        self.confidence_threshold = confidence_threshold
        self.limit = limit

    def get_interactions(self, gene_symbol: str) -> List[Dict[str, Any]]:
        score_val = int(self.confidence_threshold * 1000)
        params = {
            "identifiers": gene_symbol,
            "species": 9606,
            "required_score": score_val,
            "limit": self.limit
        }
        try:
            res = self._request("GET", self.api_url, "interaction_partners", params=params)
            if isinstance(res, list):
                return res
            return []
        except Exception as e:
            logger.error(f"Error fetching STRING interactions for {gene_symbol}: {e}")
            return []

class PubMedClient(BaseClient):
    """Client for NCBI PubMed using Entrez e-utilities."""
    def __init__(self, cache: DiskCache):
        super().__init__("pubmed", cache)
        self.esearch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        self.esummary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        self.efetch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

    def search_pubmed(self, gene: str, limit: int = 10) -> List[str]:
        term = f'("{gene}"[Title/Abstract] OR "{gene}"[All Fields]) AND ("amyotrophic lateral sclerosis"[Title/Abstract] OR ALS[Title/Abstract])'
        params = {
            "db": "pubmed",
            "term": term,
            "retmode": "json",
            "retmax": limit
        }
        res = self._request("GET", self.esearch_url, "esearch", params=params)
        return res.get("esearchresult", {}).get("idlist", [])

    def fetch_summaries(self, pmids: List[str]) -> Dict[str, Any]:
        if not pmids:
            return {}
        # Sort PMID arguments to guarantee deterministic cache keys
        sorted_pmids = sorted(str(p).strip() for p in pmids if p)
        params = {
            "db": "pubmed",
            "id": ",".join(sorted_pmids),
            "retmode": "json"
        }
        res = self._request("GET", self.esummary_url, "esummary", params=params)
        return res.get("result", {})

    def fetch_abstracts(self, pmids: List[str]) -> str:
        if not pmids:
            return ""
        # Sort PMID arguments to guarantee deterministic cache keys
        sorted_pmids = sorted(str(p).strip() for p in pmids if p)
        params = {
            "db": "pubmed",
            "id": ",".join(sorted_pmids),
            "retmode": "xml"
        }
        res = self._request("GET", self.efetch_url, "efetch", params=params, is_xml=True)
        return res

class IngestionClient:
    """Wrapper client for backward compatibility with the tier 1 test suite."""
    def __init__(self, cache: DiskCache):
        self.cache = cache
        self.uniprot = UniProtClient(cache)
        self.reactome = ReactomeClient(cache)
        self.open_targets = OpenTargetsClient(cache)
        self.clinvar = ClinVarClient(cache)
        self.string = StringClient(cache)
        self.pubmed = PubMedClient(cache)

    def fetch_uniprot(self, gene: str) -> Any:
        return self.uniprot.get_gene_details(gene)

    def fetch_reactome(self, gene: str) -> Any:
        url = f"https://reactome.org/ContentService/data/pathways/low/diagram/entity/{gene}/all"
        return self.reactome._request("GET", url, "pathways")

    def fetch_clinvar_search(self, gene: str) -> Any:
        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        params = {"db": "clinvar", "term": f"{gene}[gene]", "retmode": "json"}
        return self.clinvar._request("GET", url, "esearch", params=params)

    def fetch_clinvar_summary(self, ids: List[str]) -> Any:
        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        # Sort IDs for cache determinism
        sorted_ids = sorted(str(i).strip() for i in ids if i)
        params = {"db": "clinvar", "id": ",".join(sorted_ids), "retmode": "json"}
        return self.clinvar._request("GET", url, "esummary", params=params)

    def fetch_opentargets(self, gene_id: str) -> Any:
        url = "https://api.platform.opentargets.org/api/v4/graphql"
        query = """
        query target($geneId: String!) {
          target(id: $geneId) {
            id
            approvedSymbol
            associatedDiseases {
              rows {
                disease {
                  id
                  name
                }
                score
              }
            }
          }
        }
        """
        json_data = {"query": query, "variables": {"geneId": gene_id}}
        return self.open_targets._request("POST", url, "graphql", json_data=json_data)

    def fetch_string(self, genes: List[str]) -> Any:
        url = "https://string-db.org/api/json/network"
        params = {"identifiers": "%0d".join(genes), "species": 9606}
        return self.string._request("GET", url, "network", params=params)

    def fetch_pubmed_search(self, term: str) -> Any:
        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        params = {"db": "pubmed", "term": term, "retmode": "json"}
        return self.pubmed._request("GET", url, "esearch", params=params)

    def fetch_pubmed_summary(self, ids: List[str]) -> Any:
        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        # Sort IDs for cache determinism
        sorted_ids = sorted(str(i).strip() for i in ids if i)
        params = {"db": "pubmed", "id": ",".join(sorted_ids), "retmode": "json"}
        return self.pubmed._request("GET", url, "esummary", params=params)
