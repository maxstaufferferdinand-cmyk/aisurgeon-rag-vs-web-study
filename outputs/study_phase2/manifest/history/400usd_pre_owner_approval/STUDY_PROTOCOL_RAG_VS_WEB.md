# Studienprotokoll: lokales Closed-Corpus-RAG versus verpflichtende Live-Websuche

Protokollversion: `rag-vs-web-1.0.0`  
Prä-spezifizierte Benchmarkgewichtung: 80 lokal abgedeckte / 20 lokal nicht ausreichend abgedeckte Fragen  
Status: technischer Forschungsbenchmark; kein klinisch validiertes Medizinprodukt

## Ziel und Design

Diese prä-spezifizierte, gepaarte und wiederholte In-silico-Studie vergleicht
zwei Quellenräume für deutschsprachige Antworten an ärztliches Fachpublikum:

1. `WEB`: GPT mit in jedem Aufruf verpflichtender neuer Live-Websuche;
2. `RAG`: dasselbe GPT-Deployment mit lokaler Query-Normalisierung, FTS,
   exakter pgvector-Suche, Hybrid-RRF, gerichteter SmPC→Leitlinien-Expansion
   und endlicher Evidence-Allowlist, ohne OpenAI-Tools.

Verglichen werden zwei Modell-/Deployment-Konfigurationen, nicht ein isolierter
Modelleffekt: `gpt-5.5-2026-04-23` mit Reasoning `medium` und `gpt-5.6-sol`
mit Reasoning `high`. Für GPT-5.6 Sol war am Zugriffstag 2026-08-29 in der
offiziellen Modelldokumentation kein datierter Snapshot ausgewiesen.

Das Hauptdesign umfasst genau 100 neue Fragen, zwei Modelle, zwei Systeme und
zwei unabhängige stateless Wiederholungen, also 800 geplante Responses. Run 1
ist der primäre klinische Lauf; Run 2 dient der Reproduzierbarkeit. Antworten
werden niemals nachträglich nach Güte ausgewählt.

## Daten, Corpus und Population der Fragen

Der lokale Quellenraum ist der unveränderliche Snapshot
`cs-f61b3d4e90089c1b890c23cb` mit 4.469 retrievalfähigen Einheiten und 4.469
Embeddings (`text-embedding-3-small`, 1536 Dimensionen). PostgreSQL/pgvector
ist nur ein regenerierbarer Index. Die 99 historischen HCC-Records bleiben
policy-exkludiert. Die HCC/BCC-Quelle ist als `consultation_draft` sichtbar und
darf nicht als Finalfassung ausgegeben werden.

Die 80/20-Verteilung ist eine vorab festgelegte Benchmarkgewichtung und keine
Schätzung der klinischen Coverage-Prävalenz. Die 20 VTE-Fragen aus Phase 1 sind
ausschließlich Development-Daten und werden nicht als Hauptstudienfragen
verwendet. Kandidaten und Goldfelder werden synthetisch quellengestützt erzeugt,
aber erst nach unabhängiger menschlicher Freigabe eingefroren.

## Endpunkte

Primärer Ressourcenendpunkt ist die marginale Gesamtkostenlast in USD pro
vorgesehenem Frage-Run einschließlich Modell, Web Search, Search-Content,
Query-Embedding und Retries. Der primäre Systemeffekt ist RAG versus Web,
gepaart auf Fragenebene und nach Modellkonfiguration stratifiziert; pro Frage,
Modell und System wird der Mittelwert der zwei Runs verwendet.

Sekundäre Ressourcenendpunkte sind End-to-End- und API-Latenz,
Time-to-first-token, Input-/Output-/Reasoning-/Gesamttokens, Webaufrufe,
lokale Retrievalzeit und Kosten pro klinisch akzeptabler Antwort. Qualität und
Sicherheit umfassen klinische Akzeptabilität, Major-/Critical-Error-Rate,
Vollständigkeit, Abstention, unsupported Claims, Zitationsvalidität,
Claim-Source-Support, Quellenqualität sowie RAG Recall@5 und MRR.

## API- und Promptkonfiguration

Alle Aufrufe verwenden die Responses API, `store=false`, `service_tier=default`,
keine Conversation und kein `previous_response_id`. Text-Verbosity ist für alle
Zellen identisch dokumentiert; nicht gesetzte Samplingparameter werden als
`not_set` ausgewiesen. Im Development-Piloten lag das Maximum bei 2.786
Output-Tokens; deshalb wird `max_output_tokens=6000` unverändert für alle
Hauptstudienzellen eingefroren. Die sichtbare Antwortgrenze bleibt 350 Wörter.

WEB verwendet ausschließlich `web_search`, `tool_choice=required`,
`external_web_access=true`, `return_token_budget=default`,
`include=["web_search_call.action.sources"]`. Der Pilot startete mit
`max_tool_calls=5`; eine erfolgreiche Antwort wies tatsächlich sechs
Search-/Open-/Find-Aktionen aus. Daher wird der angeforderte Hauptstudienwert
prospektiv auf `max_tool_calls=6` eingefroren, während angeforderte und
tatsächliche Aufrufzahlen getrennt protokolliert werden. Es gibt keine
Domain-Allowlist und keine anderen Tools.

