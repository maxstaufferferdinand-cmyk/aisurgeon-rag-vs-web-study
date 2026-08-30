# Evaluationsprotokoll

## Zwei getrennte Ebenen

### Technische Tests

Automatisiert geprüft werden unter anderem Itemnummern, Produkt-/Wirkstoffalias,
Dosis/Einheit, Tippfehler, German/simple FTS, semantische Paraphrase, Routing,
Konsultationsstatus, HCC-Leakage, Evidence-Allowlist, falsche Locator,
Embedding-Resume, vollständiger DB-Rebuild und kontrollierter Kanalausfall.

Diese Tests belegen technische Verträge und Reproduzierbarkeit. Sie messen
keine klinische Güte.

### Human-Goldstandard-Vorbereitung

Das Package `hap-13fca362767b85a7947e135e` bindet den aktuellen Snapshot und
enthält:

- 50 Development-Slots;
- 250 versiegelte, für Entwicklung gesperrte Testslots;
- 25 Strata mit je 2 Dev- und 10 Testfragen;
- 25 % No-evidence-/Out-of-scope-Kandidaten;
- HCC-History-Canaries, Consultation-Draft-Probes, Mehrturn-, Dual-source-,
  Konflikt-, Negations-, Tippfehler- und Near-neighbour-Fälle;
- zwei unabhängig sortierte Reviewer-Templates und ein Adjudikationsschema;
- JSON-Schemas, CSV/JSONL-Blindexporte und ein Sampling-Manifest mit Hashes.

Alle 300 Fragen sind `synthetic_draft`. Alle klinischen Goldfelder sind leer.
Seeds dienen nur reproduzierbarem Sampling und sind kein Goldbeleg. Finale
Labels erfordern unabhängige Human-Annotation und Adjudikation.

Rebuild:

```bash
uv run python scripts/build_annotation_package.py \
  --snapshot-manifest outputs/knowledge_corpus/manifests/corpus_snapshots/cs-f61b3d4e90089c1b890c23cb.json \
  --output-dir outputs/retrieval_phase/evaluation
```

## Präregistrierter Metrikplan

Ranking:

- Evidence Recall@5/@10/@20
- MRR
- nDCG@5/@10/@20
- Precision@k
- vollständige Multi-Evidence-Coverage@k

Citation und Attribution:

- Citation Precision und Completeness
- Source-span Sufficiency
- Entity Attribution Accuracy
- exakte Dosisgenauigkeit einschließlich Wert, Einheit, Intervall und Route

Claims/Abstention:

- Macro-F1 der drei öffentlichen Supportlabels
- richtige und falsche Enthaltung
- Unsupported-Claim-Rate
- potenziell schädliche Unsupported-Claim-Rate
- Exclusion Leakage Rate

Operations:

- Stabilität der Top-10 über Wiederholungen
- p50/p95/p99-Latenz
- Input-, Output-, Cached- und Embeddingtokens
- Kostenlast mit Preislistenzeitpunkt

Auswertung ist fail-closed: nicht adjudizierte oder `synthetic_draft`-Goldfelder
werden nicht als unabhängige Validierung akzeptiert.

```bash
uv run python scripts/evaluate_retrieval.py \
  --input adjudicated_results.jsonl \
  --output evaluation_metrics.json
```
