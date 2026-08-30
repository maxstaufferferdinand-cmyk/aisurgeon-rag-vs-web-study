# Datenwörterbuch

Alle IDs sind stabile `text`-IDs aus den bestehenden Artefakten oder aus
deterministischen SHA-256-Präfixen. Unsichere Quellangaben bleiben `NULL` plus
QA-Flag; sie werden nicht erfunden.

## Kerntabellen

| Tabelle | Zweck | Wesentliche Felder |
|---|---|---|
| `corpus_snapshot` | unveränderliche Freigabeeinheit | `corpus_snapshot_id`, Content-Fingerprint, Schema-/Pipelineversion, Vorgänger, Status, Manifest |
| `corpus_artifact` | Hash- und Count-Bindung | relativer Pfad, Artefakttyp, SHA-256, Zeilen-/Bytezahl |
| `source_document` | logisches Dokument | stabile ID, Titel, `document_kind`, `source_authority` |
| `source_version` | konkrete PDF-Version | Status/Rolle/Authority, nullable Version und Daten, SHA-256, Seitenzahl, Komponenten, QA |
| `canonical_record` | regenerierbare Abbildung der JSONL-SoT | Recordtyp, Source-Version, exakter UTF-8-Text, Text-/Payload-Hash, Seiten, Eligibility/Exclusion, Raw-Payload |
| `evidence_span` | zitierbarer Originalspan oder Tabellenzelle | exakte UTF-8-Bytes, Seitenindex und 1-basierte Seiten, Druckseitenlabel, Tabellen-ID, Headerpfade, Zellentext, QA |
| `retrieval_unit` | aktive Such- und Embeddingeinheit | Dokumentstatus/-komponente/-rolle, Original- und Segmenttext, Kapitel, Itemtyp/-nummer, Seitenlocator, Entitäten, Dosisfelder, Aliase, Parent-/Relationsfelder, Eligibility |
| `formal_item` | formales Leitlinienitem | source-native Typ/Nummer, gedruckte Nummer, exakter Text, Empfehlungsgrad, Evidenzgrad, Konsens, Kapitel/Seite |
| `medicine_product` | 28 validierte Produktvarianten | Produktname, geprüfte Aliase, Wirkstoff-IDs, Stärke, Form, Route, Provenienz |
| `active_substance` | 10 validierte Wirkstoffe | bevorzugter Name, geprüfte Aliase, Provenienz |
| `entity_reference` | ungelöste IDs ohne erfundene Zuordnung | Entitytyp, `resolved`/`unresolved`, optionales Ziel, QA-Flags |
| `semantic_relation` | explizite typisierte Kante | Von-/Nach-Typ und -ID, optionale Retrieval-Endpoints, Evidenzrolle, QA |
| `retrieval_embedding` | Vektor plus Reproduzierbarkeit | Unit-ID, Modell, 1536, Cosinus, Text-/Quellhash, Zeitpunkt, Batch/Checkpoint, Usage/Kosten |
| `retrieval_run` | Metadaten eines Suchlaufs | Trace-ID, Snapshot, Routing, RRF-k, Outcome, Kanalstatus, Konfiguration |
| `retrieval_candidate` | Ränge je Kanal und Fusion | Unit-ID, Kanalrang/Rohscore, RRF-Score, finaler Rang, direct/linked |
| `evidence_package` | Backend-Allowlist | Snapshot/Run, ID-Liste, Paket-Hash |
| `answer_claim` | validierter Claim-Metadatensatz | Claimtext-Hash, öffentlicher Supportstatus, interne Statusachsen, Validatorstatus/Fehler |
| `claim_evidence` | Claim-zu-Evidence-Bindung | Unit-ID, direct/linked, Entailmentstatus |
| `retrieval_trace` | datensparsame Telemetrie | Kandidaten/Ränge, RRF, Evidence-IDs, Tokens/Kosten, Latenzen, Fehler, lokale Ressourcen, Validator |

## Bedeutende Felder

- `pdf_page_index`: 0-basierter technischer Index; nullable, niemals rekonstruiert.
- `pdf_pages_1based`: 1-basierte physische PDF-Seiten für Backend-Zitationen.
- `printed_page_label`: gedrucktes Label, sofern quellenseitig vorhanden.
- `source_native_item_number`: native Nummer oder `NULL`; Duplikate und
  unnummerierte Items erhalten transparente QA-Flags.
- `document_component`: `guideline`, `smPC`, `annex_ii`, `labelling`,
  `patient_information` oder `unknown`.
- `source_status`: unter anderem `final`, `current_at_snapshot`,
  `consultation_draft`.
- `source_role`: Retrievalrolle auf Recordebene, insbesondere `guideline` oder
  `smPC`.
- `conflict_status`: `none`, `guideline_vs_smpc`, `within_guideline`,
  `version_conflict`.
- `eligibility_status`: `eligible`, `ineligible` oder `review`.
- `excluded_by_policy` und `exclusion_reason`: harte normalpfadübergreifende
  Sperre; HCC-History verwendet `hcc_historical_change_table`.

## Verlustfreie NUL-Behandlung

PostgreSQL-`text` und `jsonb` können U+0000 nicht darstellen. 218 kanonische
Records enthalten zusammen 794 solche Quellbytes. Search-Projektionen zeigen
sie explizit als `\\u0000`; parallele `bytea`-Felder bewahren die exakten
UTF-8-Bytes. Validatoren hashen ausschließlich diese Raw-Bytes. Die kanonischen
JSONL-Dateien werden nicht verändert.

## Entitätsauflösung

Retrieval-Einheiten referenzieren 67 Produkt- und 40 Wirkstoff-IDs. Nur 28
Produkte und 10 Wirkstoffe besitzen validierte Entitäten. Deshalb bleiben 39
Produkt- und 30 Wirkstoffreferenzen explizit `unresolved`; Crosswalk-Aliaslisten
werden nicht als klinisch validierte Synonyme oder Foreign Keys erfunden.