RAG sendet ausschließlich die Frage, feste Regeln und eine lokal erzeugte
Evidence-Allowlist. Vollständige PDFs, PostgreSQL-Zugang, Dateisystemzugriff und
nicht ausgewählte Korpusinhalte werden nicht übertragen. Quellenlabels und
Seitenlokatoren rendert das Backend.

Beide Arme verwenden dasselbe strikte Structured-Output-Schema. Structured
Output garantiert nur das Format und ist kein Nachweis klinischer Richtigkeit.
Pro Studienzelle ist genau ein generativer Aufruf erlaubt; transparente Retries
bei 408/429/5xx/Netzwerkfehlern zählen als zusätzliche HTTP-Versuche. Es gibt
keine LLM-Judges, Rewrites oder Citation-Repair-Aufrufe.

## Randomisierung, Retry und Resume

Der feste Seed ist `20260829`. Innerhalb jedes Passes werden Fragen,
Modellkonfiguration und WEB/RAG-Reihenfolge blockweise randomisiert und
zeitlich verschachtelt. Concurrency ist eins. Maximal zwei Retries erfolgen nur
bei klar transienten Fehlern mit exponentiellem Backoff. 400/401/402/403 sowie
eine modellbezogene 404 werden
nicht automatisch wiederholt; dasselbe gilt für Billing-/Quota-Blockaden und
eine nicht verfügbare Modell-ID. Der Runner checkpointet jeden Versuch und jede
fertige Zelle; fertige Run-IDs werden beim Resume nicht erneut aufgerufen.

## Kostensicherheit

Vor der Hauptstudie werden fünf alte Development-Fragen in beiden Modellen und
beiden Systemen einmal ausgeführt (20 Pilot-Responses). Preise werden vor dem
ersten Studienaufruf aus der offiziellen OpenAI-Preisseite eingefroren. Das
kumulative Limit beträgt `STUDY_MAX_ESTIMATED_API_COST_USD=400.00` und umfasst
Pilot, Hauptstudie, Web Search, Modell- und Query-Embedding-Tokens sowie alle
Retries. Reasoning-Tokens sind Teil der Output-Tokens und werden nicht doppelt
berechnet. Vor jedem Hauptblock wird eine konservative Restkostenprojektion
geprüft. Die empirische Oberprojektion setzt für jede der 200 Zellen einer
Modell-/Systemkonfiguration den beobachteten Pilot-Maximalpreis für alle drei
zulässigen Versuche an; sie ist keine vertragliche Anbieterpreisgarantie.

## Provenienz- und klinische Validierung

RAG- und Webantworten werden durch getrennte deterministische Validatoren
geprüft. Der RAG-Validator prüft Evidence-Allowlist, Eligibility,
HCC-History-Leakage, gerichtete Arzneimittelrelationen und Backend-Lokatoren.
Der Web-Validator prüft URLs gegen genau die im aktuellen Toolaufruf
zurückgegebenen Quellen und Annotationen. Diese Prüfungen validieren
Provenienz, nicht klinische Richtigkeit.

Run-1-Antworten werden für mindestens zwei unabhängige klinische Reviewer
verblindet; eine getrennte Citation-Audit-Datei erhält Quellenmetadaten. Codex
darf technische Vorbewertungen erzeugen, ist aber nicht der endgültige
klinische Rater. Ohne Ratings und Adjudikation lautet der höchstmögliche Status
`TECHNICAL_STUDY_COMPLETE_CLINICAL_RATING_PENDING`.

## Human-Freeze und Abweichungen

Die 800 Hauptaufrufe beginnen ausschließlich, wenn
`outputs/study_phase2/questions/study_questions_frozen.jsonl` genau 100
menschlich freigegebene Fragen mit bestätigter Coverage, Pflichtclaims,
kritischen Fehlern und Goldquellen beziehungsweise RAG-Abstention enthält.
Vorher ist der verbindliche Status `HUMAN_QUESTION_FREEZE_REQUIRED`.

Protokoll, Analyseplan, Prompts, Schema, Modelle, Preise, Corpus und nach
menschlicher Freigabe Fragen/Goldstandard werden SHA-256-gehasht. Jede spätere
Änderung wird in einem Protocol-Deviation-Register dokumentiert.
Unmittelbar vor dem Human-Freeze ruft ein Fail-Closed-Prüfschritt ausschließlich
die drei prä-spezifizierten offiziellen OpenAI-Seiten für GPT-5.5, GPT-5.6 Sol
und Preise ab. Der Nachweis darf beim Freeze höchstens 24 Stunden alt sein und
wird samt Seitenhashes in das Studienmanifest aufgenommen.

## Limitationen

Die Fragen sind synthetisch und die 80/20-Mischung künstlich. Der lokale Corpus
ist auf drei Leitliniendokumente und neun Arzneimittelinformations-PDFs
begrenzt; eine Quelle ist eine Konsultationsfassung. Webinhalte können sich
zeitlich ändern, und `gpt-5.6-sol` ist ohne datierten Snapshot weniger exakt
reproduzierbar. API-Latenz hängt von externen Diensten ab. Es wurden keine
Patientendaten verarbeitet. Technische Coverage und Quellenfelder sind keine
klinische Genauigkeitsgarantie.
