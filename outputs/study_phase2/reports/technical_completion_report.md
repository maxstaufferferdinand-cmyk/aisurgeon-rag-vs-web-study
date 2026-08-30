# Technischer Abschlussbericht: RAG versus Live Web

Status: `TECHNICAL_STUDY_COMPLETE_CLINICAL_RATING_PENDING`

Dies ist ein technischer, prä-spezifizierter In-silico-Benchmark. Er ist keine klinische Validierung und kein Nachweis klinischer Sicherheit.

## Freeze und Studiendesign

Corpus Snapshot: `cs-f61b3d4e90089c1b890c23cb`. Die exakt 100 Fragen wurden unverändert auf Grundlage von `study_owner_pre_freeze_approval` eingefroren: 80 `covered_by_local_corpus` und 20 `not_covered_by_local_corpus`. Es fand keine unabhängige klinische Validierung des Frage-/Goldsets statt.

API-Zeitraum (UTC): 2026-08-29T16:02:12.958252Z bis 2026-08-29T20:28:20.303142Z. Die prä-spezifizierte 80/20-Mischung ist keine Schätzung realer klinischer Coverage-Prävalenz.

| Konfiguration | Angefordert | Zurückgegeben | Reasoning | Snapshotstatus |
|---|---|---|---|---|
| GPT-5.5 / medium | `gpt-5.5-2026-04-23` | `gpt-5.5-2026-04-23` | medium | datiert |
| GPT-5.6 Sol / high | `gpt-5.6-sol` | `gpt-5.6-sol` | high | undatierter Alias |

Beide Bedingungen nutzten Responses API, `store=false`, Service Tier `default`, Text-Verbosity `medium` und maximal 6000 Output-/Reasoning-Tokens. WEB verwendete ausschließlich verpflichtende Live-Websuche; RAG verwendete keine OpenAI-Tools.

## Ausführung und Kosten

Geplant und erfasst: 800/800 eindeutige Ergebnisse; 802 tatsächliche API-Versuche, 2 transparente Retries und 0 terminal fehlgeschlagene Studienzellen. Es wurde keine fachlich ungültige Antwort neu generiert.

| Kostenkomponente | Geschätzte USD |
|---|---:|
| Pre-Freeze Query-Embeddings | 0.00006068 |
| Development-Kostenpilot | 2.16748940 |
| 800-Zellen-Hauptstudie inkl. Retries | 78.77028720 |
| **Kumulativ Phase 2** | **80.93783728** |
| Aktives fail-closed Limit | 500.00 |

Die Kosten sind anhand der vor Studienbeginn eingefrorenen öffentlichen Preistabelle `openai-public-prices-2026-08-29-v2-cap-500` mit Stichtag 2026-08-29 geschätzt. Eine offizielle Account-Abstimmung war mangels benötigtem Admin-Key nicht aktiviert.

## Primäre Ressourcenendpunkte

Die Tabelle nutzt den prä-spezifizierten Mittelwert der zwei Runs je Frage. Differenz = RAG minus WEB; 95-%-KI aus 10.000 Cluster-Bootstrap-Resamples auf Fragenebene.

| Modellkonfiguration | Metrik | RAG Mittel | WEB Mittel | Differenz | 95-%-KI | RAG/WEB |
|---|---|---:|---:|---:|---|---:|
| gpt55_medium | Kosten USD | 0.044698 | 0.140255 | -0.095557 | [-0.109516; -0.081835] | 0.400621 |
| gpt55_medium | End-to-End ms | 10419.367862 | 22855.335886 | -12435.968024 | [-14149.436108; -10786.060217] | 0.500234 |
| gpt55_medium | API-Wall ms | 9881.065203 | 22642.707081 | -12761.641878 | [-14460.114445; -11069.874555] | 0.479377 |
| gpt55_medium | TTFT ms | 4404.877020 | 14020.368642 | -9615.491622 | [-10840.597856; -8459.994952] | 0.362475 |
| gpt55_medium | Total Tokens | 5406.450000 | 19722.140000 | -14315.690000 | [-16284.866250; -12462.460500] | 0.337935 |
| gpt56_sol_high | Kosten USD | 0.035127 | 0.173770 | -0.138643 | [-0.154004; -0.123819] | 0.262031 |
| gpt56_sol_high | End-to-End ms | 9299.845970 | 36125.127497 | -26825.281527 | [-30596.254914; -23492.629706] | 0.310447 |
| gpt56_sol_high | API-Wall ms | 8760.224098 | 35900.536703 | -27140.312605 | [-30887.791321; -23717.783330] | 0.293432 |
| gpt56_sol_high | TTFT ms | 4840.618628 | 29025.795570 | -24185.176942 | [-27402.579711; -21196.070563] | 0.208166 |
| gpt56_sol_high | Total Tokens | 5228.400000 | 32010.085000 | -26781.685000 | [-29269.912000; -24354.098375] | 0.200029 |

Gesamttokens: Input 11,502,615, Cached Input 2,050,014, Cache Write 887,876, Output 970,800, davon Reasoning 524,899, Total 12,473,415. Reasoning-Tokens wurden als Untergruppe der Output-Tokens nicht doppelt verrechnet.

