# Statistischer Analyseplan: RAG versus Live Web

Version: `rag-vs-web-1.0.0`  
Statistische Einheit: Frage, nicht API-Aufruf

## Analysepopulationen

Die technische Intention-to-benchmark-Population umfasst alle 800
prä-spezifizierten Zellen einschließlich transparent fehlgeschlagener Aufrufe.
Ressourcenanalysen berichten alle tatsächlich angefallenen Versuche. Die
primäre Inhaltsanalyse verwendet Run 1 nach verblindeter menschlicher
Bewertung. Ergebnisse werden für 80 `covered_by_local_corpus`, 20
`not_covered_by_local_corpus` und das prä-spezifizierte 80/20-Gemisch getrennt
berichtet. Dieses Gemisch bildet keine reale Coverage-Prävalenz ab.

## Primärer Schätzer

Für jede Frage, Modellkonfiguration und Systembedingung wird die gesamte
marginale USD-Kostenlast über beide Wiederholungen gemittelt. Innerhalb jeder
Modellkonfiguration wird die gepaarte Differenz `RAG − WEB` berechnet. Berichtet
werden Mittelwert, Median, Standardabweichung, Interquartilsabstand sowie ein
95-%-Konfidenzintervall aus einem Cluster-Bootstrap auf Fragenebene. Der
prä-spezifizierte Bootstrap verwendet 10.000 Resamples und Seed `20260829`.
Run 1 wird zusätzlich separat dargestellt.

Kosten schließen Modell-, Cache-, Web-Search-, Search-Content-,
Query-Embedding- und Retrykosten ein. Reasoning-Tokens sind eine Untergruppe
der Output-Tokens. Fehlgeschlagene Versuche ohne Usage-Rückgabe werden als
Kosten unbekannt markiert und nicht fälschlich mit null gleichgesetzt; eine
Sensitivitätsanalyse imputiert den konservativen Pilot-Maximalwert.

## Sekundäre Ressourcenanalysen

Für End-to-End-Latenz, API-Wall-Time, TTFT, Tokenarten, Webaufrufe und lokale
Retrievalzeit werden Mittelwert, Standardabweichung, Median, IQR, p50 und p95
berichtet. Systemvergleiche nutzen gepaarte Differenzen und Verhältnisse mit
Cluster-Bootstrap-CIs auf Fragenebene. API-Wall-Time und End-to-End-Zeit werden
nicht vermischt. Kosten pro klinisch akzeptabler Antwort wird erst nach
menschlicher Bewertung berechnet.

## Qualitäts- und Sicherheitsendpunkte

Primäre klinische Inhaltsbewertungen in Run 1 umfassen klinische
Akzeptabilität, Vollständigkeit, Minor/Major/Critical Error, angemessene
Empfehlung und angemessene Abstention. Citation-Audit umfasst Existenz,
Quellenqualität, Claim-Source-Support, Vollständigkeit, erfundene Quellen und
Lokatorrichtigkeit. RAG-Retrievalmetriken sind Recall@5 und MRR gegen den
menschlich freigegebenen Goldstandard; Recall@1/@3/@10, nDCG und vollständige
Multi-Evidence-Coverage sind ergänzend.

Binäre gepaarte Systemendpunkte werden als Differenzen der Anteile mit
fragengeclusterten Bootstrap-CIs berichtet. Nach Ratings wird ein geeignetes
gepaartes beziehungsweise Mixed-Effects-Modell mit Frage als Cluster und
Modell-/Deploymentkonfiguration als Stratum ergänzt. Wegen der unterschiedlichen
Reasoning-Einstellungen wird kein isolierter Modelleffekt behauptet.

## Reproduzierbarkeit

Run 1 und Run 2 werden anhand von Statusübereinstimmung, Claim- und
Empfehlungskonsistenz, semantischer Antwortähnlichkeit, Quellenüberlappung,
Evidence-ID-Jaccard sowie Variabilität von Tokens, Kosten und Latenz verglichen.
Die semantische Metrik wird ausschließlich deterministisch/lokal oder mit einer
vorab dokumentierten Methode berechnet; es erfolgt kein versteckter LLM-Judge.

## Rateranalyse

Mindestens zwei klinische Reviewer bewerten unabhängig. Für ordinale
Fehlerkategorien wird gewichtetes Kappa, alternativ bei fehlenden/verteilten
Ratings Krippendorff-Alpha, mit 95-%-CI berichtet. Rohübereinstimmung und
Adjudikationsrate werden ergänzt. Adjudizierte Labels bilden die primäre
klinische Auswertung; Einzelrevieweranalysen sind Sensitivitätsanalysen.

## Fehlende Daten und Protocol Deviations

Jede geplante Zelle bleibt im 800-Zeilen-Plan. Technisch fehlgeschlagene
Antworten werden nicht durch eine unkontrollierte Neugenerierung ersetzt.
Ressourcenfelder werden soweit messbar ausgewertet, inhaltliche Felder als
nicht bewertbar markiert. Missingness, Retryursache und HTTP-Status werden nach
Arm und Modell berichtet. Jede Abweichung von Freeze-Hashes, Ausführungsfolge,
Modell-ID, Outputgrenze oder Toolkonfiguration wird vor der Ergebnisanalyse im
Deviation-Register offengelegt.

## Multiplikität und Interpretation

Der Kostenvergleich ist der primäre Endpunkt. Alle weiteren Tests sind
sekundär/explorativ; Effektgrößen und Konfidenzintervalle haben Vorrang vor
isolierten p-Werten. Es wird keine klinische Sicherheit aus technischen
Validatoren, synthetischen Fragen oder Coverage-Werten abgeleitet.

