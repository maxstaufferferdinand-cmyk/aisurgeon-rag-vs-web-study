# Retrievaldesign und Konfiguration

## Kanäle

### Deterministisch

`search_exact` priorisiert source-native Itemnummern, danach exakte Aliase und
strukturierte Suchfelder. Originaltext wird nicht normalisiert überschrieben.
Produkte, Wirkstoffe, dokumentierte Namen/Mentions, Dosiswerte, Einheiten,
Frequenz, Route, Dokumentkennung und Kapitelpfade bleiben getrennte Felder.

### Lexikalisch

PostgreSQL erzeugt zwei getrennte `tsvector`-Repräsentationen:

- `german`: deutschsprachiger natürlicher Retrievaltext;
- `simple`: ungestemmte Produkt-/Wirkstoffnamen, Einheiten, Abkürzungen,
  Itemnummern und kritische Originalbegriffe.

Beide besitzen GIN-Indizes. Dies ist PostgreSQL-Volltextsuche, nicht BM25.
PostgreSQL empfiehlt GIN als bevorzugten FTS-Indextyp:
<https://www.postgresql.org/docs/18/textsearch-indexes.html>.

### Schreibvarianten

`pg_trgm` unterstützt Tippfehler und Produkt-/Wirkstoffvarianten über GIN-
Trigrammindizes. Itemnummern bleiben zusätzlich exakt indiziert. Technischer
Default für die Similarity-Schwelle ist 0,15; er ist nicht klinisch optimiert.
Siehe <https://www.postgresql.org/docs/18/pgtrgm.html>.

### Dense Retrieval

- Modell: `text-embedding-3-small`
- Dimension: 1.536
- Distanz: Cosinus
- Primärbaseline: exakter pgvector-Scan
- ANN-Indizes: keine HNSW-/IVFFlat-Indizes

Embeddingtext:

```text
Dokumenttyp | Quellenrolle | Dokumentkomponente | Kapitel |
Itemtyp/Itemnummer | Produkt/Wirkstoff | Quellsegment
```

Für den kleinen aktiven Korpus ist der exakte Scan die verlustfreie Referenz.
Das pgvector-Projekt dokumentiert exakte Suche als Default, solange kein ANN-
Index angelegt wird: <https://github.com/pgvector/pgvector>.

Die OpenAI-Modellseite nennt 1.536 als Defaultdimension und einen Standardpreis
von 0,02 USD pro Million Input-Tokens zum dokumentierten Preisstand 2026-08-16:
<https://developers.openai.com/api/docs/models/text-embedding-3-small>.

## Baseline-Ergebnis

- Drei-Einheiten-Smoke: 3/3, Persistenz idempotent, jeder Queryvektor Self-Rank 1
- Vollbaseline: 4.469/4.469
- API-Input-Tokens: 1.217.859
- geschätzte Baselinekosten: 0,02435718 USD
- Checkpoints: 71, sequenziell
- Resume: 4.469 übersprungen, 0 neue Provider-Aufrufe
- Vektornormen in PostgreSQL: 0,999408 bis 1,000539 (Float32-Speicherung)

Ein zusätzlicher synthetischer semantischer Query-Smoke verbrauchte 51 Tokens
(0,00000102 USD) und rankte die paraphrasierte Basisevidenz auf Platz 1.

## RRF

Jede Rangliste leistet ausschließlich `1 / (k + Rang)`; Rohscores bleiben nur
Diagnostik. Der technische Default `k=60` und alle Kandidatenzahlen sind
konfigurierbar und nicht als klinisch optimal validiert. Sortierung und
Tie-Breaking sind deterministisch.

## Relationsexpansion

Nur die bestplatzierten Seeds werden limitiert erweitert. Direkte Evidenz und
`linked_context` bleiben unterscheidbar. Unterstützte Typen umfassen:

- Leitlinienitem → Rationale beziehungsweise Tabelle;
- Tabelle → explizit verknüpfter Parent-Kontext;
- Produkt → validierter Wirkstoff;
- Arzneimittel → Dosierung, Warnung, Gegenanzeige, Nebenwirkung;
- Produkt/Wirkstoff-Kontext.

Arzneimittelkontext wird nur aus gemeinsamen provenance-erhaltenen Produkt-IDs
abgeleitet; ungelöste IDs werden nicht automatisch klinisch gemappt. Kategorien
werden bei begrenztem Budget interleaved, damit lange Nebenwirkungslisten nicht
sämtlichen Dosierungs-/Warnkontext verdrängen.

Tabellenheaderpfade sind im Alt-Korpus nicht explizit codiert. Sie bleiben
`NULL`/leer mit QA-Flag; es werden keine Header erfunden.
