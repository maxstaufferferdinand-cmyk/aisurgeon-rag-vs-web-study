# AISurgeon Decentralised – dauerhafte Arbeitsregeln

## Wissenschaftlicher Rahmen

- Dieses Repository ist ein Offline-Forschungsprototyp und kein klinisch validiertes Medizinprodukt.
- Zulässige Beschreibung: lokal kontrollierte, provenance-erhaltende Retrieval-Infrastruktur für öffentliche deutsche Leitlinien und Arzneimittelinformationen.
- Nicht behaupten: „hallucination-free“, „fully decentralised“, „clinically safe“, „100% extraction accuracy“, „GDPR compliant“ oder „no evidence exists“.
- Coverage, vorhandene Quellenfelder und technische Validatoren sind keine Garantie klinischer Genauigkeit.

## Daten- und Provenienzregeln

- Die versionierten kanonischen JSONL-Dateien unter `outputs/knowledge_corpus/canonical/` sind Source of Truth.
- PostgreSQL/pgvector ist ausschließlich ein regenerierbarer Index.
- Quell-PDFs niemals ändern, umbenennen oder überschreiben. Vor und nach datenverändernden Phasen ihre SHA-256-Werte prüfen.
- Exakten Originaltext niemals durch Normalisierung ersetzen. Fehlende Seiten-, Item- oder Versionsangaben bleiben `null` und erhalten einen transparenten QA-Flag.
- Jeder Import und jedes Embedding muss einem unveränderlichen `corpus_snapshot_id` zugeordnet sein.
- Bestehende kanonische Dateien nur über einen neuen, versionierten Snapshot weiterentwickeln; keinen Snapshot destruktiv ersetzen.

## Retrieval- und Quellenpolicy

- Alle normalen Retrieval-, Relations- und Evidenzpfade müssen die zentrale Eligibility-Grenze `eligible_retrieval_units` verwenden.
- Records mit `excluded_by_policy=true` oder `exclusion_reason=hcc_historical_change_table` dürfen niemals in normale Kandidaten oder Evidenzpakete gelangen.
- Die HCC/BCC-Konsultationsfassung bleibt `source_status=consultation_draft`, bis eine eindeutige Finalfassung quellenseitig belegt ist.
- Leitlinie und Fachinformation haben unterschiedliche `source_role`; Unterschiede werden nicht automatisch als Extraktionsfehler oder still als gelöst behandelt.
- Arzneimittelkomponenten (`smPC`, Annex II, Kennzeichnung, Patienteninformation) getrennt klassifizieren; bei Unsicherheit `unknown` plus QA-Flag.
- PostgreSQL-FTS heißt lexikalische Volltextsuche, nicht BM25.
- Die Baseline verwendet exakte pgvector-Suche. HNSW/IVFFlat sind kein primärer Index.
- Ranglisten nur per RRF oder einem explizit validierten Fusionsverfahren verbinden; inkompatible Rohscores nicht direkt addieren.
- Die Arzneimittelbrücke ist ausschließlich `smPC_to_guideline`: Fachinformation → validiertes Produkt → validierter Wirkstoff/Alias → policy-zulässige Leitlinienerwähnung. Keine implizite Rückrichtung erzeugen.
- Eine fehlende Leitlinienerwähnung oder eine nicht importierte Fachinformation ist kein Datenfehler. Nur belegte Kanten aktivieren; Kandidaten ohne Beleg bleiben review-/unmatched-markiert.

## Evidence- und Claim-Vertrag

- Ein Modell darf ausschließlich Evidence-IDs aus der Backend-Allowlist des aktuellen Pakets zitieren.
- Dokumentname, Version, Seite und Link rendert ausschließlich das Backend aus dem Snapshot.
- Öffentliche Labels sind exakt `supported`, `partially_supported`, `no_validated_evidence`.
- `no_validated_evidence` bedeutet nur: keine ausreichende Evidenz im freigegebenen Snapshot nach vollständigem Retrieval-Fallback.
- Modell-Selbstvertrauen ist kein Sicherheitskriterium. Unzureichend belegte Claims verwerfen oder herabstufen.
- Klinische Goldlabels niemals automatisch als unabhängigen Goldstandard ausfüllen. Automatische Fragen als `synthetic_draft` markieren.

## Secrets, externe APIs und Telemetrie

