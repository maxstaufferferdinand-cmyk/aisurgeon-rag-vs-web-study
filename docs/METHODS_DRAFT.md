# Paper Methods Draft – technische Retrievalphase

## Ziel und Design

Wir entwickelten und validierten offline eine local-first,
provenance-erhaltende Hybrid-Retrieval-Infrastruktur zur gemeinsamen Abfrage
deutscher klinischer Leitlinien und Arzneimittelinformationen. Die Phase
untersuchte technische Reproduzierbarkeit, Policy-Isolation, Retrievalkanäle
und Evidenzverträge; sie stellt keine klinische Validierung dar.

## Korpus und Snapshot

Der Ausgangskorpus bestand aus drei Leitlinien- und neun
Arzneimittelinformations-PDFs (2.060 Seiten). Gemini war ausschließlich im
vorherigen Arbeitsschritt Offline-PDF-Extractor für diese öffentlichen
Dokumente und wurde in der Retrievalphase nicht erneut aufgerufen. Der aktuelle
kanonische Stand umfasst 7.306 nicht aggregierte Records und 4.469 aktive
Retrieval-Einheiten. Ein deterministischer Snapshot bindet relative Pfade,
SHA-256 aller PDFs und Canonical-Dateien, Schema-/Pipelineversionen, Counts,
Eligibilitystatistiken, QA-Einschränkungen und Vorgängersnapshot.

## Datenbank

PostgreSQL 18.6 und pgvector 0.8.6 liefen in einem digest-gepinnten Container
mit Loopback-Portbindung und persistentem lokalem Volume. Neun hashregistrierte
Migrationen erzeugten Provenienz-, Retrieval-, Embedding-, Claim- und
Telemetrietabellen. Die kanonischen JSONL-Dateien blieben Source of Truth; die
Datenbank war ein regenerierbarer Index. Ein transaktionaler idempotenter Import
prüfte Counts, Text-/Source-Hashes, Foreign Keys, Locator, Relationen,
Eligibility, Exclusions und Snapshotzuordnung.

## Retrieval

Wir kombinierten (i) deterministische Item-/Alias-/Struktursuche, (ii)
PostgreSQL-FTS mit getrennten `german`- und `simple`-Repräsentationen, (iii)
`pg_trgm` für Schreibvarianten und (iv) exakte Cosinus-Suche über pgvector.
Vektor-ANN-Indizes wurden nicht verwendet. Die 1.536-dimensionalen
`text-embedding-3-small`-Embeddings wurden in sequenziellen Batches erstellt,
sofort validiert, lokal checkpointed und per Text-/Quellhash an den Snapshot
gebunden. Ein Drei-Einheiten-Smoke ging der Vollbaseline voraus.

Kandidatenlisten wurden rangbasiert mit Reciprocal Rank Fusion verbunden. Der
vorläufige technische Parameter war k=60; inkompatible Rohscores wurden nicht
addiert. Routing war `guideline_first`, `smpc_first` oder `dual_source`.
Typisierte, budgetierte Relationsexpansion kennzeichnete verbundenen Kontext
separat von direkt gerankter Evidenz.

## Policy und Evidenzvertrag

Eine zentrale Security-Barrier-View schloss alle nicht eligible Einheiten aus.
99 historische HCC/BCC-Records blieben auditierbar, erreichten aber keinen
normalen Such-, Embedding-, Relations- oder Evidence-Package-Pfad. Die
HCC/BCC-Konsultationsfassung trug explizit den Status `consultation_draft`.
Leitlinien- und SmPC-Rollen wurden getrennt geroutet; Unterschiede wurden nicht
stillschweigend aufgelöst.

Das Backend erzeugte eine snapshotgebundene Evidence-Allowlist und renderte
Dokumentname, Version, Status, Seite und Link. Ein deterministischer Claim-
Validator prüfte Evidence-IDs, Locator, Dokumentstatus, Dosis, Einheit,
Frequenz, Route, Population und Negation. Öffentliche Labels waren `supported`,
`partially_supported` und `no_validated_evidence`; interne Achsen trennten
Entailment, Retrievaloutcome, Konflikt und Anwendbarkeit.

## Externe Schritte und Datenschutzgrenze

OpenAI wurde extern ausschließlich für die Corpus-Embeddings, einen
synthetischen semantischen Query-Smoke und einen kleinen Structured-Output-
Vertragstest mit öffentlichen Evidenzeinheiten aufgerufen. Es wurden keine
Patientendaten verarbeitet. Vollständige Nutzerfragen oder Antworten wurden
standardmäßig nicht protokolliert; lokale Telemetrie speicherte Hashes, IDs,
Ränge, Tokens, Kostenzeitpunkt, Latenz, Retry-/Fehlercodes, Validatorstatus und
lokal gemessene CPU-/RAM-/I/O-Werte.

## Technische Evaluation

Wir prüften einen vollständigen Volume-Rebuild, Migrations-/Importidempotenz,
Embedding-Resume ohne neue API-Kosten, alle Retrievalkanäle, RRF, Routing,
Relationsexpansion, Kanalausfall, HCC-Leakage und Claimverträge. Zusätzlich
erzeugten wir ein stratifiziertes, blindierbares Human-Annotation-Package mit 50
Development- und 250 versiegelten Testslots, davon 25 % No-evidence/
Out-of-scope. Automatische Fragen wurden ausschließlich als `synthetic_draft`
markiert; klinische Goldfelder blieben leer.
