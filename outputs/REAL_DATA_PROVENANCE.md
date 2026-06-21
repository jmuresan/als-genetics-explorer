# REAL DATA PROVENANCE: ALS Genetic Mechanism Explorer

**Status:** the knowledge graph is populated from live public APIs. No hardcoded, hand-written,
mocked, or LLM-generated biological records exist in the production code or data. A gene, variant,
interaction, pathway, paper, or drug row derives from a cached API response under `data/raw/cache/`,
and the DuckDB rebuilds to the same shape from a clean (wiped) cache, which shows the records were
not seeded by hand.

Generated 2026-06-21. DB: `data/processed/als_genetics.duckdb`. Panel: 46 genes (the 41 carrying
UniProt keyword KW-0036, plus 5 ALS genes from the OMIM ALS series PS105400 / GeneReviews that
UniProt does not keyword-tag: CFAP410, MOBP, SCFD1, TAF15, UNC13A).

## Data sources (live, no paid keys)

| Source | Endpoint | What it populates |
|---|---|---|
| UniProt REST | `rest.uniprot.org/uniprotkb/search` | gene to protein accession, name |
| STRING | `string-db.org/api/json/interaction_partners` | protein interaction edges |
| NCBI Entrez (ClinVar) | `eutils.ncbi.nlm.nih.gov` esearch + esummary (db=clinvar) | classified variants |
| NCBI Entrez (PubMed) | `eutils.ncbi.nlm.nih.gov` esearch + esummary + efetch (db=pubmed) | literature, citations |
| Reactome | `reactome.org/ContentService/data/mapping/UniProt/{acc}/pathways` | pathway membership |
| Open Targets | `api.platform.opentargets.org/api/v4/graphql` | ALS disease association, known drugs |

Rate limiting: under 3 requests per second (per-request delay plus exponential backoff on 429/5xx).
Raw responses are cached under `data/raw/cache/`, keyed by `sha256(source, endpoint, params)`.

## Per-source row counts (production DuckDB)

| Table | Rows | Source |
|---|---:|---|
| genes | 46 | UniProt |
| interactions | 405 | STRING (confidence >= 0.7) |
| variants | 2,424 | ClinVar (across the 46 genes) |
| pathways (distinct R-HSA) | 163 | Reactome |
| gene_pathways | 207 | Reactome |
| disease_associations | 189 (92 ALS-specific) | Open Targets |
| drugs | 110 (real ChEMBL ids) | Open Targets |
| gene_drugs | 110 | Open Targets |
| papers | 362 (244 distinct PMIDs cited; 0 placeholders) | PubMed |
| claims | 11,419 | derived |
| hypotheses | 589 (a hypothesis cites >= 1 real PMID; 244 distinct PMIDs across the set) | derived |
| hypothesis_evidence | 3,529 | derived |

## 5 spot-checked records (DB value confirmed against the live source)

Each was re-queried live and matches the stored DuckDB value.

1. **Gene to UniProt accession.** DB: `SOD1` to `P00441`, "Superoxide dismutase [Cu-Zn]".
   Live: <https://rest.uniprot.org/uniprotkb/P00441.json> returns geneName `SOD1`. Match.

2. **Variant to ClinVar.** DB: `VCV002059610` (gene SOD1), clinical_significance "Likely pathogenic".
   Live: <https://www.ncbi.nlm.nih.gov/clinvar/variation/2059610/>, germline_classification
   "Likely pathogenic". Match.

3. **Interaction to STRING.** DB: `CCS` and `SOD1`, confidence `0.999`.
   Live: <https://string-db.org/api/json/interaction_partners?identifiers=SOD1&species=9606&required_score=700>
   lists partner `CCS` at `0.999`. Match.

4. **Pathway to Reactome.** DB: `R-HSA-3299685` "Detoxification of Reactive Oxygen Species" linked to SOD1.
   Live: <https://reactome.org/ContentService/data/mapping/UniProt/P00441/pathways?species=9606>
   contains stId `R-HSA-3299685`. Match.

5. **Drug and disease association to Open Targets.** DB: SOD1 to drug `CHEMBL3833346` TOFERSEN
   (clinical phase 4.0); disease_association SOD1 to `MONDO_0004976` score `0.8701`.
   Live: target `ENSG00000142168` returns drugAndClinicalCandidates `CHEMBL3833346` TOFERSEN at stage
   `APPROVAL`, and associatedDiseases `MONDO_0004976` at `0.8701`. Match.
   Drug page: <https://platform.opentargets.org/drug/CHEMBL3833346>.

Bonus, paper to PubMed: DB paper `PMID 20301623` ("Amyotrophic Lateral Sclerosis Overview")
resolves at <https://pubmed.ncbi.nlm.nih.gov/20301623/>.

## Verification gate

`gate_check.py` passes 8 of 8 core checks against this DuckDB: 46 genes with real UniProt
accessions; 405 STRING edges at or above the threshold with a partner per gene; 2,424 ClinVar
variants across the panel with real VCV ids and controlled-vocabulary significance; 163 distinct
Reactome pathways with gene links; a real Open Targets ALS association (MONDO_0004976) for the panel;
110 drugs with ChEMBL ids and clinical phase, including TOFERSEN (`CHEMBL3833346`, approved,
SOD1-targeted); 362 papers with 244 distinct resolvable PMIDs and 0 placeholders; and hypotheses
that cite 244 distinct PMIDs. A clean-cache re-run rebuilds the DuckDB to the same shape.

Open Targets `drugAndClinicalCandidates` returns the drugs molecularly target-linked to a panel gene,
so riluzole and edaravone (which act on glutamate and free-radical pathways, not these proteins) do
not appear. The mapped set is what Open Targets returns for these targets.

## Reproduce from scratch

```bash
cd als-genetics-explorer && source .venv/bin/activate
find data/raw/cache -name '*.json' -delete
rm -f data/processed/als_genetics.duckdb data/processed/deduplicated_papers.json
ALS_DB_PATH=data/processed/als_genetics.duckdb python3 -m src.pipeline.run_all --config config.yaml
python3 gate_check.py
```

The first run fetches live and caches. A later run replays from `data/raw/cache/` (set
`offline_mode: true` in `config.yaml` to force cache-only).