- Secrets niemals ausgeben, loggen oder committen. Die externe Datei `${AISURGEON_SECRET_ENV_FILE}` beziehungsweise standardmäßig `${XDG_CONFIG_HOME:-~/.config}/aisurgeon-decentralised/.env` nicht verändern.
- Externe API-Aufrufe nur mit öffentlichen, nicht patientenbezogenen Korpuseinheiten durchführen.
- Embedding-Batches sequenziell ausführen, sofort validieren und checkpointen. Validierte Checkpoints beim Resume nicht erneut berechnen.
- HTTP 400/401/402/403 sowie Modell-404, Billing- und `insufficient_quota`-Fehler nicht automatisch retryen. 408, transiente 429, 5xx und Netzwerkfehler nur mit begrenztem exponentiellem Backoff und Resume.
- Standardtelemetrie enthält keine vollständigen Nutzerfragen oder Antworten; Volltextlogging nur nach explizitem Opt-in und Redaction.
- „server load“ bezeichnet ausschließlich lokal gemessene Infrastruktur.
- Im geschlossenen Responses-Modus `store=false` und keine Websuche, File Search, MCP-, Code-Interpreter- oder Function-Tools übergeben. Nur Frage, feste Regeln und die endliche lokale Evidence-Allowlist verlassen das System.
- Der No-context-API-Vergleichsarm ist nie als evidenzvalidierte Ausgabe publizierbar.

## Reproduzierbarkeit und Abschluss

- Nach jeder Phase Tests, Validator, Checkpoint und Statusreport aktualisieren.
- Migrationen und Importe müssen idempotent sein; ein Rebuild muss Counts, Hashes, FKs, Relations-, Locator-, Eligibility- und Snapshot-Integrität prüfen.
- Es darf gleichzeitig nur einen schreibenden Migrationsprozess und einen Embedding-API-Prozess geben.
- Keinen Git-Commit, Push, Remote-Upload, Release oder PR ohne ausdrücklichen neuen Auftrag durchführen.
- Phase 1 bleibt eine gemeinsam importierbare CLI-/RAG-Kernpipeline. Web-App, Frontend und klinische Produktdarstellung gehören nicht in diese Phase.

## Phase-2-Vergleichsstudie

- Die Hauptstudie vergleicht die zwei Deployment-Konfigurationen GPT-5.5/medium und GPT-5.6 Sol/high in den Armen `WEB` und `RAG`; daraus darf kein isolierter Modelleffekt unabhängig vom Reasoning-Effort abgeleitet werden.
- Das Haupttestset umfasst genau 100 neue Fragen (80 `covered_by_local_corpus`, 20 `not_covered_by_local_corpus`). Die 20 VTE-Fragen aus Phase 1 bleiben ausschließlich Development-Daten.
- Vor kostenpflichtigen Hauptstudienaufrufen müssen die 100 unveränderten Fragen/Goldkandidaten durch `study_owner_pre_freeze_approval` in `outputs/study_phase2/questions/study_questions_frozen.jsonl` eingefroren sein. Keine Revieweridentitäten oder unabhängige Fragevalidierung erfinden; die spätere unabhängige verblindete Antwortbewertung bleibt verpflichtend.
- Der Studienplan enthält genau 800 Responses-Ergebnisse: 100 Fragen × 2 Modell-/Deployment-Konfigurationen × 2 Systeme × 2 Wiederholungen. Run 1 ist primär; Run 2 dient der Reproduzierbarkeit. Nie nachträglich die bessere Antwort auswählen.
- Pro geplantem Ergebnis ist genau ein generativer Responses-Aufruf zulässig. Keine versteckten LLM-Judges, Rewrite-, Repair- oder Zusatzgenerierungen.
- Der Webarm verwendet ausschließlich `web_search` mit verpflichtendem Toolaufruf und Live-Zugriff. Der RAG-Arm verwendet keine OpenAI-Tools und sendet nur Frage, feste Regeln und die endliche lokale Evidence-Allowlist.
- Beide Arme brauchen getrennte Provenienzvalidatoren. RAG-Evidence-IDs dürfen nicht zur automatischen Verwerfung von Webantworten verwendet werden; Web-URLs müssen aus dem jeweiligen aktuellen Toolaufruf stammen.
- Das kumulative Sicherheitslimit für externe Phase-2-Studienkosten ist `STUDY_MAX_ESTIMATED_API_COST_USD=500.00`. Vorbereitung, Pilot, Hauptlauf und Retries werden ohne Zählerreset gemeinsam berücksichtigt. Die frühere 400-USD-Grenze ist historisch archiviert und in aktiven Pfaden abgelöst.
- Nach dem Development-Piloten sind `max_output_tokens=6000` und `max_tool_calls=6` für alle Hauptstudienzellen eingefroren; angeforderte und tatsächlich beobachtete Webaktionen bleiben getrennt protokolliert.
- JSONL ist die kanonische Studienrohdatenquelle. CSV und Excel sind deterministisch daraus abgeleitete Exporte. Human-Ratings und Adjudikation bleiben von technischen Vorbewertungen getrennt.
- Ohne zwei unabhängige klinische Bewertungen und Adjudikation darf der Endstatus höchstens `TECHNICAL_STUDY_COMPLETE_CLINICAL_RATING_PENDING` lauten; keine klinische Validierung behaupten.
