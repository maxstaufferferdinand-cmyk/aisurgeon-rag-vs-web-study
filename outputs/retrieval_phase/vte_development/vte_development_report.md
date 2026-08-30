# VTE Development-Demonstration

Corpus Snapshot: `cs-f61b3d4e90089c1b890c23cb`

Die 20 Fragen sind source-derived `synthetic_draft`-Development-Daten und kein finaler unangetasteter klinischer Testdatensatz.

## Retrievalmetriken

| Modus | Hit@1 | Recall@3 | Recall@5 | MRR | nDCG@5 | No-evidence korrekt | Mittel ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| fts | 0.118 | 0.206 | 0.206 | 0.167 | 0.161 | 0.667 | 60.2 |
| vector | 0.765 | 0.735 | 0.735 | 0.765 | 0.737 | 1.000 | 42.3 |
| hybrid_rrf | 0.588 | 0.559 | 0.618 | 0.600 | 0.588 | 1.000 | 116.7 |
| hybrid_rrf_bridge | 0.706 | 0.676 | 0.735 | 0.718 | 0.706 | 1.000 | 117.9 |

## Erwartete und gefundene Items (Hybrid-RRF plus Bridge)

| ID | Erwartet | Gefunden (Rangfolge) |
|---|---|---|
| vte-dev-001 | 5.1 | 5.1, 19.1 |
| vte-dev-002 | 6.3 | 6.3, 6.2, 6.4 |
| vte-dev-003 | 7.3 | 19.4, 10.4 |
| vte-dev-004 | 8.1, 8.2 | 8.2 |
| vte-dev-005 | 8.1 | 8.1 |
| vte-dev-006 | 8.1 | 8.1 |
| vte-dev-007 | 8.2 | 8.2 |
| vte-dev-008 | 9.2 | 9.2, 10.4, 19.5 |
| vte-dev-009 | 9.4 | 9.4, 9.3, 9.7, 9.6, 9.5 |
| vte-dev-010 | 10.2 | 10.2, 10.1, 10.4, 12.7, 10.3 |
| vte-dev-011 | 10.1, 10.3 | 10.1, 10.3, 10.4, 10.2, 8.1 |
| vte-dev-012 | 12.17 | 12.17, 12.28 |
| vte-dev-013 | 12.22 | 12.28 |
| vte-dev-014 | 12.32 | 12.32 |
| vte-dev-015 | 12.40 | 10.2, 10.1, 12.42, 10.4, 12.43 |
| vte-dev-016 | 13.5 | keine |
| vte-dev-017 | 15.5 | 10.1, 10.2, 10.4, 10.3, 15.5, 15.4 |
| vte-dev-018 | no-evidence | keine |
| vte-dev-019 | no-evidence | keine |
| vte-dev-020 | no-evidence | keine |

## Determinismus

Deterministische Wiederholungen: **80/80 identisch**.

## Responses-Kostengate

Drei-Fragen-Smoke (beide Arme): 6 API-Aufrufe.  
Konservative maximale Zusatzkostenschätzung: $0.072725; Grenze: $2.00; Entscheidung: `proceed`.

## Antwortgenerierung

- `closed_corpus_rag`: 20 Runs, Backend-publizierbar 20, Kosten $0.019739, Input/Output-Tokens 74447/3880, mittlere API-Wall-Time 2533.7 ms.
- `no_retrieval_context`: 20 Runs, Backend-publizierbar 0, Kosten $0.009803, Input/Output-Tokens 6939/6732, mittlere API-Wall-Time 3593.3 ms.

Der No-context-Arm ist absichtlich nicht als evidenzvalidierte Ausgabe publizierbar; er dient nur als API-Vergleich. Quellenangaben im Closed-RAG-Arm werden ausschließlich aus Backend-Lokatoren gerendert.

## Technische Einordnung

Die Resultate sind eine technische Development-Demonstration. Automatische Retrievalerwartungen und technische Claim-Validatoren ersetzen keine unabhängige klinische Annotation oder Validierung.
