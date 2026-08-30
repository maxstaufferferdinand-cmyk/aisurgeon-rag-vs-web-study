# Extraktions- und QA-Bericht

Erstellt: 2026-08-16T10:58:00.397796+00:00

## Laufkonfiguration

- Modell: `gemini-3.5-flash`
- Promptversion: `clinical-corpus-de-v1.2.0`
- Schemaversion: `knowledge-corpus-1.0.0`
- PDF-Verarbeitung: deterministische Mini-PDFs; keine vollständige PDF an Gemini
- Gemini-Aufrufe: sequenziell; keine Modell-Fallbacks
- Embeddings: nicht erzeugt; nur providerneutrales Embedding-Input

## Umfang

- PDFs: 12
- Seiten: 2060
- Erfolgreiche Vollbatches: 831
- Fehlgeschlagene Vollbatches: 0
- Durch deterministische Batch-Verkleinerung wiederhergestellt: 9
- Formale Leitlinienitems: 558
- Primäre Haupttext-Items: 433
- Sekundäre/historische formale Darstellungen: 125
- Tabellen/Abbildungen/Algorithmen: 275
- Arzneimittelprodukte: 28
- Wirkstoffe: 10
- Retrieval-Einheiten: 4469

## Deterministische QA

- Coverage: 100.0000 %
- Citation Completeness: 100.0000 %
- Ungelöste QA-Flags: 2785
- Quell-PDFs unverändert: True
- Gezieltes Reparaturoverlay angewendet: True
- Gezielte Reparatur: lokal-deterministisch; keine erneute Batch-Extraktion und kein Gemini-Aufruf

Citation Completeness prüft alle klinischen kanonischen Records und Retrieval-Einheiten auf
vollständige Quelle, SHA-256, Originalseite, Quelltext, Zitationslabel und Batch-Provenienz.

## Antwortlogik für spätere Retrieval-/Synthese-Läufe

- **supported:** Die konkrete Aussage wird von den tatsächlich abgerufenen und zitierten kanonischen Quellen vollständig getragen.
- **partially supported:** Nur ein Teil der Aussage ist belegt oder relevante Einschränkungen bleiben bestehen.
- **no validated evidence:** Im validierten Korpus wurde keine ausreichend belastbare Quelle gefunden.

Diese Einstufung darf ausschließlich auf abgerufenen kanonischen Datensätzen beruhen. Fehlende Evidenz
ist keine Evidenz für das Gegenteil. Der Extraktionslauf selbst vergibt keinen pauschalen Support-Status.

## Reproduzierbarkeit

Validierte Checkpoints werden über stabile Batch-IDs adressiert. Ein erneuter Lauf überspringt nur
schema-, prompt-, modell- und quellidentische validierte Checkpoints. Das Source Manifest friert die
Eingabemenge ein; neu hinzukommende oder geänderte PDFs brechen die Integritätsprüfung ab.
