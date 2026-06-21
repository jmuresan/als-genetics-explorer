import duckdb
import json

db_path = "data/processed/als_genetics.duckdb"
conn = duckdb.connect(db_path)

print("--- TABLES IN DATABASE ---")
tables = conn.execute("SHOW TABLES").fetchall()
print(f"Total tables: {len(tables)}")
for t in tables:
    print(f"  - {t[0]}")

print("\n--- TABLE ROW COUNTS ---")
for t in tables:
    count = conn.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
    print(f"  - {t[0]}: {count}")

print("\n--- SAMPLE INGESTION LOG ---")
logs = conn.execute("SELECT source_name, status, record_count, cache_path FROM ingestion_log LIMIT 5").fetchall()
for log in logs:
    print(f"  - Source: {log[0]}, Status: {log[1]}, Records: {log[2]}, Cache: {log[3]}")

print("\n--- SAMPLE CLAIMS ---")
claims = conn.execute("SELECT claim_id, paper_id, subject, predicate, object, evidence_level FROM claims LIMIT 5").fetchall()
for c in claims:
    print(f"  - ID: {c[0]}, Paper: {c[1]}, Subject: {c[2]}, Predicate: {c[3]}, Object: {c[4]}, Evidence: {c[5]}")

print("\n--- CHECK FOR DEDUPLICATED PAPERS ---")
# Papers that have ingestion_reason with commas (merged reasons)
merged_papers = conn.execute("SELECT pmid, doi, title, ingestion_reason FROM papers WHERE ingestion_reason LIKE '%,%' LIMIT 5").fetchall()
print(f"Merged/Deduplicated papers count: {len(merged_papers)}")
for p in merged_papers:
    print(f"  - PMID: {p[0]}, DOI: {p[1]}, Title: {p[2][:50]}, Reason: {p[3]}")

print("\n--- SAMPLE PAPERS ---")
papers = conn.execute("SELECT pmid, doi, title, ingestion_reason FROM papers LIMIT 5").fetchall()
for p in papers:
    print(f"  - PMID: {p[0]}, DOI: {p[1]}, Title: {p[2][:50]}, Reason: {p[3]}")

conn.close()
