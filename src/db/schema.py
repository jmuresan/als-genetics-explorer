import duckdb

def create_tables(conn: duckdb.DuckDBPyConnection):
    # Ingestion log table
    conn.execute("""
    CREATE TABLE IF NOT EXISTS ingestion_log (
        source_name VARCHAR,
        query_params VARCHAR,
        status VARCHAR,
        record_count INTEGER,
        cache_path VARCHAR,
        error_message VARCHAR,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Genes table
    conn.execute("""
    CREATE TABLE IF NOT EXISTS genes (
        ensembl_id VARCHAR PRIMARY KEY,
        gene_symbol VARCHAR,
        uniprot_id VARCHAR,
        protein_description VARCHAR
    )
    """)

    # Variants table
    conn.execute("""
    CREATE TABLE IF NOT EXISTS variants (
        variant_id VARCHAR PRIMARY KEY,
        gene_symbol VARCHAR,
        clinical_significance VARCHAR,
        disease_name VARCHAR
    )
    """)

    # Disease associations table
    conn.execute("""
    CREATE TABLE IF NOT EXISTS disease_associations (
        gene_symbol VARCHAR,
        disease_id VARCHAR,
        disease_name VARCHAR,
        score DOUBLE,
        PRIMARY KEY (gene_symbol, disease_id)
    )
    """)

    # Pathways table
    conn.execute("""
    CREATE TABLE IF NOT EXISTS pathways (
        pathway_id VARCHAR PRIMARY KEY,
        pathway_name VARCHAR
    )
    """)

    # Gene-pathway mappings
    conn.execute("""
    CREATE TABLE IF NOT EXISTS gene_pathways (
        gene_symbol VARCHAR,
        pathway_id VARCHAR,
        PRIMARY KEY (gene_symbol, pathway_id)
    )
    """)

    # Interactions table
    conn.execute("""
    CREATE TABLE IF NOT EXISTS interactions (
        gene_a VARCHAR,
        gene_b VARCHAR,
        confidence_score DOUBLE,
        PRIMARY KEY (gene_a, gene_b)
    )
    """)

    # Papers table
    conn.execute("""
    CREATE TABLE IF NOT EXISTS papers (
        pmid VARCHAR PRIMARY KEY,
        doi VARCHAR,
        title VARCHAR,
        abstract TEXT,
        pub_date VARCHAR,
        ingestion_reason VARCHAR
    )
    """)

    # Claims table
    conn.execute("""
    CREATE TABLE IF NOT EXISTS claims (
        claim_id VARCHAR PRIMARY KEY,
        paper_id VARCHAR,
        subject VARCHAR,
        predicate VARCHAR,
        object VARCHAR,
        evidence_level VARCHAR
    )
    """)

    # Hypotheses table
    conn.execute("""
    CREATE TABLE IF NOT EXISTS hypotheses (
        hypothesis_id VARCHAR PRIMARY KEY,
        title VARCHAR,
        description TEXT,
        confidence VARCHAR,
        hypothesis_type VARCHAR
    )
    """)

    # Hypothesis evidence table
    conn.execute("""
    CREATE TABLE IF NOT EXISTS hypothesis_evidence (
        hypothesis_id VARCHAR,
        pmid VARCHAR,
        claim_id VARCHAR DEFAULT NULL,
        relationship_type VARCHAR DEFAULT 'supports',
        PRIMARY KEY (hypothesis_id, pmid)
    )
    """)

    # Drugs table
    conn.execute("""
    CREATE TABLE IF NOT EXISTS drugs (
        drug_id VARCHAR PRIMARY KEY,
        name VARCHAR,
        mechanism_of_action VARCHAR,
        max_clinical_phase DOUBLE
    )
    """)

    # Gene-drug mappings table
    conn.execute("""
    CREATE TABLE IF NOT EXISTS gene_drugs (
        gene_symbol VARCHAR,
        drug_id VARCHAR,
        PRIMARY KEY (gene_symbol, drug_id)
    )
    """)

