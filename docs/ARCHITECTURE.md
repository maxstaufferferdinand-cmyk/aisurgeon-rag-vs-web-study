# Architektur und Datenmodell

## Systemgrenze

AISurgeon Decentralised ist in dieser Phase eine lokal kontrollierte
Retrieval-Forschungsinfrastruktur. Kanonische JSONL-Dateien und öffentliche
Quell-PDFs sind dauerhaft maßgeblich. PostgreSQL/pgvector kann vollständig aus
dem Snapshot und den Embedding-Checkpoints neu aufgebaut werden.

```mermaid
flowchart LR
    PDF[12 öffentliche Quell-PDFs] --> CAN[kanonische JSONL\nSource of Truth]
    CAN --> SNAP[unveränderlicher\nCorpus Snapshot]
    SNAP --> IMP[idempotenter Import]
    SNAP --> EMB[text-embedding-3-small\nsequenziell + Checkpoints]
    IMP --> PG[(PostgreSQL 18.6\npgvector 0.8.6)]
    EMB --> PG
    PG --> GATE[eligible_retrieval_units]
    GATE --> EX[Exakt/Alias]
    GATE --> FTS[German/simple FTS]
    GATE --> TRI[pg_trgm]
    GATE --> VEC[exakter Vektorscan]
    EX --> RRF[RRF]
    FTS --> RRF
    TRI --> RRF
    VEC --> RRF
    RRF --> ROUTE[Quellenrouting]
    ROUTE --> REL[typisierte Relationsexpansion]
    REL --> PKG[Evidence-Paket + Allowlist]
    PKG --> CLAIM[deterministischer Claim-Vertrag]
    CLAIM --> CITE[Backend-Citation-Rendering]
    RRF --> TRACE[lokale datensparsame Telemetrie]
    CLAIM --> TRACE
```

## ER-Modell

```mermaid
erDiagram
    CORPUS_SNAPSHOT ||--o{ CORPUS_SNAPSHOT_SOURCE : contains
    SOURCE_DOCUMENT ||--o{ SOURCE_VERSION : versions
    SOURCE_VERSION ||--o{ CORPUS_SNAPSHOT_SOURCE : frozen_in
    CORPUS_SNAPSHOT ||--o{ CORPUS_ARTIFACT : hashes
    CORPUS_SNAPSHOT ||--o{ CANONICAL_RECORD : indexes
    SOURCE_VERSION ||--o{ CANONICAL_RECORD : provenance
    CANONICAL_RECORD ||--o{ EVIDENCE_SPAN : spans
    SOURCE_VERSION ||--o{ EVIDENCE_SPAN : locates
    CORPUS_SNAPSHOT ||--o{ RETRIEVAL_UNIT : releases
    EVIDENCE_SPAN ||--o| RETRIEVAL_UNIT : parent
    CANONICAL_RECORD ||--o| FORMAL_ITEM : specializes
    CORPUS_SNAPSHOT ||--o{ MEDICINE_PRODUCT : validates
    CORPUS_SNAPSHOT ||--o{ ACTIVE_SUBSTANCE : validates
    CORPUS_SNAPSHOT ||--o{ ENTITY_REFERENCE : preserves
    CORPUS_SNAPSHOT ||--o{ SEMANTIC_RELATION : types
    RETRIEVAL_UNIT ||--o{ RETRIEVAL_EMBEDDING : embeds
    CORPUS_SNAPSHOT ||--o{ RETRIEVAL_RUN : queries
    RETRIEVAL_RUN ||--o{ RETRIEVAL_CANDIDATE : ranks
    RETRIEVAL_RUN ||--o| EVIDENCE_PACKAGE : builds
    EVIDENCE_PACKAGE ||--o{ ANSWER_CLAIM : constrains
    ANSWER_CLAIM ||--o{ CLAIM_EVIDENCE : cites
    RETRIEVAL_UNIT ||--o{ CLAIM_EVIDENCE : supports
    CORPUS_SNAPSHOT ||--o{ RETRIEVAL_TRACE : measures
```

## Vertrauensgrenzen

1. Die Snapshot-Erzeugung verifiziert Quell- und Canonical-Hashes vor jeder
   Verwendung.
2. Die Security-Barrier-View `retrieval.eligible_retrieval_units` ist der
   einzige normale Such-Gateway. Alle sechs SQL-Funktionen bauen darauf auf.
3. Relationsexpansion beginnt nur bei hoch gerankten Seeds, ist typisiert,
   limitiert und kennzeichnet Ergebnisse als `linked_context`.
4. Das Evidence-Paket bindet Snapshot, ID-Reihenfolge, Text-Hashes und Locator
   kryptografisch. Unbekannte, fremde oder ausgeschlossene IDs scheitern
   fail-closed.
5. Ein Sprachmodell kann nur Supportstatus und Allowlist-IDs liefern. Citation-
   Metadaten werden nie vom Modell übernommen.

Die aktuelle Implementierung ist eine technische Forschungsbaseline, keine
klinische Sicherheitsarchitektur.
