# Corpus Snapshot `cs-f61b3d4e90089c1b890c23cb`

## Identität

- Schema: `corpus-snapshot-1.0.0`
- Retrievalschema: `retrieval-provenance-1.0.0`
- Retrievalpipeline: `retrieval-phase-1.2.0`
- Content-Fingerprint:
  `f61b3d4e90089c1b890c23cb877117c74938081cd16fdcc19dffbf5b8e3ef9e7`
- Vorgänger: `cs-d248f3d2c72bbaadd8fbe89d`
- Manifest:
  `outputs/knowledge_corpus/manifests/corpus_snapshots/cs-f61b3d4e90089c1b890c23cb.json`

Die ID wird deterministisch aus relativen Quellpfaden, PDF-/Canonical-Hashes,
Schema- und Pipelineversion gebildet. Absolute maschinenspezifische Manifest-
Pfade und ein Git-SHA werden nicht verwendet; das Repository besitzt noch
keinen Commit.

## Umfang

- 12 PDF-Dateien, 2.060 Seiten
- 29 kanonische JSONL-Dateien mit 8.224 physischen Zeilen
- 7.306 nicht aggregierte quellabgeleitete Records
- 4.469 Retrieval-Einheiten
- 12.492 Evidenzspans: 7.306 Recordspans plus 5.186 nichtleere Tabellenzellen
- 195 explizite semantische Relationen

Die physischen Zusatzzeilen sind keine zusätzlichen Quellrecords:
`documents.jsonl` enthält 12 Dokumententitäten, `pharmacology.jsonl` dupliziert
868 spezialisierte Pharmakologiepartitionen, und normalisierte Produkt-/
Wirkstoffdateien enthalten 28/10 Entitäten. Der Import nutzt deshalb eine
explizite Partition-Map.

## Bestandsabweichungen

| Merkmal | zuvor gemeldet | belegt im Snapshot | Erklärung |
|---|---:|---:|---|
| formale Items | 529 | 558 | 529 war der Stand vor dem finalen +29-Overlay; aktuell 433 primär +125 sekundär |
| Retrieval-Einheiten | 4.585 | 4.469 | 4.585 liegt nur in einem älteren Pre-Policy-Backup; alle aktuellen SoT-Artefakte nennen 4.469 |
| gedruckte Duplikate `15.4` ohne native Nummer | 1 | 2 | VTE PDF-S. 139 und 150; zusätzlich existiert das reguläre `15.4` auf S. 136 |

Bestätigt bleiben die drei source-nativen Nummerierungslücken `15.7`, `19.2`
und `4.29`, das unnummerierte HCC/BCC-Haupttextitem auf PDF-S. 152 und die
transparenten VTE-Reparaturen. Keine Nummer wurde erfunden.

## Eligibility und Status

- kanonisch eligible: 4.794
- kanonisch ineligible: 2.512
- aktive Retrieval-Einheiten: 4.469
- HCC-History-Ausschlüsse: 99
- sekundäre formale Items: 125, aus normaler Suche ausgeschlossen
- HCC/BCC-Dokumentstatus: `consultation_draft`

Alle 3.206 aktiven Arzneimittel-Retrieval-Einheiten liegen in lokal geprüften
SmPC-Seitenbereichen. Anhänge, Kennzeichnung und Patienteninformation werden
nicht als SmPC-Retrieval-Evidenz eingemischt.

Lokal anhand der PDF-Überschriften geprüfte Komponentenbereiche:

| Dokument | SmPC | Annex II | Annex-III-Titel | Kennzeichnung | Patienteninformation |
|---|---:|---:|---:|---:|---:|
| Abraxane | 1–30 | 31–32 | 33 | 34–40 | 41–51 |
| Eliquis | 1–124 | 125–127 | 128 | 129–151 | 152–204 |
| Enhertu | 1–40 | 41–44 | 45 | 46–49 | 50–59 |
| Keytruda | 1–347 | 348–351 | 352 | 353–363 | 364–393 |
| Lixiana | 1–36 | 37–39 | 40 | 41–63 | 64–73 |
| Plavix | 1–27 | 28–29 | 30 | 31–39 | 40–56 |
| Xarelto | 1–186 | 187–190 | 191 | 192–256 | 257–347 |

5-FU (S. 1–7) und Cisplatin (S. 1–12) sind durchgehend nationale
Fachinformation/SmPC. Der Annex-III-Titel wird im kompakten Source-Version-
Range als Teil des anschließenden Kennzeichnungscontainers geführt; es existiert
kein aktiver Retrieval-Record auf diesen Titelseiten.

## Bekannte QA-Einschränkungen

Der header-only Targeted-Repair-Queue bedeutet nicht, dass keine Reviewpunkte
mehr bestehen. Der Snapshot bewahrt 2.785 Review-Flags, darunter:

- 710 `quote_not_locally_verified`
- 440 `dosing_fields_not_explicit_or_missing`
- 273 `dose_value_not_explicit`
- 264 fehlende/unklare Nebenwirkungshäufigkeiten
- 177 unklare formale Itemnummern
- 154 ungelöste Referenzlinks
- 12 Formal-Item-Kandidatenseiten ohne Item

Diese Einschränkungen sind nicht blockierend für die technische Retrievalphase,
aber relevant für spätere Human-Annotation und klinische Interpretation.
