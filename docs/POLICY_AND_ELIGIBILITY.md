# Policy- und Eligibility-Regeln

## Zentrale Grenze

`retrieval.eligible_retrieval_units` ist eine PostgreSQL-Security-Barrier-View.
Sie verlangt gleichzeitig:

- versiegelten Snapshot;
- `eligibility_status='eligible'`;
- alle vier Freigabeflags für Retrieval, Embedding, Antwort und Primärsuche;
- `excluded_by_policy=false`;
- keinen HCC-History-Ausschlussgrund;
- einen ebenfalls eligible und nicht ausgeschlossenen Parent-Evidence-Span.

Die SQL-Funktionen `search_exact`, `search_lexical`, `search_trigram`,
`search_vector_exact`, `expand_relations` und `evidence_package_rows` verwenden
ausnahmslos diese View. Der Validator prüft ihre Definitionen im laufenden
Datenbankkatalog.

## HCC/BCC-History

99 historische Vergleichs-/Änderungstabellenrecords bleiben für Audit und
Provenienz kanonisch erhalten, sind aber in allen normalen Pfaden gesperrt.
Geprüftes Leakage in Retrieval-Units, Embeddings, Relationen, FTS, Trigramm,
Vector und Evidence Packages: 0.

## Konsultationsfassung

Dateiname, PDF-Metadaten und der Text auf PDF-Seite 1 kennzeichnen die
HCC/BCC-Quelle als nicht final autorisierte Konsultationsfassung. Sie bleibt
`source_status=consultation_draft`. Retrieval darf diese Quelle finden, muss den
Status aber sichtbar weitergeben und darf sie nicht still als finale aktuelle
Leitlinie rendern.

## Quellenrollen und Routing

- `guideline`: Empfehlung, Statement, Evidenzgrad, Rationale.
- `smPC`: zugelassene Indikation, Dosierung, Zubereitung, Warnung,
  Gegenanzeige, Nebenwirkung.
- `guideline_first`: Empfehlungs- und Leitlinienfragen.
- `smpc_first`: Dosierung, Zubereitung, Route, Gegenanzeigen und Sicherheit.
- `dual_source`: Mapping-, Versions- und Konfliktfragen.

Ein Unterschied zwischen Leitlinie und SmPC ist weder automatisch ein
Extraktionsfehler noch still aufzulösen. Dual-source-Pakete tragen deshalb den
Hinweis `guideline_and_smpc_evidence_not_silently_reconciled`; eine spätere
Claimprüfung muss `conflict_status` explizit setzen.

## Evidence- und Claim-Policy

Evidence Packages sind Snapshot-Allowlisten. Unbekannte, ausgeschlossene,
snapshotfremde oder paketfremde IDs werden verworfen. Backend-Citations stammen
ausschließlich aus Snapshotmetadaten.

Öffentliche Labels:

- `supported`
- `partially_supported`
- `no_validated_evidence`

Interne Achsen:

- Entailment: `supported | partial | contradicted | insufficient`
- Retrieval: `evidence_found | retrieval_failure | no_evidence_in_snapshot`
- Konflikt: `none | guideline_vs_smpc | within_guideline | version_conflict`
- Anwendbarkeit: `applicable | uncertain | not_applicable`

`no_validated_evidence` ist nur nach vollständig abgeschlossenem Fallback und
`no_evidence_in_snapshot` zulässig. Ein Kanalfehler ergibt
`retrieval_failure`, ein Widerspruch `contradicted`; beide werden nicht als
fehlende Evidenz umetikettiert. Modell-Selbstvertrauen ist kein Kriterium.
