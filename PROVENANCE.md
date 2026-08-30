# Provenienz

## Corpus Freeze

- Corpus Snapshot: `cs-f61b3d4e90089c1b890c23cb`
- Content fingerprint: `f61b3d4e90089c1b890c23cb877117c74938081cd16fdcc19dffbf5b8e3ef9e7`
- Quellen: 12 öffentliche Dokumente, 2.060 Seiten
- Retrievaleinheiten: 4.469
- vorhandene Baseline-Embeddings: 4.469, Modell
  `text-embedding-3-small`, Dimension 1.536
- Policy-Ausschluss: 99 historische HCC/BCC-Records; normales Retrieval-
  Leakage im technischen Abschluss 0

Die kanonischen JSONL-Dateien waren Source of Truth. PostgreSQL/pgvector war
ausschließlich ein regenerierbarer Index. PDFs, Korpusvolltexte und
Embeddingvektoren sind nicht Teil des GitHub-Bestands; ihre SHA-256-Werte und
Counts bleiben in Snapshot- und Quellenmanifesten nachprüfbar.

## Question und Model Freeze

Die 100 synthetischen Hauptstudienfragen wurden unverändert mit
`study_owner_pre_freeze_approval` eingefroren: 80
`covered_by_local_corpus`, 20 `not_covered_by_local_corpus`. Eine unabhängige
klinische Validierung der Fragen wurde nicht behauptet. SHA-256 der
eingefrorenen JSONL-Datei:

`b336637a1c954fdeef0d21635a7778b95b008fa947ee88f5ec1652430346c3bd`

Untersucht wurden zwei Deploymentkonfigurationen, nicht ein isolierter
Modelleffekt:

- `gpt-5.5-2026-04-23`, Reasoning `medium`
- `gpt-5.6-sol`, Reasoning `high`; zum Freeze war kein datierter
  GPT-5.6-Sol-Snapshot offiziell ausgewiesen

Pro Frage wurden `WEB` und `RAG` jeweils in `1_primary` und
`2_reproducibility` ausgeführt. Damit umfasst der Plan exakt 800 eindeutige
Studienzellen. Vollständige menschliche, verblindete klinische Bewertung und
Adjudikation stehen noch aus.

## Technischer Abschluss

Der technische Abschluss-Hashmanifest umfasst 48 historische Artefakte. Seine
eigene SHA-256 lautet:

`3db36fb33b31d194599984c81c926de5f0d9f82814d4e77b41c8aa02c54b67ca`

Ausgewählte darin gebundene historische Hashes:

| Artefakt | SHA-256 |
|---|---|
| `study_results.jsonl` | `890106ad4512da5bb6e91f970826a1fc536597bbbd22f6dc78945fc1400a5617` |
| `technical_completion_report.json` | `973479a29c0458d66144f9a06845fb4ff04a1700e0f9fd21ba21e8e55005a390` |
| `AISurgeon_RAG_vs_WEB_study_master.xlsx` | `efc2e684465b0605f4ec81b8a93bd50f7e876fe45ef2fa89a9ff25155d0c9640` |

Die unredigierten Dateien bleiben lokal unverändert und werden nicht
committet. Das GitHub-Archiv enthält getrennte, deterministisch erzeugte
redigierte Fassungen mit eigenen SHA-256-Werten in
`archive/ARCHIVE_SHA256SUMS`.

## Zeitliche Aussagekraft von Git

Der erste Git-Commit wurde erst nach Abschluss des technischen Studienlaufs
erstellt. Git kann daher den damaligen Studienzeitpunkt oder die Reihenfolge der
vorherigen Arbeitsschritte nicht allein beweisen. Archiviert werden stattdessen
die vor dem Commit erzeugten Question-, Model-, Prompt-, Preis-, Protokoll- und
technischen Abschluss-Hashes. Zusammen mit den Rohartefakthashes ermöglichen
sie eine nachträgliche Integritätsprüfung; sie ersetzen keine externe
Zeitstempel- oder Präregistrierungsinstanz.
