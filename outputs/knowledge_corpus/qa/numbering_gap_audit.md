# Audit der gemeldeten Itemnummernlücken

**Ergebnis:** Alle drei gemeldeten Lücken sind `source_native_numbering_gap` (Kategorie C). Es wurde keine Nummer erfunden.

| Lücke | Ergebnis | Quellenprüfung | Maßnahme |
|---|---|---|---|
| 15.7 | C – source_native_numbering_gap | Zwischen 15.6 und 15.8 ist keine formale Box 15.7 gedruckt. Auf Seite 139 steht stattdessen eine eigenständige, formal ausgezeichnete Box mit der erneut gedruckten Nummer 15.4. | Keine 15.7 ergänzt; zwei tatsächlich fehlende formale Haupttextitems auf PDF-S. 136 und 139 quellentreu aufgenommen. |
| 19.2 | C – source_native_numbering_gap | 19.2 ist auf Seite 151 eine Unterkapitelüberschrift, keine formale Itembox. Die zwischen 19.1 und 19.3 gedruckte formale Box trägt sichtbar die quellnative Duplikatnummer 15.4. | Keine 19.2 erfunden; bestehender Record behält die gedruckte 15.4 als Auditmetadatum und source_item_number=null. |
| 4.29 | C – source_native_numbering_gap | Die formale Haupttextbox auf Seite 152 zeigt Empfehlungsgrad, Evidenzlevel und Konsens, aber keine gedruckte Itemnummer. Die nächste gedruckte Nummer ist 4.30. | Keine 4.29 ergänzt; das gültige unnummerierte Haupttextitem bleibt unter stabiler interner ID erhalten. |

## Unnummeriertes HCC/BCC-Item auf PDF-Seite 152

`rec-3b4fd85be9e8c296bdca48da` ist eine gültige primäre Haupttextbox. Im Original ist keine Itemnummer gedruckt; `source_item_number` bleibt `null` und `item_number_status=not_printed_in_source`.

## Quellentreue Ergänzungen

Zwei zuvor fehlende formale VTE-Haupttextitems wurden aufgenommen: `rec-7a2c99c3a6dffc908b5eb111` (PDF-S. 136, gedruckt 15.4) und `rec-95dfba9075cff2c1f6940c9c` (PDF-S. 139, gedrucktes Nummernduplikat 15.4; kanonische Nummer bewusst null).

Gemini wurde nicht verwendet. Die beiden geprüften Quell-PDFs stimmen weiterhin mit dem eingefrorenen SHA-256-Manifest überein.