WEB führte 1,149 Web-Search-Aktionen aus. Die 100 Query-Embeddings des Pre-Freeze-Audits umfassten 3,034 Tokens; alle 400 RAG-Zellen nutzten transparent denselben Query-Hash-Cache, mit 0 neuen Embedding-Provideraufrufen im Hauptlauf. Fertige Retrievalantworten wurden nicht wiederverwendet.

## Retrieval, Abstention und Provenienzvalidierung

RAG Recall@5: 0.593750; MRR: 0.364718; 400 RAG-Ergebnisse, davon 320 für abgedeckte und 80 für nicht abgedeckte Fragen. `no_evidence_in_snapshot` trat bei 68 der nicht abgedeckten RAG-Ergebnisse auf.

| Arm | Technisch akzeptiert | Abgestuft | Verworfen | supported | partially_supported | no_validated_evidence |
|---|---:|---:|---:|---:|---:|---:|
| RAG | 316 | 5 | 79 | 237 | 64 | 99 |
| WEB | 360 | 23 | 17 | 330 | 53 | 17 |

Im RAG-Arm erhielten alle 80/80 nicht abgedeckten Frage-Runs den validierten Status `no_validated_evidence`; zusätzlich 19/320 abgedeckte RAG-Runs. Das ist ein technischer Abstention-Befund, keine klinische Richtigkeitsbewertung.

Policy-ineligible Evidence-Package-Issues: 0. Unbekannte/nicht allowlistete Evidence-ID-Issues: 1; diese Antwort wurde deterministisch abgefangen. WEB- und RAG-Provenienzvalidatoren wurden getrennt angewandt. Automatische Provenienzvalidierung belegt keine klinische Korrektheit.

WEB protokollierte 10,650 konsultierte Quellenoccurrences und 105 normalisierte zitierte Quellenoccurrences. Technische WEB-Flags: 382 fehlende URL-Zitationsannotationen und 40 nicht im jeweiligen Suchaufruf zurückgegebene URLs. Diese wurden abgestuft oder verworfen; eine Halluzinations- oder klinische Fehlerrate wird erst nach unabhängiger Bewertung berichtet.

## Reproduzierbarkeit

Vollständige Run-1/Run-2-Paare: 400/400. Antwortstatus-Übereinstimmung: 0.8675; mittlere deterministische Token-Cosinusähnlichkeit: 0.5825; mittlere Quellenreferenz-Jaccard-Überlappung: 0.6330; mittlere absolute Kostendifferenz: 0.027675 USD; mittlere absolute End-to-End-Differenz: 6009.43 ms.

## Tests und Integrität

- `phase2`: passed (36/36 Checks)
- `corpus`: passed (50/50 Checks)
- `retrieval`: passed (37/37 Checks)
- `phase1_cli`: passed (31/31 Checks)
- `rationale_relations`: passed (2/2 Checks)
- `pytest`: passed (118 Tests, 12 Subtests)
- `ruff`: passed (0 Issues)

Excel-Integrität: passed; Masterdatei mit 800 eindeutigen Ergebnissen und vier Armdateien mit je 200. Quell-PDFs, kanonische JSONL-Dateien und die 4.469 Corpus-Embeddings blieben unverändert.

## Protocol Deviations

- `PD-001` — Study-owner approval replaces independent question freeze: explicit study-owner directive before any main-study call
- `PD-002` — Cumulative Phase-2 cost ceiling increased to 500 USD: explicit study-owner directive before main-study execution

## Limitationen

- Independent clinical ratings, citation audit and adjudication are pending.
- The question/gold set was approved by the study owner and was not independently clinically validated.
- GPT-5.6 Sol was available only as an undated alias at study freeze.
- The synthetic 80/20 benchmark weighting is not a prevalence estimate.
- Provenance validators do not establish clinical correctness.
- The locally controlled snapshot is limited to three guidelines and nine medicinal-product-information PDFs.
- The snapshot retains 2,785 pre-existing review-severity extraction flags as documented QA limitations.
- Costs are estimates from the frozen public price table; official account reconciliation is disabled without an admin key.
- All 400 main-study RAG cells used transparently recorded query-embedding cache hits from the 100-question pre-freeze audit.
- Gemini was not called in Phase 2, and no patient data were processed.

## Zentrale Artefakte und Resume

- Kanonische Rohdaten: `outputs/study_phase2/results/study_results.jsonl` und `api_attempts.jsonl`
- Master-Excel: `outputs/study_phase2/excel/AISurgeon_RAG_vs_WEB_study_master.xlsx`
- Klinisches Rating: `outputs/study_phase2/ratings/clinical_ratings_blinded.xlsx`
- Citation Audit: `outputs/study_phase2/ratings/citation_audit.xlsx`
- Vollständige maschinenlesbare Kennzahlen: `outputs/study_phase2/reports/technical_completion_report.json`

Nach zwei unabhängigen verblindeten Ratings und Citation Audit:

```bash
PYTHONPATH=src uv run python scripts/import_rag_vs_web_ratings.py
```

Deterministischer Neuaufbau der technischen Exporte ohne API-Aufruf:

```bash
PYTHONPATH=src uv run python scripts/finalize_rag_vs_web_main.py
```

Es wurde kein Git-Commit und kein Git-Push durchgeführt.
