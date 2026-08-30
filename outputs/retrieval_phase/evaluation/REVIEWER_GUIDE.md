# Reviewer-Anleitung für den Human-Goldstandard

## Status und Ziel

Alle Fragen in diesem Paket sind `synthetic_draft`. Sie sind Sampling- und
Annotationsträger, kein klinischer Goldstandard. Zwei Reviewer annotieren
unabhängig; erst die dokumentierte Adjudikation erzeugt Goldfelder.

## Verblindung

Verwenden Sie ausschließlich den zugeteilten Blindexport. Der unblinded
Authoring-Export enthält technische Seed-IDs und darf Reviewern nicht gezeigt
werden. Primärstratum, vorgesehener Scope und Seed-Evidenz sind im Blindexport
nicht enthalten.

## Vorgehen

1. Führen Sie den vollständigen Retrieval-Fallback im im Paket fixierten Corpus
   Snapshot aus.
2. Prüfen Sie Evidence-ID, exakten Quellspan, Dokumentkomponente, Version,
   Dokumentstatus und Seitenlocator im Backend.
3. Annotieren Sie jede erforderliche Evidence-ID; ein Seed ist niemals Gold.
4. Prüfen Sie Dosiswert, Einheit, Intervall, Route, Population und Negation
   getrennt. Fehlende Angaben dürfen nicht ergänzt werden.
5. Leitlinie und Fachinformation haben verschiedene Quellenrollen. Unterschiede
   sind kein automatischer Extraktionsfehler und dürfen nicht still aufgelöst
   werden.
6. Die HCC/BCC-Konsultationsfassung ist `consultation_draft`. Historische
   HCC/BCC-Änderungstabellen mit `excluded_by_policy` sind keine zulässige
   normale Evidenz.

## Labelvertrag

- `supported`: Der vollständige konkrete Claim wird durch die adjudizierte
  Evidenz getragen.
- `partially_supported`: Nur ein Teil ist getragen oder relevante
  Einschränkungen bleiben.
- `no_validated_evidence`: Erst nach vollständigem Retrieval-Fallback ist im
  freigegebenen Snapshot keine ausreichende Evidenz vorhanden.

Intern zusätzlich: `entailment_status`, `retrieval_outcome`, `conflict_status`
und `applicability_status`. Modell-Selbstvertrauen ist kein Kriterium.

## Adjudikation

Abweichungen werden feldweise dokumentiert. Der Adjudikator sieht beide
Begründungen, prüft die Quelle erneut und trägt die Entscheidung samt kurzer
Rationale ein. Bei ungelöstem fachlichem Dissens wird `requires_third_review`
verwendet; es wird kein Mehrheitslabel erfunden.
