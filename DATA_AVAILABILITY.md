# Datenverfügbarkeit und Rechtegrenzen

## Im privaten Repository enthalten

- vollständiger Python-, SQL-, Test- und Docker-Code;
- sichere Konfigurationsvorlagen;
- Protokoll, Statistical Analysis Plan, Prompts und Antwortschema;
- REFINE- und MI-CLEAR-LLM-Compliance-Matrizen;
- Corpus-, Question-, Model-, Preis- und technische Freeze-Manifeste;
- die 100 eingefrorenen synthetischen Fragen;
- Bridge-Relationen und aggregierte Retrieval-/QA-Berichte ohne Korpusvolltext;
- aggregierte Statistiken;
- redigierte JSONL-/CSV-Ergebnisse und neu abgeleitete Excel-Dateien;
- leere beziehungsweise noch nicht klinisch ausgefüllte Rating- und
  Adjudikationsvorlagen.

## Nicht enthalten

- Original-Leitlinien- und Fachinformations-PDFs;
- vollständig extrahierte kanonische Korpora und Evidence-Spans;
- PostgreSQL-Volumes oder Datenbankdumps;
- 4.469 Korpus-Embeddingvektoren und Query-Embedding-Caches;
- Extraktions- und Embeddingcheckpoints;
- unredigierte API-Attempt-Ledger, HTTP-Header, Provider-Request-/Response-IDs,
  SDK-Dumps oder operative Logs;
- Secrets, Credential-Dateien, private Schlüssel, Patientendaten,
  Reviewernamen oder Signaturen.

Die Ausschlüsse sind kein Datenfehler. Sie folgen fehlender ausdrücklicher
Weiterverbreitungsfreigaben beziehungsweise dem Prinzip der Datensparsamkeit.
Insbesondere weist die offizielle Bezugsseite darauf hin, dass die
HCC/BCC-Konsultationsfassung nicht die endgültige autorisierte Fassung ist und
ihr Inhalt nicht durch Dritte weiterverbreitet werden darf.

## Quellenbezug

`archive/corpus/source_manifest.json` und `.csv` enthalten für alle zwölf
Dokumente Titel, Herausgeber, Version, offizielle Bezugsseite, ursprüngliches
Abrufdatum, SHA-256, Seitenzahl und Corpus-Snapshot-ID. Ein aktueller Download
kann von der eingefrorenen Fassung abweichen; nur eine SHA-256-identische Datei
rekonstruiert denselben Snapshot.

## Lizenz

Dem Repository wurde bewusst keine Open-Source-Lizenz hinzugefügt. Es bleibt
privat. Aus der technischen Archivierung folgt weder eine Lizenz für die
Quellwerke noch eine Freigabe für öffentliche Weiterverbreitung. Vor einer
späteren Veröffentlichung müssen Rechte für Code, synthetische Studieninhalte
und abgeleitete Artefakte separat geklärt werden.

## Klinische Daten

Es wurden keine Patientendaten verarbeitet. Die Studienfragen sind
synthetische Forschungsfragen. Die technische Provenienzvalidierung der
Antworten ist keine unabhängige klinische Validierung; diese bleibt ausstehend.
