import pytest
import os
import json
from unittest.mock import MagicMock

class BlockedNetworkError(RuntimeError):
    pass

def mock_response(json_data, status_code=200):
    mock_res = MagicMock()
    mock_res.status_code = status_code
    mock_res.json.return_value = json_data
    mock_res.text = json.dumps(json_data)
    mock_res.raise_for_status = MagicMock()
    return mock_res

def mock_response_xml(xml_content, status_code=200):
    mock_res = MagicMock()
    mock_res.status_code = status_code
    mock_res.json.side_effect = ValueError("Not JSON")
    mock_res.text = xml_content
    mock_res.raise_for_status = MagicMock()
    return mock_res

@pytest.fixture(autouse=True)
def mock_requests_layer(monkeypatch):
    """Mocks requests get/post/request to intercept biological API calls."""
    
    def mock_get(url, params=None, **kwargs):
        url_lower = url.lower()
        
        # 1. UniProt
        if "uniprotkb/search" in url_lower:
            data = {
                "results": [
                    {
                        "primaryAccession": "P00441",
                        "uniProtkbId": "SODC_HUMAN",
                        "genes": [{"geneName": {"value": "SOD1"}}],
                        "proteinDescription": {"recommendedName": {"fullName": {"value": "Superoxide dismutase"}}}
                    }
                ]
            }
            return mock_response(data)
            
        # 2. Reactome
        elif "reactome.org" in url_lower:
            data = [
                {
                    "stId": "R-HSA-9711123",
                    "displayName": "Amyotrophic lateral sclerosis (ALS)",
                    "literatureReference": [{"pubId": "31567890", "title": "Reactome paper"}]
                }
            ]
            return mock_response(data)
            
        # 3. NCBI E-Utilities (esearch, esummary, efetch)
        elif "esearch.fcgi" in url_lower:
            db = params.get("db") if params else None
            term = params.get("term", "") if params else ""
            if db == "clinvar":
                data = {"esearchresult": {"idlist": ["8877"]}}
            elif db == "pubmed":
                data = {"esearchresult": {"idlist": ["31567890"]}}
            else:
                data = {"esearchresult": {"idlist": []}}
            return mock_response(data)
            
        elif "esummary.fcgi" in url_lower:
            db = params.get("db") if params else None
            if db == "clinvar":
                data = {
                    "result": {
                        "8877": {
                            "uid": "8877",
                            "clinical_significance": {"description": "Pathogenic"},
                            "trait_set": [{"trait_name": "Amyotrophic lateral sclerosis"}]
                        }
                    }
                }
            elif db == "pubmed":
                data = {
                    "result": {
                        "31567890": {
                            "uid": "31567890",
                            "title": "SOD1 study",
                            "articleids": [{"idtype": "doi", "value": "10.1038/nature123"}],
                            "sortpubdate": "2019-10-01",
                            "authors": [{"name": "Author A"}],
                            "source": "Nature"
                        }
                    }
                }
            else:
                data = {"result": {}}
            return mock_response(data)
            
        elif "efetch.fcgi" in url_lower:
            xml_content = """<?xml version="1.0" encoding="UTF-8"?>
            <PubmedArticleSet>
              <PubmedArticle>
                <MedlineCitation>
                  <PMID>31567890</PMID>
                </MedlineCitation>
                <Article>
                  <Abstract>
                    <AbstractText>Mock abstract for SOD1 study.</AbstractText>
                  </Abstract>
                </Article>
              </PubmedArticle>
            </PubmedArticleSet>
            """
            return mock_response_xml(xml_content)
            
        # 4. STRING
        elif "string-db.org" in url_lower:
            data = [
                {"stringId_A": "9606.ENSP00000270142", "stringId_B": "9606.ENSP00000263967", "preferredName_A": "SOD1", "preferredName_B": "CCS", "score": 0.999}
            ]
            return mock_response(data)
            
        raise BlockedNetworkError(f"Hermetic mock blocked GET request to: {url} with params {params}")

    def mock_post(url, json=None, **kwargs):
        url_lower = url.lower()
        if "api.platform.opentargets.org" in url_lower:
            query = json.get("query", "") if json else ""
            if "targetSearch" in query or "target($geneId" in query:
                data = {
                    "data": {
                        "search": {
                            "hits": [{"id": "ENSG00000091409", "entity": "target"}]
                        }
                    }
                }
            elif "targetAssociations" in query or "associatedDiseases" in query:
                data = {
                    "data": {
                        "target": {
                            "id": "ENSG00000091409",
                            "approvedSymbol": "SOD1",
                            "approvedName": "Superoxide dismutase 1",
                            "associatedDiseases": {
                                "rows": [
                                    {
                                        "disease": {"id": "EFO_0000253", "name": "amyotrophic lateral sclerosis"},
                                        "score": 0.85
                                    }
                                ]
                            }
                        }
                    }
                }
            elif "targetDiseaseEvidences" in query:
                data = {
                    "data": {
                        "target": {
                            "evidences": {
                                "rows": [{"literature": ["31567890"]}]
                            }
                        }
                    }
                }
            elif "targetDrugs" in query:
                data = {
                    "data": {
                        "target": {
                            "knownDrugs": {
                                "rows": [
                                    {
                                        "drug": {
                                            "id": "CHEMBL123",
                                            "name": "Test Drug",
                                            "maximumClinicalTrialPhase": 4.0
                                        },
                                        "mechanismOfAction": "Inhibitor"
                                    }
                                ]
                            }
                        }
                    }
                }
            else:
                data = {"data": {}}
            return mock_response(data)
            
        raise BlockedNetworkError(f"Hermetic mock blocked POST request to: {url} with json {json}")

    def mock_request(method, url, **kwargs):
        if method.upper() == "POST":
            return mock_post(url, **kwargs)
        return mock_get(url, **kwargs)

    monkeypatch.setattr("requests.get", mock_get)
    monkeypatch.setattr("requests.post", mock_post)
    monkeypatch.setattr("requests.request", mock_request)

@pytest.fixture
def mock_response_fixture():
    """Helper fixture to create mock response objects manually in tests."""
    return mock_response

@pytest.fixture
def temp_cache_dir(tmp_path):
    """Fixture providing a clean temporary directory for cache testing."""
    return str(tmp_path / "cache")
