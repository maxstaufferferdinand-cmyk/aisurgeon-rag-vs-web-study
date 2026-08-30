"""Source-derived provisional question bank for the Phase-2 study.

The questions are intentionally authored as synthetic drafts and are never
promoted to an independently reviewed clinical gold standard by this module.
Expected evidence IDs point only to policy-eligible retrieval units in the
sealed corpus snapshot.  Human approval remains mandatory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class CoveredDraft:
    text: str
    evidence_ids: tuple[str, ...]
    domain: str
    question_type: str
    difficulty: Literal["easy", "moderate", "hard"] = "moderate"
    required_claims: tuple[str, ...] = ()
    acceptable_variants: tuple[str, ...] = ()
    critical_omissions: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    relation_types: tuple[str, ...] = ()


def _c(
    text: str,
    evidence: str | tuple[str, ...],
    domain: str,
    question_type: str,
    claim: str,
    *,
    difficulty: Literal["easy", "moderate", "hard"] = "moderate",
    acceptable: tuple[str, ...] = (),
    omissions: tuple[str, ...] = (),
    forbidden: tuple[str, ...] = (),
    relations: tuple[str, ...] = (),
) -> CoveredDraft:
    return CoveredDraft(
        text=text,
        evidence_ids=(evidence,) if isinstance(evidence, str) else evidence,
        domain=domain,
        question_type=question_type,
        difficulty=difficulty,
        required_claims=(claim,),
        acceptable_variants=acceptable,
        critical_omissions=omissions,
        forbidden_claims=forbidden,
        relation_types=relations,
    )


# 15 VTE, 15 Pankreaskarzinom, 10 HCC/BCC consultation draft and 40 SmPC
# questions.  None is text-identical to a Phase-1 VTE development question.
COVERED_DRAFTS: tuple[CoveredDraft, ...] = (
    _c(
        "Welche einfachen nichtmedikamentösen Grundmaßnahmen sollte ein stationärer Patient unabhängig von seinem individuellen Thromboserisiko regelmäßig erhalten?",
        "ru-17bac05292fff9021867e999",
        "VTE-Prophylaxe",
        "recommendation",
        "Frühmobilisation, Bewegungsübungen und angeleitete Eigenübungen werden regelmäßig empfohlen.",
        difficulty="easy",
    ),
    _c(
        "Welchen Stellenwert haben standardisierte Risk Assessment Models bei der individuellen Abschätzung eines venösen Thromboembolierisikos?",
        "ru-a167bddee6a18e4a40aa153e",
        "VTE-Prophylaxe",
        "statement",
        "Risikostratifizierungsinstrumente können bei der individuellen Evaluation hilfreich sein.",
    ),
    _c(
        "Dürfen Mobilisation und Kompressionsmaßnahmen eine eigentlich indizierte medikamentöse Thromboseprophylaxe ersetzen?",
        "ru-6dd5e68be41ffb410e26a53f",
        "VTE-Prophylaxe",
        "negative_recommendation",
        "Basis- und physikalische Maßnahmen ersetzen eine indizierte medikamentöse Prophylaxe nicht.",
        forbidden=(
            "Physikalische Maßnahmen seien grundsätzlich gleichwertiger Ersatz.",
        ),
    ),
    _c(
        "Was muss vor dem ersten Heparingeben bezüglich einer möglichen heparininduzierten Thrombozytopenie bedacht werden?",
        "ru-4f654bf64c3fe35e807b4fc1",
        "VTE-Prophylaxe",
        "risk_assessment",
        "Das individuelle HIT-Risiko soll vor der Heparinanwendung bedacht und eingeschätzt werden.",
    ),
    _c(
        "Wie soll bei einem Behandlerwechsel verfahren werden, wenn die medikamentöse Thromboseprophylaxe weiterlaufen soll?",
        "ru-de680f03288887779adf187e",
        "VTE-Prophylaxe",
        "care_transition",
        "Die Fortführung ist an Weiterbehandelnde zu übergeben und der Patient ist entsprechend anzuhalten.",
    ),
    _c(
        "Welche Prophylaxe ist nach einem Eingriff am zentralen Nervensystem vorgesehen, solange Medikamente wegen Blutungsgefahr nicht eingesetzt werden können?",
        "ru-e1beeb880561566a6abfe927",
        "VTE-Prophylaxe",
        "patient_context",
        "Bei fehlender Möglichkeit einer medikamentösen Prophylaxe soll physikalisch prophylaktisch behandelt werden.",
    ),
    _c(
        "Welche VTE-Prophylaxe wird nach offenen Eingriffen an aortoiliakalen, renalen oder viszeralen Gefäßen bevorzugt?",
        "ru-8baf3d2205f90ee07b01d94b",
        "VTE-Prophylaxe",
        "recommendation",
        "Eine medikamentöse Prophylaxe, bevorzugt mit niedermolekularem Heparin, sollte erfolgen.",
    ),
    _c(
        "Ist nach einer Lebendspender-Teilleberresektion postoperativ eine medikamentöse Thromboseprophylaxe vorgesehen?",
        "ru-a145fd684e11eb07e1576fb3",
        "VTE-Prophylaxe",
        "patient_context",
        "Postoperativ sollte eine medikamentöse VTE-Prophylaxe, bevorzugt NMH, erfolgen.",
    ),
    _c(
        "Bis zu welchem funktionellen Endpunkt soll nach einer Operation mit notwendiger Entlastung des Beins die medikamentöse VTE-Prophylaxe fortgesetzt werden?",
        "ru-31d594555bce964afe0e9949",
        "VTE-Prophylaxe",
        "duration",
        "Die Prophylaxe sollte bis zum Erreichen der Vollbelastung fortgeführt werden.",
    ),
    _c(
        "Benötigen Patienten nach großen orthopädischen oder unfallchirurgischen Knieeingriffen eine medikamentöse VTE-Prophylaxe?",
        "ru-7f79156b63ee79d63f0a219c",
        "VTE-Prophylaxe",
        "recommendation",
        "Nach großen Knieeingriffen soll eine medikamentöse VTE-Prophylaxe erfolgen.",
    ),
    _c(
        "Wie ist bei einer gelenkübergreifenden Immobilisierung des Knies im Hartverband hinsichtlich einer Thromboseprophylaxe vorzugehen?",
        "ru-e6e3eef2b9b711ecffe23a99",
        "VTE-Prophylaxe",
        "patient_context",
        "Analog zu operierten Patienten soll eine medikamentöse VTE-Prophylaxe erfolgen.",
    ),
    _c(
        "Entfällt bei Knie- oder Hüftgelenkersatz im Fast-track-Protokoll die medikamentöse Thromboseprophylaxe?",
        "ru-ebdd4c7ddbeb84552a568a57",
        "VTE-Prophylaxe",
        "negative_recommendation",
        "Auch im Fast-track-Protokoll soll medikamentös prophylaktisch behandelt werden.",
        forbidden=("Fast-track mache eine Prophylaxe entbehrlich.",),
    ),
    _c(
        "Welche physikalische Methode wird nach elektiver Wirbelsäulenoperation zur VTE-Prophylaxe bevorzugt genannt?",
        "ru-93ae050fb4b41006c11ff7ff",
        "VTE-Prophylaxe",
        "recommendation",
        "Physikalische Maßnahmen, bevorzugt intermittierende pneumatische Kompression, sollten eingesetzt werden.",
    ),
    _c(
        "Für welchen Zeitraum wird bei motorisch kompletter oder inkompletter Querschnittlähmung eine medikamentöse VTE-Prophylaxe empfohlen?",
        "ru-3c356c3353841daa48a9f90c",
        "VTE-Prophylaxe",
        "duration",
        "Bevorzugt mit NMH sollte ab Eintritt der Querschnittlähmung 12 bis 24 Wochen prophylaktisch behandelt werden.",
        omissions=("Zeitraum 12 bis 24 Wochen",),
    ),
    _c(
        "Soll bei polytraumatisierten Patienten routinemäßig ein Vena-cava-inferior-Filter zur primären Thromboseprophylaxe eingesetzt werden?",
        "ru-3bec9a407fd42ae3bfc7f270",
        "VTE-Prophylaxe",
        "negative_recommendation",
        "Ein Vena-cava-inferior-Filter sollte nicht zur primären Prophylaxe eingesetzt werden.",
        forbidden=("Routinemäßige Filterimplantation empfehlen.",),
    ),
    _c(
        "Gibt es eine bestimmte Ernährungsweise, die zur Senkung des Pankreaskarzinomrisikos empfohlen werden kann?",
        "ru-1e3023e7bfb623c1faf0be89",
        "Pankreaskarzinom",
        "statement",
        "Eine spezifische Ernährungsempfehlung zur Risikoreduktion kann nicht gegeben werden.",
    ),
    _c(
        "Wann liegt aufgrund der Familienanamnese ein deutlich erhöhtes Risiko für ein Pankreaskarzinom vor?",
        "ru-604305810db0dfb652960fc2",
        "Pankreaskarzinom",
        "risk_assessment",
        "Das formale Item beschreibt ein deutlich erhöhtes familiäres Risiko unabhängig vom Genvariantenstatus.",
        difficulty="hard",
    ),
    _c(
        "Soll bei beschwerdefreien Menschen ohne erhöhtes Risiko routinemäßig nach einem Pankreaskarzinom gesucht werden?",
        "ru-89b9ccbb8fabf7ba3c8df53b",
        "Pankreaskarzinom",
        "negative_recommendation",
        "Asymptomatische Personen ohne erhöhtes Risiko sollen nicht gescreent werden.",
    ),
    _c(
        "Welche bildgebenden Verfahren kommen bei der ersten Surveillance-Untersuchung eines Hochrisikoindividuums für Pankreaskarzinom infrage?",
        "ru-823b830ce821a8552e8a7707",
        "Pankreaskarzinom",
        "diagnostics",
        "MRT/MRCP und/oder endoskopischer Ultraschall sollten eingesetzt werden.",
    ),
    _c(
        "Welche Untersuchungen gelten als diagnostische Verfahren der ersten Wahl zur Detektion eines Pankreaskarzinoms?",
        "ru-efa7529707e26382d7faf7bd",
        "Pankreaskarzinom",
        "diagnostics",
        "Oberbauchsonographie, Endosonographie, Multidetektor-CT sowie MRT mit MRCP werden genannt.",
        difficulty="hard",
    ),
    _c(
        "Welche Läsion sollte für die histologische Sicherung eines vermuteten Pankreaskarzinoms bevorzugt punktiert werden?",
        "ru-fd9f6db95e9720778991c559",
        "Pankreaskarzinom",
        "diagnostics",
        "Die am besten und mit möglichst geringem Risiko zugängliche Läsion sollte punktiert werden.",
    ),
    _c(
        "Welche der Verfahren ERCP, MRCP und Skelettszintigraphie sollen nicht für die Ausbreitungsdiagnostik beim Pankreaskarzinom eingesetzt werden?",
        "ru-c039ee72cad7ef8e238b4f84",
        "Pankreaskarzinom",
        "negative_recommendation",
        "ERCP, MRCP und Skelettszintigraphie sollten nicht zur Ausbreitungsdiagnostik herangezogen werden.",
    ),
    _c(
        "Warum kann bei einer zystischen Pankreasläsion eine Endosonographie sinnvoll sein?",
        "ru-f757531f47d527b7921a624e",
        "Pankreaskarzinom",
        "diagnostics",
        "Sie dient der Identifikation morphologischer Merkmale zur Einschätzung des Entartungsrisikos.",
    ),
    _c(
        "In welcher Versorgungssituation kann vor einer Pankreasoperation eine biliäre Drainage erwogen werden?",
        "ru-273466302315a0ef412f68f0",
        "Pankreaskarzinom",
        "patient_context",
        "Eine präoperative Galleableitung kann erwogen werden, wenn die Operation nicht zeitnah erfolgen kann.",
    ),
    _c(
        "Welche tumorbiologischen Befunde sprechen beim Pankreaskarzinom für eine Borderline-Resektabilität?",
        "ru-30089467bb61fd27d88a1868",
        "Pankreaskarzinom",
        "risk_assessment",
        "N-positive Lymphknoten und/oder präoperatives CA19-9 über 500 U/ml ohne relevante Cholestase werden genannt.",
        difficulty="hard",
    ),
    _c(
        "Unter welcher Voraussetzung soll bei synchron oligometastasiertem Pankreaskarzinom der Primärtumor reseziert werden?",
        "ru-0fd7e72d981752ce8802eb75",
        "Pankreaskarzinom",
        "restrictive_recommendation",
        "Die Resektion soll nur im Rahmen prospektiver Studien als Teil einer multimodalen Strategie erfolgen.",
    ),
    _c(
        "Welchen Stellenwert haben PARP-Inhibitoren nach platinhaltiger Vorbehandlung bei metastasiertem Pankreaskarzinom mit Keimbahn-BRCA1/2-Mutation?",
        "ru-50e78ec309b4b76b8ec4a311",
        "Pankreaskarzinom",
        "treatment",
        "PARP-Inhibitoren haben in dieser Situation einen Stellenwert als Erhaltungstherapie.",
    ),
    _c(
        "Wann kann nach Gemcitabin-basierter Vorbehandlung ein OFF-Regime mit 5-FU und Oxaliplatin als Zweitlinie angeboten werden?",
        "ru-1abd45de58716a91e27f7350",
        "Pankreaskarzinom",
        "treatment",
        "Das Item erlaubt die Zweitlinie unter den dort genannten klinischen Auswahlkriterien.",
        difficulty="hard",
    ),
    _c(
        "Wann ist bei nicht heilbarem Pankreaskarzinom eine spezialisierte Palliativversorgung angezeigt?",
        "ru-98545cdd4dd3ec86a8ed56fe",
        "Pankreaskarzinom",
        "palliative_care",
        "Bei hoher Komplexität der Situation soll spezialisierte Palliativversorgung erfolgen.",
    ),
    _c(
        "Welcher Stenttyp ist bei tumorbedingter biliärer Obstruktion grundsätzlich bevorzugt und wann kommt ein Plastikstent infrage?",
        "ru-b5e1198e1cbda60a9dbff773",
        "Pankreaskarzinom",
        "device_choice",
        "Metallstents sind Therapie der Wahl; Plastikstents bei geschätzter Überlebenszeit unter drei Monaten.",
        difficulty="hard",
    ),
    _c(
        "Welche chronischen Grunderkrankungen nennt die konsultierte HCC-Leitlinienfassung als wesentliche Risikofaktoren für ein Leberzellkarzinom?",
        "ru-a31a784308806dd5bb584684",
        "HCC/BCC",
        "risk_factors",
        "Das Statement nennt insbesondere chronische Lebererkrankungen im Zirrhosestadium als Risikofaktoren.",
        difficulty="hard",
    ),
    _c(
        "Ab welchem PAGE-B-Wert sollte bei chronischer Hepatitis B eine regelmäßige HCC-Früherkennung angeboten werden?",
        "ru-4f9dc40cc4d18681edbfe4ae",
        "HCC/BCC",
        "threshold",
        "Ab einem PAGE-B-Score von 10 sollte regelmäßige Früherkennung angeboten werden.",
        omissions=("Schwellenwert 10",),
    ),
    _c(
        "Welche Personengruppen sollen laut konsultierter Fassung gegen Hepatitis B geimpft werden?",
        "ru-813bf212107e901fb33fe351",
        "HCC/BCC",
        "prevention",
        "Das Item verweist auf die dort aufgeführten STIKO-Gruppen einschließlich Säuglingen und Neugeborenen.",
        difficulty="hard",
    ),
    _c(
        "Soll nach kurativ intendierter HCC-Behandlung bei gleichzeitig chronischer Hepatitis C eine DAA-Therapie angeboten werden?",
        "ru-859e107000cfa67c33985b36",
        "HCC/BCC",
        "treatment",
        "Eine DAA-Behandlung soll angeboten werden.",
    ),
    _c(
        "Welche pathologischen Kernelemente soll der Befund eines HCC-Resektats oder Leberexplantats enthalten?",
        "ru-a73eff55f65f5809196eeada",
        "HCC/BCC",
        "pathology",
        "Staging nach TNM, Tumortyp und Differenzierungsgrad sollen berichtet werden.",
    ),
    _c(
        "Welche drei klinischen Dimensionen sollen bei der Therapieentscheidung für ein HCC gemeinsam berücksichtigt werden?",
        "ru-a9d063ff4e273738f660c99f",
        "HCC/BCC",
        "decision_factors",
        "Tumorlast, Leberfunktion und Leistungsstatus sollen gemeinsam berücksichtigt werden.",
    ),
    _c(
        "Ist eine Lebertransplantation bei HCC mit extrahepatischen Manifestationen oder makrovaskulärer Invasion angezeigt?",
        "ru-d5e0521d20d818b4b9b9274c",
        "HCC/BCC",
        "negative_recommendation",
        "Bei diesen Befunden soll keine Lebertransplantation durchgeführt werden.",
        forbidden=("Transplantation trotz extrahepatischer Manifestation empfehlen.",),
    ),
    _c(
        "Welche bevorzugte Bildgebung nennt die konsultierte Fassung für Kontrollen nach lokoregionärer HCC-Therapie?",
        "ru-8db4575bed94145286358244",
        "HCC/BCC",
        "follow_up",
        "Bevorzugt wird mehrphasige MRT mit extrazellulärem Kontrastmittel, alternativ triphasische Bildgebung gemäß Item.",
    ),
    _c(
        "Wie soll bei HCC und Child-Pugh B bis acht Punkten zwischen Immuntherapie und Tyrosinkinasehemmer entschieden werden?",
        "ru-b823b11129e56b7e9f382910",
        "HCC/BCC",
        "treatment_choice",
        "Mangels Vergleichsdaten sollte individuell in einer interdisziplinären Tumorkonferenz entschieden werden.",
    ),
    _c(
        "Ist bei einem primär resektablen Cholangiokarzinom außerhalb klinischer Studien eine neoadjuvante Chemotherapie vorgesehen?",
        "ru-eaf09f17d1a684384886bbc4",
        "HCC/BCC",
        "negative_recommendation",
        "Außerhalb klinischer Studien soll keine neoadjuvante Chemotherapie erfolgen.",
        forbidden=("Neoadjuvante Chemotherapie routinemäßig empfehlen.",),
    ),
    # SmPC: 5-FU medac (4)
    _c(
        "Welche adjuvante Alternative nennt die Pankreaskarzinom-Leitlinie bei Gemcitabin-Unverträglichkeit, und zu welchem Wirkstoff führt der Handelsname 5-FU medac?",
        ("ru-068e5dc809613aa6a5a26144", "ru-c58cbb6e33e251eccaead984"),
        "Pankreaskarzinom/Arzneimittel",
        "smpc_guideline_bridge",
        "5-FU medac enthält 5-Fluorouracil; bei Gemcitabin-Unverträglichkeit nennt Item 7.7 adjuvantes 5-FU als Alternative.",
        difficulty="hard",
        relations=("smpc_product_substance_to_guideline_mention",),
    ),
    _c(
        "Auf welchem Weg wird 5-FU medac laut Fachinformation verabreicht?",
        "ru-f21b16dc7cde29003d2ad6b8",
        "5-Fluorouracil",
        "route",
        "Die Anwendung erfolgt intravenös als Bolus oder Infusion beziehungsweise Dauerinfusion.",
    ),
    _c(
        "Welche wesentlichen Gegenanzeigen nennt die Fachinformation für 5-Fluorouracil?",
        "ru-79f9f5706d57fa05747b42e3",
        "5-Fluorouracil",
        "contraindication",
        "Die Antwort muss die im Record aufgeführten Gegenanzeigen wiedergeben und darf keine ergänzen.",
        difficulty="hard",
    ),
    _c(
        "Welche sehr häufige dosislimitierende hämatologische Nebenwirkung ist bei 5-Fluorouracil beschrieben?",
        "ru-a3d51beaa4fab0d249d7881a",
        "5-Fluorouracil",
        "adverse_reaction",
        "Myelosuppression ist als sehr häufig und dosislimitierend beschrieben.",
    ),
    # Abraxane (4)
    _c(
        "Für welche pankreatische Tumorsituation ist Abraxane zusammen mit Gemcitabin zugelassen, und welches Leitlinienitem stützt diese Kombination?",
        ("ru-d22610b7ad283a505ab078d1", "ru-d8e490ea51dbf36405e93458"),
        "Pankreaskarzinom/Arzneimittel",
        "smpc_guideline_bridge",
        "Abraxane ist mit Gemcitabin für metastasiertes Pankreasadenokarzinom zugelassen; Leitlinienitem 8.13 beschreibt die Kombination unter Auswahlkriterien.",
        difficulty="hard",
        relations=("smpc_product_substance_to_guideline_mention",),
    ),
    _c(
        "Welche Dosis und welches Intervall nennt die Fachinformation für die Abraxane-Monotherapie?",
        "ru-96808120149a6f0553fe70b7",
        "Paclitaxel",
        "dose_interval",
        "260 mg/m² als intravenöse Infusion über 30 Minuten alle drei Wochen.",
        omissions=("260 mg/m²", "alle drei Wochen", "30 Minuten"),
    ),
    _c(
        "Bei welchem Ausgangswert der Neutrophilen darf Abraxane nicht gegeben werden?",
        "ru-ea7145f37e3fb1e9eb8133f8",
        "Paclitaxel",
        "contraindication_threshold",
        "Bei weniger als 1.500 Neutrophilen pro mm³ besteht eine Gegenanzeige.",
        omissions=("< 1.500 Zellen/mm³",),
    ),
    _c(
        "Welche Filterporengröße ist für die intravenöse Gabe der rekonstituierten Abraxane-Dispersion vorgesehen?",
        "ru-6eb8cb22beafb460e6a98365",
        "Paclitaxel",
        "preparation",
        "Ein Infusionsbesteck mit integriertem 15-µm-Filter wird angegeben.",
    ),
    # Cisplatin (4)
    _c(
        "Ist Cisplatin zusammen mit Gemcitabin Standard in der Erstlinie des metastasierten Pankreaskarzinoms, und welcher Wirkstoff ist in Cisplatin Teva enthalten?",
        ("ru-b98e1b29ad664a7da28a7e5c", "ru-5d74d62a0635340313709653"),
        "Pankreaskarzinom/Arzneimittel",
        "smpc_guideline_bridge",
        "Cisplatin Teva enthält Cisplatin; die Kombination mit Gemcitabin ist laut Item 8.15 kein Erstlinienstandard in dieser Situation.",
        difficulty="hard",
        relations=("smpc_product_substance_to_guideline_mention",),
    ),
    _c(
        "Welches Mehrtages-Dosierschema mit 15 bis 20 mg/m² nennt die Cisplatin-Fachinformation?",
        "ru-aa0211f41c2112a40b9e214d",
        "Cisplatin",
        "dose_interval",
        "15 bis 20 mg/m² täglich über fünf Tage, wiederholt alle drei bis vier Wochen.",
    ),
    _c(
        "Welche patientenbezogenen Gegenanzeigen sind für Cisplatin Teva beschrieben?",
        "ru-2ad1054a053377592a5fde9d",
        "Cisplatin",
        "contraindication",
        "Die Antwort muss auf die im Record genannten Gegenanzeigen begrenzt bleiben.",
        difficulty="hard",
    ),
    _c(
        "Über welchen Zeitraum soll Cisplatin intravenös infundiert werden und welches Material darf dabei nicht in Kontakt kommen?",
        "ru-b66675c96d911fbf080fd476",
        "Cisplatin",
        "preparation",
        "Die Infusion dauert sechs bis acht Stunden; aluminiumhaltiges Material ist zu vermeiden.",
    ),
    # Eliquis (5)
    _c(
        "Für welche postoperative VTE-Prophylaxe ist Eliquis zugelassen, und welches Leitlinienitem nennt den zugehörigen Wirkstoff Apixaban als Option?",
        ("ru-a5d29f1e93f639d49cbc0384", "ru-dc8ada4dca8f67ce2a0f1406"),
        "VTE/Arzneimittel",
        "smpc_guideline_bridge",
        "Eliquis ist nach elektivem Hüft- oder Kniegelenkersatz zugelassen; Leitlinienitem 8.1 nennt Apixaban als Option.",
        difficulty="hard",
        relations=("smpc_product_substance_to_guideline_mention",),
    ),
    _c(
        "Wie soll Eliquis eingenommen werden und ist die Einnahme an Mahlzeiten gebunden?",
        "ru-46977d5b95d3095e2138c98a",
        "Apixaban",
        "route",
        "Eliquis wird oral mit Wasser und unabhängig von Mahlzeiten eingenommen.",
    ),
    _c(
        "Welche vaskulären Läsionen werden als Gegenanzeigen für Apixaban aufgeführt?",
        "ru-3abfd39bb1100c80f4fb6b47",
        "Apixaban",
        "contraindication",
        "Ösophagusvarizen, AV-Malformationen, Aneurysmen sowie größere intraspinale oder intrazerebrale Gefäßanomalien werden genannt.",
        difficulty="hard",
    ),
    _c(
        "Mit welcher Häufigkeitskategorie ist Übelkeit unter Eliquis in der Fachinformation erfasst?",
        "ru-cb02bad210aefd308a33e854",
        "Apixaban",
        "adverse_reaction_frequency",
        "Übelkeit ist als häufig erfasst.",
    ),
    _c(
        "Welcher Wirkstoff steckt hinter dem Handelsnamen Eliquis?",
        "ru-46977d5b95d3095e2138c98a",
        "Apixaban",
        "product_substance_mapping",
        "Eliquis enthält Apixaban.",
        difficulty="easy",
        acceptable=("Apixaban",),
    ),
    # Enhertu (4)
    _c(
        "Für welche vorbehandelte HER2-positive Brustkrebssituation ist Enhertu als Monotherapie zugelassen?",
        "ru-b78634054c5aa84eb828d071",
        "Trastuzumab deruxtecan",
        "therapeutic_indication",
        "Zugelassen ist die Monotherapie bei inoperablem oder metastasiertem HER2-positivem Brustkrebs nach mindestens einer HER2-gerichteten Vorbehandlung.",
    ),
    _c(
        "Was ist laut Fachinformation mit Enhertu zu tun, wenn eine symptomatische Herzinsuffizienz auftritt?",
        "ru-bddcf72917ea6e7b1fb93149",
        "Trastuzumab deruxtecan",
        "dose_modification",
        "Enhertu ist bei symptomatischer kongestiver Herzinsuffizienz dauerhaft abzusetzen.",
    ),
    _c(
        "Welche In-line-Filtergrößen sind für die intravenöse Enhertu-Infusion vorgesehen?",
        "ru-8fca6165ac4839173558d4e1",
        "Trastuzumab deruxtecan",
        "preparation",
        "Vorgesehen sind 0,20-µm- oder 0,22-µm-In-line-Filter aus PES oder PSU.",
    ),
    _c(
        "Welche okulären Nebenwirkungen sind unter Enhertu als häufig aufgeführt?",
        "ru-431b34b8928bdc8a8f6bed69",
        "Trastuzumab deruxtecan",
        "adverse_reaction",
        "Trockenes Auge und verschwommenes Sehen sind als häufig aufgeführt.",
    ),
    # Keytruda (5)
    _c(
        "Für welche vollständig resezierte Melanomsituation ist Pembrolizumab adjuvant zugelassen?",
        "ru-f3a54ffa6cbd6efc63b60aa0",
        "Pembrolizumab",
        "therapeutic_indication",
        "Für vollständig reseziertes Melanom im Stadium III ist eine adjuvante Behandlung beschrieben.",
    ),
    _c(
        "Welches Pembrolizumab-Schema mit 200 mg wird in der Keytruda-Fachinformation beschrieben?",
        "ru-585b22d6e097e817219d5721",
        "Pembrolizumab",
        "dose_interval",
        "Pembrolizumab 200 mg alle drei Wochen wird beschrieben.",
    ),
    _c(
        "Welche Gegenanzeige nennt die Keytruda-Fachinformation?",
        "ru-b36e4877f036d78af12bfd28",
        "Pembrolizumab",
        "contraindication",
        "Gegenanzeige ist Überempfindlichkeit gegen Wirkstoff oder sonstige Bestandteile.",
    ),
    _c(
        "Ist Nephritis als unerwünschte Wirkung von Pembrolizumab in der Fachinformation erfasst?",
        "ru-05243a603a844d8b24725290",
        "Pembrolizumab",
        "adverse_reaction",
        "Nephritis ist als Nebenwirkung erfasst.",
    ),
    _c(
        "Welche Verabreichungsart ist für die Keytruda-Studienmedikation dokumentiert?",
        "ru-43ae56168e4a9bd4dc231add",
        "Pembrolizumab",
        "route",
        "Die Gabe ist als intravenöse Infusion dokumentiert.",
    ),
    # Lixiana (4)
    _c(
        "Für welche Behandlung und Rezidivprophylaxe venöser Thromboembolien ist Lixiana bei Erwachsenen zugelassen?",
        "ru-44f5f1dde4a58fd53057cf3e",
        "Edoxaban",
        "therapeutic_indication",
        "Lixiana ist zur Behandlung von TVT und LE sowie zur Rezidivprophylaxe zugelassen.",
    ),
    _c(
        "Welche Lixiana-Dosis nennt die pädiatrische Tabelle für Jugendliche ab zwölf Jahren mit mindestens 60 kg Körpergewicht?",
        "ru-272257ebfb1e99edd68ae818",
        "Edoxaban",
        "dose_population",
        "Für 12 bis unter 18 Jahre und mindestens 60 kg nennt die Tabelle 60 mg.",
    ),
    _c(
        "Darf Lixiana bei einer klinisch relevanten akuten Blutung angewendet werden?",
        "ru-240ca603f4dcd24242e00027",
        "Edoxaban",
        "contraindication",
        "Eine klinisch relevante akute Blutung ist eine Gegenanzeige.",
        forbidden=("Anwendung trotz akuter klinisch relevanter Blutung empfehlen.",),
    ),
    _c(
        "Kann Lixiana unabhängig von Mahlzeiten und bei Schluckproblemen zerkleinert eingenommen werden?",
        "ru-f5ca81b435da03c0a4412f08",
        "Edoxaban",
        "route_preparation",
        "Edoxaban kann unabhängig von Mahlzeiten eingenommen und bei Bedarf zerkleinert mit Wasser oder Apfelmus gegeben werden.",
    ),
    # Plavix (4)
    _c(
        "Bei welchen Erwachsenen ist Plavix zur Sekundärprävention atherothrombotischer Ereignisse indiziert?",
        "ru-2ccdd082d0f86a57585b4422",
        "Clopidogrel",
        "therapeutic_indication",
        "Die im Record genannten Gruppen nach Herzinfarkt, ischämischem Schlaganfall oder bei pAVK sind wiederzugeben.",
        difficulty="hard",
    ),
    _c(
        "Wie lautet die tägliche Erhaltungsdosis von Plavix bei Erwachsenen und älteren Patienten?",
        "ru-76a3bbfe9cf1bfdee5195976",
        "Clopidogrel",
        "dose_interval",
        "Einmal täglich 75 mg Clopidogrel.",
    ),
    _c(
        "Welche Blutungs- und Leberkonstellationen sprechen gegen die Anwendung von Plavix?",
        "ru-e8c1e7d80b0f032c6d69d8cd",
        "Clopidogrel",
        "contraindication",
        "Schwere Leberfunktionsstörung und akute pathologische Blutung sind Gegenanzeigen.",
    ),
    _c(
        "Muss Plavix mit einer Mahlzeit eingenommen werden?",
        "ru-dd2ae2e959847af468310017",
        "Clopidogrel",
        "route",
        "Plavix wird oral und unabhängig von Mahlzeiten eingenommen.",
    ),
    # Xarelto (6)
    _c(
        "Für welche elektiven Gelenkersatzoperationen ist Xarelto zur VTE-Prophylaxe zugelassen, und welches Leitlinienitem nennt Rivaroxaban als Option?",
        ("ru-2344a1f11e4de7652c9667bd", "ru-dc8ada4dca8f67ce2a0f1406"),
        "VTE/Arzneimittel",
        "smpc_guideline_bridge",
        "Die Zulassung umfasst elektiven Hüft- oder Kniegelenkersatz; Item 8.1 nennt Rivaroxaban als Option.",
        difficulty="hard",
        relations=("smpc_product_substance_to_guideline_mention",),
    ),
    _c(
        "Welches einmal tägliche Rivaroxaban-Schema ist in der Xarelto-Fachinformation dokumentiert?",
        "ru-814e26def4483f9945b22dc3",
        "Rivaroxaban",
        "dose_interval",
        "Rivaroxaban 20 mg einmal täglich.",
    ),
    _c(
        "Ist eine akute klinisch relevante Blutung eine Gegenanzeige für Xarelto?",
        "ru-ba7fbcd380c8577c51943fe0",
        "Rivaroxaban",
        "contraindication",
        "Eine akute klinisch relevante Blutung ist eine Gegenanzeige.",
    ),
    _c(
        "Wie soll die 20-mg-Xarelto-Tablette in Bezug auf Mahlzeiten eingenommen werden?",
        "ru-c495672e192005d7e18faaba",
        "Rivaroxaban",
        "route",
        "Die Tablette ist oral mit einer Mahlzeit einzunehmen.",
    ),
    _c(
        "Ist Urtikaria als Nebenwirkung von Rivaroxaban dokumentiert?",
        "ru-3342d694a94df539fceed6ca",
        "Rivaroxaban",
        "adverse_reaction",
        "Urtikaria ist als Nebenwirkung dokumentiert.",
    ),
    _c(
        "Welche Blutungsarten werden unter Xarelto besonders häufig berichtet?",
        "ru-b07583ea5344c086bc746562",
        "Rivaroxaban",
        "adverse_reaction_frequency",
        "Epistaxis und gastrointestinale Blutungen werden als am häufigsten gemeldete Blutungen genannt.",
        difficulty="hard",
    ),
)


NOT_COVERED_DRAFTS: tuple[tuple[str, str, str], ...] = (
    (
        "Welche orale Antibiotikatherapie ist bei unkomplizierter akuter Appendizitis bei Erwachsenen leitliniengerecht?",
        "Allgemeinchirurgie",
        "treatment",
    ),
    (
        "Welches empirische Antibiotikaregime wird bei ambulant erworbener Pneumonie ohne Risikofaktoren empfohlen?",
        "Infektiologie",
        "dose_treatment",
    ),
    (
        "Mit welcher Insulininfusionsrate wird eine diabetische Ketoazidose bei Erwachsenen initial behandelt?",
        "Endokrinologie",
        "dose_treatment",
    ),
    (
        "Welche initiale Flüssigkeitsmenge wird bei septischem Schock im Kindesalter empfohlen?",
        "Pädiatrie",
        "dose_treatment",
    ),
    (
        "Bis zu welchem Zeitfenster kann eine intravenöse Thrombolyse beim akuten ischämischen Schlaganfall erfolgen?",
        "Neurologie",
        "time_window",
    ),
    (
        "Welche Akutmedikation ist bei einem schweren Asthmaanfall eines Erwachsenen vorgesehen?",
        "Pneumologie",
        "treatment",
    ),
    (
        "Wann soll bei Diabetes mellitus und chronischer Nierenkrankheit ein SGLT2-Hemmer begonnen werden?",
        "Nephrologie",
        "treatment_threshold",
    ),
    (
        "Welche Laborkontrollen sind nach Beginn einer Methotrexattherapie bei rheumatoider Arthritis erforderlich?",
        "Rheumatologie",
        "monitoring",
    ),
    (
        "Wie wird ein konvulsiver Status epilepticus nach der ersten Benzodiazepingabe weiterbehandelt?",
        "Neurologie",
        "treatment",
    ),
    (
        "Welchen Stellenwert haben SGLT2-Hemmer bei Herzinsuffizienz mit erhaltener Ejektionsfraktion?",
        "Kardiologie",
        "treatment",
    ),
    (
        "Wie lange sollen systemische Glukokortikoide bei einer akuten COPD-Exazerbation gegeben werden?",
        "Pneumologie",
        "duration",
    ),
    (
        "Welches Antibiotikum wird bei asymptomatischer Bakteriurie in der Schwangerschaft bevorzugt?",
        "Gynäkologie",
        "treatment",
    ),
    (
        "Für welche Erwachsenen wird die Impfung gegen Herpes zoster empfohlen?",
        "Impfmedizin",
        "prevention",
    ),
    (
        "Ab welchen Blutdruckwerten soll eine schwere Hypertonie in der Schwangerschaft akut behandelt werden?",
        "Geburtshilfe",
        "threshold",
    ),
    (
        "Ab welchem Bilirubinwert benötigt ein reifes Neugeborenes eine Phototherapie?",
        "Neonatologie",
        "threshold",
    ),
    (
        "Wie wird Levothyroxin bei neu diagnostizierter primärer Hypothyreose initial dosiert?",
        "Endokrinologie",
        "dose_treatment",
    ),
    (
        "Wie lange sollte eine Bisphosphonattherapie bei postmenopausaler Osteoporose vor einer Therapiepause fortgeführt werden?",
        "Osteologie",
        "duration",
    ),
    (
        "Welche medikamentöse Malariaprophylaxe ist für eine Reise nach Ghana geeignet?",
        "Reisemedizin",
        "prevention",
    ),
    (
        "Welches Antidepressivum ist bei einer ersten mittelgradigen depressiven Episode bevorzugt?",
        "Psychiatrie",
        "treatment",
    ),
    (
        "Welches systolische Blutdruckziel gilt initial bei akuter Aortendissektion?",
        "Gefäßmedizin",
        "threshold",
    ),
)


assert len(COVERED_DRAFTS) == 80
assert len(NOT_COVERED_DRAFTS) == 20


__all__ = ["COVERED_DRAFTS", "NOT_COVERED_DRAFTS", "CoveredDraft"]
