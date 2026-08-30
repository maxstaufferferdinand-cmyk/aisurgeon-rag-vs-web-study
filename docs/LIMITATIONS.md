# Bekannte Limitationen

- Der Prototyp ist nicht klinisch validiert und kein Medizinprodukt.
- Generalisierbarkeit ist auf drei Leitlinien und neun
  Arzneimittelinformations-PDFs begrenzt.
- 2.785 Review-Flags bleiben offen. Insbesondere sind nicht alle Quotes lokal
  verifiziert und nicht alle Dosis-/Häufigkeitsfelder explizit belegt.
- Die HCC/BCC-Quelle ist eine Konsultationsfassung. Sie ist sichtbar abrufbar,
  aber nie still als finale Leitlinie behandelt.
- 39 Produkt- und 30 Wirkstoffreferenzen sind bewusst ungelöst. Keine
  automatische klinische Entity-Zuordnung wird behauptet.
- Tabellenzeilen-/Spaltenkopfpfade sind im Alt-Korpus nicht explizit codiert;
  sie bleiben transparent leer/`NULL`. Nichtleere Zellen und der vollständige
  kanonische Tabellenpayload bleiben erhalten.
- Quelldaten enthalten 218 Records mit U+0000. PostgreSQL bewahrt ihre exakten
  Bytes in `bytea`; Suchprojektionen zeigen Escapes.
- RRF-k=60, Kandidatenbudgets und Trigrammschwelle sind technische Defaults,
  keine klinisch optimierten Hyperparameter.
- Dense Retrieval verwendet externe OpenAI-Embeddings. Korpus, Checkpoints und
  Datenbank sind lokal kontrolliert, die Embeddingberechnung selbst war ein
  klar abgegrenzter externer Schritt.
- Der kleine Structured-Output-Smoke belegt nur Schema-, Allowlist- und
  Validatorintegration. Er ist keine großflächige klinische Antwortvalidierung.
- Die 300 synthetischen Fragen sind Drafts, kein unabhängiger klinischer
  Goldstandard.
- Telemetrie misst nur lokal kontrollierte Prozesse, Datenbank und Container.
  Sie misst keine interne OpenAI- oder Google-Serverauslastung.
- Technische 100-%-Seiten-Coverage und vorhandene Quellenfelder garantieren
  weder vollständige klinische Extraktion noch korrekte Schlussfolgerungen.
