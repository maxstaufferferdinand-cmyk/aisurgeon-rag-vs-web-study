# Human-Annotation-Package

Deterministisch erzeugte Vorbereitung für 50 Development- und 250 versiegelte
Testfragen. Genau 25 % der Slots sind als No-evidence-/Out-of-scope-Kandidaten
stratifiziert. Sämtliche Fragen sind `synthetic_draft`; alle klinischen
Goldfelder sind leer.

- `authoring_items.jsonl`: unblinded technische Sampling-Provenienz, nicht an
  Reviewer verteilen
- `development_blind_questions.*`, `test_blind_questions.*`: blindierbare
  Fragen ohne Seed-, Scope- oder Stratumhinweise
- `reviewer_a_annotations.csv`, `reviewer_b_annotations.csv`: unabhängige leere
  Annotationstabellen in verschiedener Reihenfolge
- `adjudication_template.*`: leere feldweise Adjudikation
- `annotation.schema.json`, `adjudication.schema.json`: Datenverträge
- `sampling_manifest.json`: Snapshot, Quoten und Dateihashes
- `metrics_plan.json`: vorab spezifizierte technische und spätere Humanmetriken

Der Testsplit bleibt bis zum festgelegten Evaluationslauf unangetastet und darf
nicht für Prompt-, Retrieval- oder Schwellenwertoptimierung verwendet werden.
