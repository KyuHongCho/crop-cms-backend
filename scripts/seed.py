"""Seeds the crop corpora via the ORM (a script, not a migration: crops have no author endpoint).
ECOCROP `source`/`ecocrop_id` are pinned literals; tests/test_seed.py drift tests catch divergence.
Keyed on (crop, title, source): title/source edits and earlier seeds' stale basil rows need hand removal."""
from sqlalchemy.orm import Session

from app.db.migrate_db import engine as sync_engine
from app.model.model import Crop, Item, MainCategory, SubCategory

CROP = {
    "slug": "basil",
    "common_name": "basil",
    "scientific_name": "Ocimum basilicum",
    "ecocrop_id": 1547,
}

# the topic field, not the category tree, groups documents for retrieval (see Item in model.py);
# one flat sub-category is enough here.
MAIN_CATEGORY = {"slug": "basil-content", "name": "Basil content"}
SUB_CATEGORY = {"slug": "documents", "name": "Documents"}

TOPIC_OPTIMAL_TEMPERATURE = "optimal-temperature"

# marker so tests can find the draft by its body text, without hand-copying the string.
DRAFT_BODY_MARKER = "INTERNAL-DRAFT-DO-NOT-PUBLISH-BASIL-PROPAGATION-NOTE"

# deliberately absent from the advisor's registry, for registry-membership tests.
OFF_REGISTRY_SOURCE = "Folklore (no citable source)"

# --- documents ---
# each dict goes straight to Item(**...); "topic", "published", "read_directly" default below.

# FAO's general web-content terms (https://www.fao.org/contact-us/terms/en/, read 2026-10-03):
# copying allowed with acknowledgement of FAO and no implied endorsement. One shared note for
# every ECOCROP document.
_FAO_LICENCE_NOTE = (
    "(c) FAO. Source: FAO ECOCROP (https://ecocrop.apps.fao.org/). Reused under the "
    "FAO Terms and Conditions (https://www.fao.org/contact-us/terms/en/), which permit "
    "use for private study, research and teaching, and in non-commercial products or "
    "services, provided FAO is acknowledged as the source and copyright holder. Used "
    "here non-commercially. FAO's endorsement is not stated or implied."
)

# shared by every journal document: CC BY 4.0, read in full (read_directly=True, via=None).
_CC_BY = dict(
    read_directly=True,
    via=None,
    licence_note=(
        "CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Summarised and "
        "paraphrased from the cited article; changes were made. Not endorsed by the authors."
    ),
)

# the two basil temperature papers are not read directly: their figures were read in Walters, Tarr
# & Lopez (2023), CC BY 4.0. One shared note for both.
_CC_BY_VIA_NOTE = (
    "Read through Walters, Tarr & Lopez (2023), CC BY 4.0 "
    "(https://creativecommons.org/licenses/by/4.0/). Figure summarised from that "
    "article; changes were made. Not endorsed by the authors. The original papers "
    "(Chang et al. 2005; Walters & Currey 2019) were not read and their own licences "
    "were not checked."
)

_TEMPERATURE_DOCS = [
    dict(
        title="FAO ECOCROP on basil's optimal temperature",
        body=(
            "The FAO ECOCROP data sheet for Ocimum basilicum (id 1547) states an "
            "optimal temperature range of 18-27 degrees C, bounded absolutely by "
            "7-36 degrees C. No cultivation condition is stated."
        ),
        source="FAO ECOCROP (id 1547)",
        reference="FAO ECOCROP data sheet, id 1547",
        url="https://ecocrop.apps.fao.org/ecocrop/srv/en/dataSheet?id=1547",
        read_directly=True,
        via=None,
        condition=None,
        licence_note=_FAO_LICENCE_NOTE,
    ),
    dict(
        title="Chang, Alderson & Wright (2005) on basil's optimal temperature",
        body=(
            "Chang, Alderson & Wright (2005) report an optimal range of 25-30 "
            "degrees C, under a stated daily light integral of 20-22 mol m-2 d-1. "
            "Read through Walters, Tarr & Lopez (2023), not from the original journal."
        ),
        source="Chang, Alderson & Wright (2005)",
        reference="Chang, Alderson & Wright (2005), J. Hortic. Sci. Biotechnol. 80:593-598",
        url="https://pmc.ncbi.nlm.nih.gov/articles/PMC10688745/",
        read_directly=False,
        via="Walters, Tarr & Lopez (2023), PLoS One 18(11):e0294905",
        condition="DLI 20-22 mol m-2 d-1",
        licence_note=_CC_BY_VIA_NOTE,
    ),
    dict(
        title="Walters & Currey (2019) on basil's optimal temperature",
        body=(
            "Walters & Currey (2019) report a 29-35 degrees C optimum, "
            "conditioned on a stated daily light integral of 19.5 mol m-2 d-1. "
            "Does not agree with the ECOCROP band; both are published here and "
            "neither is ranked."
        ),
        source="Walters & Currey (2019)",
        reference="Walters & Currey (2019), HortScience 54(11):1915",
        url="https://pmc.ncbi.nlm.nih.gov/articles/PMC10688745/",
        read_directly=False,
        via="Walters, Tarr & Lopez (2023), PLoS One 18(11):e0294905",
        condition="DLI 19.5 mol m-2 d-1",
        licence_note=_CC_BY_VIA_NOTE,
    ),
]

_WATERING_DOCS = [
    dict(
        title="Substrate moisture and stem structure of container-grown basil (Driesen et al., 2021)",
        body=("Basil seedlings in small peat-compost-perlite containers were irrigated by "
              "flood-and-drain at four substrate moisture set-points, 20, 30, 40 and 50% water by "
              "volume, from near the wilting point to near container capacity. The driest set-point "
              "did not significantly reduce stem-plus-leaf dry weight (3.44 g per container against "
              "3.78 g for the wettest), and water-use efficiency was higher in the two driest "
              "set-points (2.4-2.5 g dry matter per litre against 1.5-1.7). The wetter set-points "
              "evaporated and transpired more water (1054-1180 mL per container against 769-797 mL), "
              "and the wettest gave stems with less collenchyma and lower bending strength."),
        source="Driesen et al. (2021)",
        reference=("Driesen E, De Proft M, Saeys W (2021). Soil Moisture Levels Affect the Anatomy and "
                   "Mechanical Properties of Basil Stems (Ocimum basilicum L.). Plants 10(7):1320. "
                   "doi:10.3390/plants10071320"),
        url="https://doi.org/10.3390/plants10071320",
        condition=("Ocimum basilicum cv. Marian, growth chamber (24/19 C, 16 h), 230 cm3 containers of "
                   "peat, compost and perlite with about 15 plants each, flood-and-drain nutrient "
                   "solution (EC about 0.7 mS/cm), about five weeks per run, two runs (Nov-Dec 2020, "
                   "Mar-Apr 2021), seedling stage; authors based at KU Leuven, Belgium, site not "
                   "stated in the methods"),
        **_CC_BY,
    ),
    dict(
        title="Drip irrigation level and yield of field-grown sweet basil (Sayarer et al., 2023)",
        body=("Sweet basil (Ocimum basilicum, a Malatya population) was grown under drip irrigation at "
              "100, 75, 50 or 25% of the full amount (the authors label these % FC), scheduled from "
              "pan evaporation, in two seasons on an alkaline, calcareous loam in semi-arid Eskisehir, "
              "Turkey. Fresh herb yield at 75% was statistically the same as at 100% in both years "
              "(7606 against 7625 kg per ha in 2016; 7065 against 7139 in 2017), while 25% gave about "
              "16% less in both years (6364 and 6000 kg per ha). The essential oil percentage rose as "
              "water fell (0.48% at 100% to 0.66% at 25% in 2016; 0.60% to 0.75% in 2017). Irrigation "
              "ran every 4 days in June and September and every 3.5 days in July and August."),
        source="Sayarer et al. (2023)",
        reference=("Sayarer M, Aytac Z, Kurkcuoglu M (2023). The Effect of Irrigation and Humic Acid "
                   "on the Plant Yield and Quality of Sweet Basil (Ocimum basilicum L.) with Mulching "
                   "Application under Semi-Arid Ecological Conditions. Plants 12(7):1522. "
                   "doi:10.3390/plants12071522"),
        url="https://doi.org/10.3390/plants12071522",
        condition=("Open field, drip irrigation, Eskisehir, Turkey, 2016 and 2017, soil pH 7.78 and "
                   "8.14, harvest at the start of flowering, cut once at the start of September, no "
                   "pesticides; yields are the no-mulch, pooled-humic-acid means"),
        **_CC_BY,
    ),
    dict(
        title="Irrigation interval and leaf yield of basil accessions in pots (Rahimi et al., 2023)",
        body=("Twenty-two basil accessions were grown in 3 kg pots of sandy clay loam outdoors in "
              "Mahan, Iran, and watered every 5 days (normal), 9 days (moderate deficit) or 13 days "
              "(severe deficit). Italian Genovese lost the least leaf yield, about 7% on the 9-day "
              "cycle and about 32% on the 13-day cycle, and Turkish Arzuman lost the most on the "
              "13-day cycle, about 43%. The accession-by-irrigation interaction was significant, so "
              "how much a longer interval costs depends on the accession."),
        source="Rahimi et al. (2023)",
        reference=("Rahimi M, Mortazavi M, Mianabadi A, Debnath S (2023). Evaluation of basil (Ocimum "
                   "basilicum) accessions under different drought conditions based on yield and "
                   "physio-biochemical traits. BMC Plant Biology 23:523. "
                   "doi:10.1186/s12870-023-04554-8"),
        url="https://doi.org/10.1186/s12870-023-04554-8",
        condition=("Pots under field conditions, Mahan, Iran (2021 or 2022, the paper gives both), 3 "
                   "kg pots of soil:sand:manure 2:2:1, three replicates, labelled O. basilicum "
                   "throughout; two of the 22 accessions are named Holy Thai and Holy (Tulsi), so only "
                   "the named sweet basil accessions are quoted"),
        **_CC_BY,
    ),
]

_SOIL_PH_DOCS = [
    dict(
        title="Soil pH, growth and manganese uptake in sweet basil (Adamczyk-Szabela & Wolf, 2022)",
        body=("Sweet basil, dandelion and lemon balm were sown in pots of an organic, acidic soil left "
              "at pH 6.0, lowered to 4.7 with sulphuric acid, or raised to about 8.7 with calcium "
              "oxide (8.5 in the results tables). Basil grew tallest in the unmodified pH 6.0 soil, "
              "and the alkaline soil strongly inhibited growth in all three species. The ratio of "
              "manganese in basil shoots to soil manganese was 1.05 at pH 4.7, 0.58 at pH 6.0 and 0.52 "
              "at pH 8.5, and the acid soil held more bioavailable manganese (133 against 99 ug per g "
              "at pH 6.0). One soil and one pH step either side were tested, so this does not define a "
              "best range."),
        source="Adamczyk-Szabela & Wolf (2022)",
        reference=("Adamczyk-Szabela D, Wolf WM (2022). The Impact of Soil pH on Heavy Metals Uptake "
                   "and Photosynthesis Efficiency in Melissa officinalis, Taraxacum officinalis, "
                   "Ocimum basilicum. Molecules 27(15):4671. doi:10.3390/molecules27154671"),
        url="https://doi.org/10.3390/molecules27154671",
        condition=("Ocimum basilicum L. (sweet basil, cultivar not stated), pot experiment in soil "
                   "(not hydroponic), 12 cm pots with about 20 seeds, five pots per pH, organic soil "
                   "with 32.5% organic matter from Lagiewniki, Poland, greenhouse 23/16 C day/night, "
                   "16 h, deionised water, three months (March-July 2019)"),
        **_CC_BY,
    ),
]

_PEST_DOCS = [
    dict(
        title="Cultural practices against sweet basil downy mildew (Omer et al., 2021)",
        body=("Sweet basil 'Peri', which is susceptible to downy mildew (Peronospora belbahrii), was "
              "grown in Israeli greenhouses and walk-in tunnels in 2013-2015 with infected plants "
              "placed beside the plots as the source of the epidemic. Night-time air circulation cut "
              "disease severity by up to 72.5% without changing yield, and gray or transparent "
              "polyethylene mulch cut it by up to about 65% and usually raised yield. Halving plant "
              "density (14-15 against 24-30 plants per m2) lowered severity by roughly 20-68% but cut "
              "yield by 12-31% in the greenhouse trials, and tunnels oriented north-south had less "
              "disease than east-west ones. All measures gave only intermediate control."),
        source="Omer et al. (2021)",
        reference=("Omer C, Nisan Z, Rav-David D, Elad Y (2021). Effects of Agronomic Practices on the "
                   "Severity of Sweet Basil Downy Mildew (Peronospora belbahrii). Plants 10(5):907. "
                   "doi:10.3390/plants10050907"),
        url="https://doi.org/10.3390/plants10050907",
        condition=("Ocimum basilicum cv. Peri; commercial-scale greenhouses (tuff substrate, drip "
                   "fertigation) and walk-in tunnels (sandy soil, brackish water) at two Israeli "
                   "research stations, 2013-2015, natural epidemic seeded with infected plants"),
        **_CC_BY,
    ),
    dict(
        title="Downy mildew resistance genes in sweet basil (Ben Naim et al., 2025)",
        body=("About 150 sweet basil entries were scored for downy mildew in six experiments, two "
              "growth-chamber seedling trials and four field trials, using two pathogen isolates (race "
              "0 and race 1). Lines carrying both resistance genes Pb1 and Pb2 showed complete "
              "resistance as seedlings to both races; Pb1 alone resisted race 0 but was as susceptible "
              "as ordinary Pb0 cultivars to race 1, and Pb2 lines had high but incomplete resistance. "
              "Pb0 cultivars and 'Eleonora' were susceptible to both races, and seedling scores "
              "correlated with adult field scores (R = 0.80 for race 0, 0.66 for race 1 across all "
              "backgrounds)."),
        source="Ben Naim et al. (2025)",
        reference=("Ben Naim Y, Mattera R, Cohen Y, Wyenandt CA, Simon JE (2025). Predicting the "
                   "resistance of basil entries to downy mildew based on their genetics, pathogen "
                   "race, growth stage, and environmental conditions. Planta 262:10. "
                   "doi:10.1007/s00425-025-04703-3"),
        url="https://doi.org/10.1007/s00425-025-04703-3",
        condition=("Breeding lines and cultivars labelled Ocimum basilicum; Pb1 derives from O. "
                   "americanum PI 500945 introgressed into sweet basil; growth chambers (seedlings, "
                   "2-4 leaves) and field trials in Israel and New Jersey, USA; isolates 'Knafo 3' "
                   "(race 0) and 'Mop Negev' (race 1)"),
        **_CC_BY,
    ),
    dict(
        # not a registry source -- see OFF_REGISTRY_SOURCE above.
        title="Folk remedy: basil planted beside tomatoes repels pests",
        body=(
            "Traditional companion-planting lore holds that basil grown beside "
            "tomatoes repels aphids and hornworms and improves tomato flavour. "
            "Unlike the temperature claims above, this is folk tradition, not a "
            "sourced study, and is recorded here as exactly that."
        ),
        source=OFF_REGISTRY_SOURCE,
        reference="Traditional companion-planting lore, as commonly repeated in gardening guides",
        # ItemBase.url has min_length=1; "folklore" means there is no single citable source.
        url="(no single citable source -- oral/traditional)",
    ),
]

_PROPAGATION_DOCS = [
    dict(
        title="Light when raising basil seedlings from seed, not stem cuttings (Walters & Lopez, 2022)",
        body=("Sweet basil 'Nufar' seeds were sown in stone-wool cubes and grown for two weeks in "
              "growth chambers under LEDs at 100, 200, 400 or 600 umol per m2 per s (16 h), with CO2 "
              "at 500 or 1000 umol per mol. Seedling mass depended on light, not CO2: fresh mass rose "
              "from 0.134 g at 100 to 0.515 g at 600. After three weeks in one common hydroponic "
              "greenhouse, plants raised at 600 were 24% taller, had 56% more branches and 80% more "
              "fresh and dry mass than those raised at 100, and had a higher eugenol concentration, "
              "while linalool, 1,8-cineole and methyl chavicol did not differ."),
        source="Walters & Lopez (2022)",
        reference=("Walters KJ, Lopez RG (2022). Basil seedling production environment influences "
                   "subsequent yield and flavor compound concentration during greenhouse production. "
                   "PLOS ONE 17(8):e0273562. doi:10.1371/journal.pone.0273562"),
        url="https://doi.org/10.1371/journal.pone.0273562",
        condition=("Covers light during seedling raising from seed, NOT stem cuttings or rooting; "
                   "Ocimum basilicum 'Nufar'; two-week seedling stage in growth chambers, then three "
                   "weeks in a deep-flow hydroponic glass greenhouse at 43 N (authors at University of "
                   "Tennessee and Michigan State University, USA); three runs 2017-2018"),
        **_CC_BY,
    ),
    dict(
        # unpublished draft fixture; DRAFT_BODY_MARKER lets draft-leak tests find it.
        title="DRAFT -- basil propagation, needs a second opinion before publishing",
        body=(
            f"{DRAFT_BODY_MARKER}: rooting hormone gel may speed root formation "
            "on stem cuttings, but this has not been checked against a second "
            "source yet -- do not publish until it has."
        ),
        source="Internal review notes",
        reference="Internal review notes (unpublished)",
        # ItemBase.url has min_length=1 (app/schema/item.py).
        url="(internal -- unpublished, no external URL yet)",
        published=False,
    ),
]

_TOPIC_GROUPS = [
    (TOPIC_OPTIMAL_TEMPERATURE, _TEMPERATURE_DOCS),
    ("watering-needs", _WATERING_DOCS),
    ("soil-ph", _SOIL_PH_DOCS),
    ("pest-and-disease", _PEST_DOCS),
    ("propagation", _PROPAGATION_DOCS),
]

# --- the other crops ---
# gaps stay absent: a topic with no verified source is not seeded. `watering-needs` is about
# how much/often/when water is supplied; composition, salinity or system type is `nutrient-solution`.

LETTUCE = {
    "slug": "lettuce",
    "common_name": "lettuce",
    "scientific_name": "Lactuca sativa",
    "ecocrop_id": 1313,
}
LETTUCE_CATEGORY = {"slug": "lettuce-content", "name": "Lettuce content"}

STRAWBERRY = {
    "slug": "strawberry",
    "common_name": "strawberry",
    "scientific_name": "Fragaria x ananassa",
    "ecocrop_id": 1112,
}
STRAWBERRY_CATEGORY = {"slug": "strawberry-content", "name": "Strawberry content"}

_LETTUCE_TEMPERATURE_DOCS = [
    dict(
        title="FAO ECOCROP on lettuce's optimal temperature",
        body=("The FAO ECOCROP data sheet for Lactuca sativa var. capitata (id 1313) states an "
              "optimal temperature range of 12-21 degrees C, bounded absolutely by "
              "5-30 degrees C. No cultivation condition is stated."),
        source="FAO ECOCROP (id 1313)",
        reference="FAO ECOCROP data sheet, id 1313",
        url="https://ecocrop.apps.fao.org/ecocrop/srv/en/dataSheet?id=1313",
        condition=None,
        licence_note=_FAO_LICENCE_NOTE,
    ),
]

_LETTUCE_WATERING_DOCS = [
    dict(
        title="Irrigation systems for greenhouse lettuce (Chen et al., 2019)",
        body=("In a Beijing greenhouse (soil culture, cv. 'sheshou 101') furrow irrigation was compared with "
              "micro-sprinkler irrigation, plastic-film mulch, and mulch combined with micro-sprinklers, for a "
              "spring and an autumn crop in 2015. In the spring crop, whole-plant fresh weight was 7.22%, 36.77% "
              "and 43.20% higher than under furrow irrigation, and biological water-use efficiency was 20.93 "
              "(furrow), 25.24, 36.81 and 38.54 kg per cubic metre."),
        source="Chen et al. (2019)",
        reference=("Chen Z, Han Y, Ning K, Luo C, Sheng W, Wang S, Fan S, Wang Y, Wang Q (2019). Assessing the "
                   "performance of different irrigation systems on lettuce (Lactuca sativa L.) in the greenhouse. "
                   "PLOS ONE 14(2):e0209329. doi:10.1371/journal.pone.0209329"),
        url="https://doi.org/10.1371/journal.pone.0209329",
        condition="Greenhouse soil culture (not hydroponic), cv. sheshou 101, spring crop, Beijing",
        **_CC_BY,
    ),
    dict(
        title="Deficit irrigation in two leaf-lettuce cultivars (Malejane et al., 2017)",
        body=("In field trials in Pretoria, South Africa, two leaf lettuces ('Lollo Bionda' and 'Vera') were "
              "irrigated at management allowable depletion (MAD) of 25, 50 and 75%. Compared with MAD 25%, "
              "'Vera' lost about 8.9% of yield at MAD 50%; at MAD 75% the losses were 34% ('Vera') and 58% "
              "('Lollo Bionda'). The response depended on the cultivar."),
        source="Malejane et al. (2017)",
        reference=("Malejane, Tinyani, Soundy, Sultanbawa & Sivakumar (2017). Deficit irrigation improves phenolic "
                   "content and antioxidant activity in leafy lettuce varieties. Food Science & Nutrition 6(2):334-341 "
                   "(online 2017; print issue 2018). doi:10.1002/fsn3.559"),
        url="https://doi.org/10.1002/fsn3.559",
        condition="Open-field trial, leaf cultivars (not var. capitata), Pretoria, South Africa",
        **_CC_BY,
    ),
]

_LETTUCE_NUTRIENT_SOLUTION_DOCS = [
    dict(
        title="Biostimulants and yield of hydroponic lettuce under salinity (Ikiz et al., 2024)",
        body=("In a 45-day greenhouse floating-culture trial, Batavia lettuce 'Caipira' grew in "
              "aerated nutrient solution salinised with 50 mM NaCl. Salt alone cut yield to 10.23 kg per "
              "square metre and mean plant weight to 230 g (about 408 g without salt). Vermicompost or "
              "plant-growth-promoting bacteria restored yield to about 17.9 kg per square metre (+75%); "
              "fulvic acid (+51%), amino acids (+31%), mycorrhizal fungi (+34%) and chitosan (+33%) gave "
              "smaller gains."),
        source="Ikiz et al. (2024)",
        reference=("Ikiz B, Dasgan HY, Balik S, Kusvuran S, Gruda NS (2024). The use of biostimulants as a key "
                   "to sustainable hydroponic lettuce farming under saline water stress. BMC Plant Biology "
                   "24:808. doi:10.1186/s12870-024-05520-8"),
        url="https://doi.org/10.1186/s12870-024-05520-8",
        condition="Hydroponic floating culture, 50 mM NaCl in the nutrient solution, 45 days, cv. Caipira (Batavia)",
        **_CC_BY,
    ),
    dict(
        title="Lettuce growth in four hydroponic systems with a liquid organic fertiliser (Chowdhury et al., 2024)",
        body=("'Green Butter' lettuce was grown for four weeks on one liquid organic fertiliser in nutrient "
              "film technique (NFT), deep water culture (DWC), Dutch bucket and plastic-container systems. "
              "Growth in the plastic container was 29-60% higher than in NFT and 15-44% higher than in DWC on "
              "shoot width, leaf number, leaf area and shoot fresh and dry weight. Dutch bucket and container "
              "growth were similar, and root growth was similar in NFT and DWC but lower than in the "
              "substrate-based systems."),
        source="Chowdhury et al. (2024)",
        reference=("Chowdhury M, Samarakoon UC, Altland JE (2024). Evaluation of hydroponic systems for organic "
                   "lettuce production in controlled environment. Frontiers in Plant Science 15:1401089. "
                   "doi:10.3389/fpls.2024.1401089"),
        url="https://doi.org/10.3389/fpls.2024.1401089",
        condition="Controlled environment, cv. Green Butter, liquid organic fertiliser, four weeks",
        **_CC_BY,
    ),
    dict(
        title="Lettuce in aquaculture-supplemented versus conventional hydroponic solution (Goddek & Vermeulen, 2018)",
        body=("Two NFT systems ran in a Dutch plastic-tunnel greenhouse from August to October. One "
              "hydroponic solution received 40 L of carp recirculating-aquaculture water every two weeks, "
              "with pH kept at 5.0-6.0; the other was a conventional solution. Lettuce in the "
              "aquaculture-supplemented system finished 7.9% heavier in fresh weight and 33.2% heavier in "
              "dry weight, even though the added water carried relatively high sodium."),
        source="Goddek & Vermeulen (2018)",
        reference=("Goddek S, Vermeulen T (2018). Comparison of Lactuca sativa growth performance in conventional "
                   "and RAS-based hydroponic systems. Aquaculture International 26(6):1377-1386. "
                   "doi:10.1007/s10499-018-0293-8"),
        url="https://doi.org/10.1007/s10499-018-0293-8",
        condition="NFT in a plastic-tunnel greenhouse, Bleiswijk, The Netherlands, August-October",
        **_CC_BY,
    ),
]

_LETTUCE_PEST_DOCS = [
    dict(
        title="Root microbiome of hydroponic lettuce infected with Phytophthora cryptogea (Vlasselaer et al., 2024)",
        body=("Roots were sampled from three commercial hydroponic lettuce greenhouses naturally infested with "
              "Phytophthora cryptogea; symptomatic plants (brown or necrotic roots) were compared with "
              "nonsymptomatic ones, with the pathogen confirmed by qPCR. Infection significantly changed the "
              "root-associated bacterial community, mostly in the rhizosphere, and communities also differed "
              "between greenhouses. One Flavobacterium type was more abundant on symptomatic plants in all three "
              "greenhouses and a Pseudomonas type in two, while a Sphingobium type and a different Flavobacterium "
              "type were more abundant on healthy plants in two. This describes association, not cause."),
        source="Vlasselaer et al. (2024)",
        reference=("Vlasselaer L, Crauwels S, Lievens B, De Coninck B (2024). Unveiling the microbiome of "
                   "hydroponically cultivated lettuce: impact of Phytophthora cryptogea infection on "
                   "plant-associated microorganisms. FEMS Microbiology Ecology 100(3):fiae010. "
                   "doi:10.1093/femsec/fiae010"),
        url="https://doi.org/10.1093/femsec/fiae010",
        condition="Three commercial hydroponic greenhouses, natural infection, butterhead and multicolour cultivars",
        **_CC_BY,
    ),
    dict(
        title="Aquaponic water and Pythium root rot of lettuce (Stouvenakers et al., 2020)",
        body=("Pythium aphanidermatum spreads by swimming zoospores in recirculated water, so reusing water "
              "raises the risk of root rot. In this study aquaponic water inhibited the pathogen in the "
              "laboratory, and lettuce ('Millennia RZ') grown in it showed significantly fewer disease symptoms "
              "than lettuce in hydroponic water or in aquaponic water supplemented with mineral nutrients. The "
              "authors link the suppression to the composition and diversity of the root microbiota, and "
              "report that it was lost when nutrients were added to the aquaponic water."),
        source="Stouvenakers et al. (2020)",
        reference=("Stouvenakers G, Massart S, Depireux P, Jijakli MH (2020). Microbial Origin of Aquaponic Water "
                   "Suppressiveness against Pythium aphanidermatum Lettuce Root Rot Disease. Microorganisms "
                   "8(11):1683. doi:10.3390/microorganisms8111683"),
        url="https://doi.org/10.3390/microorganisms8111683",
        condition="Lettuce var. Millennia RZ in rockwool, inoculated with P. aphanidermatum; aquaponic, hydroponic and supplemented-aquaponic water",
        **_CC_BY,
    ),
    dict(
        title="Insect pests of lettuce in an aquaponic system (da Silva et al., 2024)",
        body=("A survey in an aquaponic system with lambari fish in Cravinhos, Sao Paulo, Brazil, sampled 10 "
              "plants per bed each week over 13 cultivation cycles between February and September 2021. Thrips "
              "(Frankliniella schultzei and Caliothrips phaseoli) and the aphid Aphis spiraecola predominated, "
              "and temperature and humidity significantly affected their numbers. Natural enemies included "
              "predatory thrips and the ladybugs Cycloneda sanguinea, Eriopis connexa and Hippodamia convergens."),
        source="da Silva et al. (2024)",
        reference=("da Silva, Cividanes, Salles, Perticarrari, Cunha & Santos-Cividanes (2024). Insect pests and "
                   "natural enemies associated with lettuce Lactuca sativa L. (Asteraceae) in an aquaponics "
                   "system. Scientific Reports 14(1):14947. doi:10.1038/s41598-024-63938-4"),
        url="https://doi.org/10.1038/s41598-024-63938-4",
        condition="Aquaponic system, Cravinhos (Sao Paulo), Brazil, February-September 2021",
        **_CC_BY,
    ),
]

_LETTUCE_PROPAGATION_DOCS = [
    dict(
        title="X-ray seed priming of lettuce in hydroponic culture (Sorrentino et al., 2024)",
        body=("Lettuce and lamb's lettuce (Valerianella locusta) seeds were exposed to X-rays (1 or 10 Gy) "
              "before sowing in hydroponic culture. Lettuce mean germination time fell from 8.5 days (control) "
              "to 5.5 days at 1 Gy and 7 days at 10 Gy, and biomass increased, especially in lettuce. Lamb's "
              "lettuce responded differently: its germination percentage fell at first and its mean germination "
              "time did not change significantly, so the authors conclude the dose must be fitted to the species."),
        source="Sorrentino et al. (2024)",
        reference=("Sorrentino MC, Granata A, Cantalupo M, Manti L, Pugliese M, Giordano S, Capozzi F, Spagnuolo V "
                   "(2024). Seed Priming by Low-Dose Radiation Improves Growth of Lactuca sativa and Valerianella "
                   "locusta. Plants 13(2):165. doi:10.3390/plants13020165"),
        url="https://doi.org/10.3390/plants13020165",
        condition="Hydroponic culture, X-ray doses 1 and 10 Gy",
        **_CC_BY,
    ),
    dict(
        title="Heat and lettuce seed germination (Wei et al., 2024)",
        body=("A 2024 review states that most lettuce cultivars germinate best between 15 and 22 degrees C and "
              "that germination is inhibited above that range; experiments it cites report inhibition at "
              "temperatures from 28 up to 35 degrees C. It attributes both temporary (thermoinhibition) and "
              "permanent (heat stress) inhibition to hardening of the endosperm, which stops the radicle from "
              "emerging."),
        source="Wei et al. (2024)",
        reference=("Wei J, Zhang Q, Zhang Y, Yang L, Zeng Z, Zhou Y, Chen B (2024). Advance in the Thermoinhibition "
                   "of Lettuce (Lactuca sativa L.) Seed Germination. Plants 13(15):2051. "
                   "doi:10.3390/plants13152051"),
        url="https://doi.org/10.3390/plants13152051",
        condition="Review of other authors' experiments, not a single trial",
        **_CC_BY,
    ),
    dict(
        title="Genetics of heat-inhibited germination in lettuce (Oh et al., 2025)",
        body=("A genome-wide association study of lettuce accessions found that heat inhibited germination less "
              "in dark-coloured seeds than in white ones, and that wild Lactuca serriola, the progenitor of "
              "cultivated lettuce, was less heat-inhibited than cultivated lettuce. Regions linked to ethylene "
              "and abscisic-acid signalling were associated with the trait and overlapped with seed-colour genes."),
        source="Oh et al. (2025)",
        reference=("Oh, Ahn, Shi, Mou & Park (2025). Genome-wide association studies in lettuce reveal the "
                   "interplay of seed age, color, and germination under high temperatures. Scientific Reports 15(1):733. "
                   "doi:10.1038/s41598-024-84197-3"),
        url="https://doi.org/10.1038/s41598-024-84197-3",
        condition="Diverse Lactuca accessions, germination under high temperature",
        **_CC_BY,
    ),
]

_STRAWBERRY_TEMPERATURE_DOCS = [
    dict(
        title="FAO ECOCROP on strawberry's optimal temperature",
        body=("The FAO ECOCROP data sheet for Fragaria x ananassa (id 1112) states an optimal temperature "
              "range of 11-24 degrees C, bounded absolutely by 6-28 degrees C. No cultivation condition is stated."),
        source="FAO ECOCROP (id 1112)",
        reference="FAO ECOCROP data sheet, id 1112",
        url="https://ecocrop.apps.fao.org/ecocrop/srv/en/dataSheet?id=1112",
        condition=None,
        licence_note=_FAO_LICENCE_NOTE,
    ),
]

_STRAWBERRY_WATERING_DOCS = [
    dict(
        title="Drip density for hydroponic strawberry (Jeong et al., 2026)",
        body=("In recycling hydroponics, 'Kuemsil' strawberries on coir received drip irrigation at three "
              "densities: three, six or nine drippers per slab. Daily volumes were 40-90 mL per plant (three "
              "drippers), 80-160 mL (six) and 160-240 mL (nine), given for one minute every 30 minutes between "
              "09:00 and 14:00. Six drippers gave the best balance of marketable yield and fruit quality; nine "
              "gave the highest total fruit but more unmarketable fruit (under 10 g)."),
        source="Jeong et al. (2026)",
        reference=("Jeong Y, Zebro M, Kim M, Rabbani MG, Choi KY (2026). Optimizing drip irrigation density enhances "
                   "growth, yield, physiology, and antioxidant capacity of Korean strawberry 'Kuemsil' in "
                   "recycling hydroponics. PeerJ 14:e21637. doi:10.7717/peerj.21637"),
        url="https://doi.org/10.7717/peerj.21637",
        condition="Recycling hydroponics, coir, October 2024 to May 2025, cv. Kuemsil",
        **_CC_BY,
    ),
]

_STRAWBERRY_NUTRIENT_SOLUTION_DOCS = [
    dict(
        title="Nutrient-solution strength for hydroponic strawberry (Zebro et al., 2025)",
        body=("'Kuemsil' strawberries were grown for 175 days (September 2024 to May 2025) in a recycling "
              "hydroponic system on coconut coir, with the nutrient solution at one-third, one-half, "
              "two-thirds or full strength. Two-thirds strength gave the best overall balance of growth, "
              "photosynthesis, antioxidant activity and fruit quality (fruit weight, firmness, soluble solids "
              "and potassium, magnesium and phosphorus content), while one-third strength gave the lowest values."),
        source="Zebro et al. (2025)",
        reference=("Zebro M, Baek JH, Kim M, Jeong Y, Rabbani MG, Choi KY (2025). Nutrient solution strength affects "
                   "growth, physiology, biochemistry and fruit quality of Korean strawberry 'Kuemsil' in a "
                   "recycling hydroponic system. Frontiers in Plant Science 16:1685755. "
                   "doi:10.3389/fpls.2025.1685755"),
        url="https://doi.org/10.3389/fpls.2025.1685755",
        condition="Recycling hydroponics, coconut coir, greenhouse, 175 days, cv. Kuemsil",
        **_CC_BY,
    ),
]

_STRAWBERRY_PEST_DOCS = [
    dict(
        title="Oxygen and Pythium root rot in strawberry NFT (Scott & Villouta, 2026)",
        body=("A 2026 review reports that nutrient film technique, introduced for strawberry in the 1970s, was "
              "abandoned in the United Kingdom, Belgium and the Netherlands in the 1980s in favour of peat and "
              "coir substrates. It argues that two causes are linked through root-zone oxygen: dissolved oxygen "
              "falls as the root mat grows (a recommended minimum in commercial production is 5 mg per litre, and "
              "measurements in soilless strawberry show a decline of nearly 40% between winter and spring), and "
              "Pythium root rot exploits hypoxic roots. Because strawberry is unusually oxygen-sensitive and forms "
              "no aerenchyma, thresholds set for tomato and cucumber do not apply, and a strawberry-specific "
              "threshold has not been established."),
        source="Scott & Villouta (2026)",
        reference=("Scott S, Villouta C (2026). Dissolved oxygen limitation and Pythium root rot in strawberry NFT "
                   "systems: mechanisms, research gaps, and prospects for substrate-free production. Frontiers "
                   "in Plant Science 17:1829367. doi:10.3389/fpls.2026.1829367"),
        url="https://doi.org/10.3389/fpls.2026.1829367",
        condition="Review of other authors' studies; NFT strawberry production",
        **_CC_BY,
    ),
    dict(
        title="Spider mite thresholds on strawberry (Abdelmaksoud et al., 2026)",
        body=("A two-season (2019 and 2020) field study in Egypt related two-spotted spider mite (Tetranychus "
              "urticae) density to strawberry yield, with infestation levels set up using chitosan treatments. "
              "Yield fell by about 1.25% per mite. The authors estimate equilibrium positions of about 2.2-2.8 "
              "mites per leaf and action thresholds of 4.0 mites per leaf (2019) and 3.4 (2020), and favour "
              "preventive over reactive control."),
        source="Abdelmaksoud et al. (2026)",
        reference=("Abdelmaksoud EM, El-Refai SA, Mahmoud KW, El-Sayed W, Ragab ME (2026). Estimation of economic "
                   "injury levels and action thresholds for the two-spotted spider mite, Tetranychus urticae "
                   "Koch, on strawberry plants. Experimental & Applied Acarology 97(3):25. "
                   "doi:10.1007/s10493-026-01159-2"),
        url="https://doi.org/10.1007/s10493-026-01159-2",
        condition="Open-field strawberry, Egypt (not hydroponic), two seasons",
        **_CC_BY,
    ),
]

_STRAWBERRY_PROPAGATION_DOCS = [
    dict(
        title="Light and runner propagation of strawberry in a plant factory (Chen et al., 2024)",
        body=("'Akihime' mother plants were grown for 115 days in a plant factory under different LED spectra to "
              "produce runner plants. White LEDs gave 23-30% more runner-plant dry mass than white-plus-red "
              "mixes. Compared with red-and-blue LEDs, replacing some blue with green raised the number of runner "
              "plants by 16% and of runners by 19%. The authors conclude that reducing the red proportion in "
              "full-spectrum LEDs benefits runner propagation."),
        source="Chen et al. (2024)",
        reference=("Chen J, Ji F, Gao R, He D (2024). Reducing red light proportion in full-spectrum LEDs enhances "
                   "runner plant propagation by promoting the growth and development of mother plants in "
                   "strawberry. Frontiers in Plant Science 15:1465004. doi:10.3389/fpls.2024.1465004"),
        url="https://doi.org/10.3389/fpls.2024.1465004",
        condition="Plant factory with artificial lighting, cv. Akihime, mother plants in 1.5 L pots, 115 days",
        **_CC_BY,
    ),
    dict(
        title="Thidiazuron and strawberry shoot multiplication in tissue culture (Wang et al., 2025)",
        body=("Strawberry cultures were treated with thidiazuron at 0, 0.025, 0.05, 0.1 and 0.4 mg per litre for "
              "four weeks. 0.05 mg per litre gave the best shoot multiplication; higher concentrations induced "
              "more callus. Thidiazuron also reduced plant height, chlorophyll, carotenoid and soluble sugar, "
              "lowered endogenous cytokinin and raised auxin."),
        source="Wang et al. (2025)",
        reference=("Wang F, Li Y, Pang Y, Hu J, Kang X, Qian C (2025). Thidiazuron Enhances Strawberry Shoot "
                   "Multiplication by Regulating Hormone Signal Transduction Pathways. International Journal of "
                   "Molecular Sciences 26(9):4060. doi:10.3390/ijms26094060"),
        url="https://doi.org/10.3390/ijms26094060",
        condition="In vitro tissue culture, four weeks",
        **_CC_BY,
    ),
]

_LETTUCE_TOPIC_GROUPS = [
    (TOPIC_OPTIMAL_TEMPERATURE, _LETTUCE_TEMPERATURE_DOCS),
    ("watering-needs", _LETTUCE_WATERING_DOCS),
    ("nutrient-solution", _LETTUCE_NUTRIENT_SOLUTION_DOCS),
    ("pest-and-disease", _LETTUCE_PEST_DOCS),
    ("propagation", _LETTUCE_PROPAGATION_DOCS),
]

_STRAWBERRY_TOPIC_GROUPS = [
    (TOPIC_OPTIMAL_TEMPERATURE, _STRAWBERRY_TEMPERATURE_DOCS),
    ("watering-needs", _STRAWBERRY_WATERING_DOCS),
    ("nutrient-solution", _STRAWBERRY_NUTRIENT_SOLUTION_DOCS),
    ("pest-and-disease", _STRAWBERRY_PEST_DOCS),
    ("propagation", _STRAWBERRY_PROPAGATION_DOCS),
]

# --- tomato, cucumber, sweet pepper and kale ---
# same rules as above: no soil-ph topic, journal documents read_directly=True via=None under
# _CC_BY, and the ECOCROP document carries _FAO_LICENCE_NOTE.

# ECOCROP id 1379 is titled "Lycopersicon esculentum"; the accepted name is used here (the pin is the id).
TOMATO = {
    "slug": "tomato",
    "common_name": "tomato",
    "scientific_name": "Solanum lycopersicum",
    "ecocrop_id": 1379,
}
TOMATO_CATEGORY = {"slug": "tomato-content", "name": "Tomato content"}

CUCUMBER = {
    "slug": "cucumber",
    "common_name": "cucumber",
    "scientific_name": "Cucumis sativus",
    "ecocrop_id": 817,
}
CUCUMBER_CATEGORY = {"slug": "cucumber-content", "name": "Cucumber content"}

SWEET_PEPPER = {
    "slug": "sweet-pepper",
    "common_name": "sweet pepper",
    "scientific_name": "Capsicum annuum",
    "ecocrop_id": 618,
}
SWEET_PEPPER_CATEGORY = {"slug": "sweet-pepper-content", "name": "Sweet pepper content"}

KALE = {
    "slug": "kale",
    "common_name": "kale",
    "scientific_name": "Brassica oleracea var. acephala",
    "ecocrop_id": 3867,
}
KALE_CATEGORY = {"slug": "kale-content", "name": "Kale content"}

_TOMATO_TEMPERATURE_DOCS = [
    dict(
        title="FAO ECOCROP on tomato's optimal temperature",
        body=("The FAO ECOCROP data sheet for Lycopersicon esculentum (id 1379, now accepted as "
              "Solanum lycopersicum) states an optimal temperature range of 20-27 degrees C, "
              "bounded absolutely by 7-35 degrees C. No cultivation condition is stated."),
        source="FAO ECOCROP (id 1379)",
        reference="FAO ECOCROP data sheet, id 1379",
        url="https://ecocrop.apps.fao.org/ecocrop/srv/en/dataSheet?id=1379",
        condition=None,
        licence_note=_FAO_LICENCE_NOTE,
    ),
]

_TOMATO_NUTRIENT_SOLUTION_DOCS = [
    dict(
        title="Soilless systems and saline water for greenhouse tomato (Rodriguez-Ortega et al., 2019)",
        body=("'Optima' tomatoes were grown for 108 days in deep flow, nutrient film (NFT) and perlite "
              "systems, each with nutrient solution at 2.2, 6.3 or 10.2 dS per metre (0, 40 or 80 mM NaCl). "
              "Marketable yield averaged 5.0 kg per plant in deep flow, 4.2 in perlite and 3.8 in NFT, and "
              "rose salinity lowered it progressively. Water use per plant fell from about 176-195 litres "
              "without salt to about 132-164 litres at the highest salinity."),
        source="Rodriguez-Ortega et al. (2019)",
        reference=("Rodriguez-Ortega WM, Martinez V, Nieves M, Simon I, Lidon V, Fernandez-Zapata JC, "
                   "Martinez-Nicolas JJ, Camara-Zapata JM, Garcia-Sanchez F (2019). Agricultural and "
                   "Physiological Responses of Tomato Plants Grown in Different Soilless Culture Systems "
                   "with Saline Water under Greenhouse Conditions. Scientific Reports 9:6733. "
                   "doi:10.1038/s41598-019-42805-7"),
        url="https://doi.org/10.1038/s41598-019-42805-7",
        condition="Soilless greenhouse culture (deep flow, NFT, perlite), cv. Optima, Murcia, Spain, April-July 2014, one season",
        **_CC_BY,
    ),
    dict(
        title="Nutrient-solution replacement thresholds in NFT cherry tomato (Signore et al., 2016)",
        body=("Cherry tomato 'Naomi' grew in a closed NFT system in which the nutrient solution was fully "
              "replaced only when its EC passed 5, 7.5 or 10 dS per metre. Yield did not differ (about 3.05 kg "
              "per plant) and each plant used about 137 litres of solution over the crop. Waiting until 10 "
              "dS per metre meant the solution was never replaced and gave about 59% less total nutrient "
              "discharge (sodium and chloride excluded) than the 5 dS threshold, with sweeter fruit "
              "(up to 9.3 degrees Brix)."),
        source="Signore et al. (2016)",
        reference=("Signore A, Serio F, Santamaria P (2016). A Targeted Management of the Nutrient Solution in a "
                   "Soilless Tomato Crop According to Plant Needs. Frontiers in Plant Science 7:391. "
                   "doi:10.3389/fpls.2016.00391"),
        url="https://doi.org/10.3389/fpls.2016.00391",
        condition="Closed NFT, cherry tomato cv. Naomi, greenhouse, Mola di Bari, Italy, about 167-169 days, first of two trials",
        **_CC_BY,
    ),
    dict(
        title="Vapour-pressure deficit and salinity in hydroponic tomato (Puppala et al., 2025)",
        body=("Two tomato genotypes, 'Saluoso' and 'Sweeterno', grew hydroponically in growth chambers at "
              "high (3.1 kPa) or low (1.9 kPa) vapour-pressure deficit, with or without 30 mM NaCl, and were "
              "harvested 24 days after planting. Drier air raised total dry weight from 4.26 to 7.09 g "
              "(Saluoso) and from 4.91 to 7.27 g (Sweeterno). With 30 mM NaCl shoot biomass did not differ "
              "significantly from the unsalted plants and the benefit of drier air was mostly lost. The "
              "chambers ran hot (day temperature about 32 degrees C) and only the vegetative stage was studied."),
        source="Puppala et al. (2025)",
        reference=("Puppala HK, Germer J, Asch F (2025). Genotypic Responses to Combined Effects of VPD and "
                   "Salinity in Hydroponically Grown Tomato and Cucumber. Plant-Environment Interactions "
                   "6(3):e70064. doi:10.1002/pei3.70064"),
        url="https://doi.org/10.1002/pei3.70064",
        condition="Recirculating hydroponics, growth chambers, Hohenheim, Germany, vegetative stage, 24 days after planting",
        **_CC_BY,
    ),
]

_TOMATO_PEST_DOCS = [
    dict(
        title="Yeast priming against Fusarium crown and root rot in hydroponic tomato (Kthiri et al., 2025)",
        body=("'Rio Grande' seedlings in small boxes of Hoagland nutrient solution were treated at the root with "
              "the yeast Meyerozyma guilliermondii and then inoculated with Fusarium oxysporum f. sp. "
              "radicis-lycopersici. Infection cut biomass by 59% in untreated plants and by about 38% in treated "
              "plants, and mean disease severity on a 0-5 scale was 3.75 untreated against 0.75 treated. Treated "
              "plants also showed raised defence-gene and enzyme activity before infection, which the authors "
              "read as priming. This is one seedling experiment, not a greenhouse or grower trial."),
        source="Kthiri et al. (2025)",
        reference=("Kthiri Z, Ben Jabeur M, Karmous C, Hamada W (2025). Yeast to the rescue: Meyerozyma "
                   "guilliermondii primes tomato vigor and resistance to Fusarium crown and root rot. Plant "
                   "Signaling & Behavior 20(1):2596486. doi:10.1080/15592324.2025.2596486"),
        url="https://doi.org/10.1080/15592324.2025.2596486",
        condition="Non-circulating hydroponics (400 mL Hoagland per box), growth chamber 22 degrees C, cv. Rio Grande, seedlings with three true leaves, severity scored 20 days after inoculation",
        **_CC_BY,
    ),
    dict(
        title="Whitefly control with a predatory bug on greenhouse tomato (Smith & Krey, 2019)",
        body=("Cages of four potted tomato plants infested with sweetpotato whitefly (Bemisia tabaci) received "
              "4, 8 or 12 adults of the predatory bug Dicyphus hesperus a week for three weeks. Cages with "
              "predators had fewer whitefly eggs and nymphs than untreated cages, but the three release rates "
              "did not differ, and even the highest left nymph numbers the authors judged above the "
              "treatment threshold. They conclude it helps if released early, at low whitefly numbers, but "
              "is not enough alone."),
        source="Smith & Krey (2019)",
        reference=("Smith HA, Krey KL (2019). Three Release Rates of Dicyphus hesperus (Hemiptera: Miridae) for "
                   "Management of Bemisia tabaci (Hemiptera: Aleyrodidae) on Greenhouse Tomato. Insects "
                   "10(7):213. doi:10.3390/insects10070213"),
        url="https://doi.org/10.3390/insects10070213",
        condition="Potted tomato in potting mix (not hydroponic), caged greenhouse benches with a mullein banker plant, cv. Lanai, Florida, USA, fall 2016",
        **_CC_BY,
    ),
    dict(
        title="Pathogens in recirculating tomato nutrient solution before and after biofiltration (Picot et al., 2020)",
        body=("Water was sampled at three commercial hydroponic tomato greenhouses in Brittany, France, including "
              "recirculating nutrient solution before and after a slow biofilter in one of them. Plate counts "
              "fell about 100-fold for total bacteria and about 1000-fold for Fusarium oxysporum after "
              "filtration, and Pythium and Saprolegnia were no longer detected; bacterial diversity rose while "
              "fungal diversity fell. The authors stress that these are propagules of genera that include "
              "pathogens, with no pathogenicity tests, one sampling date and one recirculating greenhouse."),
        source="Picot et al. (2020)",
        reference=("Picot A, Cobo-Diaz JF, Pawtowski A, Donot C, Legrand F, Le Floch G, Deniel F (2020). Water "
                   "Microbiota in Greenhouses With Soilless Cultures of Tomato by Metabarcoding and "
                   "Culture-Dependent Approaches. Frontiers in Microbiology 11:1354. doi:10.3389/fmicb.2020.01354"),
        url="https://doi.org/10.3389/fmicb.2020.01354",
        condition="Three commercial hydroponic tomato greenhouses, Brittany, France, sampled once in April 2017; biofilter study in one greenhouse",
        **_CC_BY,
    ),
]

_TOMATO_PROPAGATION_DOCS = [
    dict(
        title="Adventitious rooting of tomato stem cuttings and auxin (Guan et al., 2019)",
        body=("Four-week-old tomato shoots were cut and rooted in soil or in hydroponic Hoagland solution. "
              "Untreated cuttings showed root primordia about 3 days after cutting and a working root system "
              "by 7-9 days. Adding 10 micromolar IAA gave about eight times more primordia at 3 days and about "
              "four times more, and twice as long, roots at 5 days, while the auxin-transport blocker NPA cut "
              "primordia about sevenfold. This is root-formation biology in one cultivar, not a rooting protocol "
              "or a comparison of planting success."),
        source="Guan et al. (2019)",
        reference=("Guan L, Tayengwa R, Cheng ZM, Peer WA, Murphy AS, Zhao M (2019). Auxin regulates adventitious "
                   "root formation in tomato cuttings. BMC Plant Biology 19:435. "
                   "doi:10.1186/s12870-019-2002-9"),
        url="https://doi.org/10.1186/s12870-019-2002-9",
        condition="Stem cuttings in hydroponic Hoagland solution (pH 5.8, 25 degrees C), cv. Ailsa Craig, laboratory growth room",
        **_CC_BY,
    ),
]

_CUCUMBER_TEMPERATURE_DOCS = [
    dict(
        title="FAO ECOCROP on cucumber's optimal temperature",
        body=("The FAO ECOCROP data sheet for Cucumis sativus (id 817) states an optimal "
              "temperature range of 18-32 degrees C, bounded absolutely by 6-38 degrees C. "
              "No cultivation condition is stated."),
        source="FAO ECOCROP (id 817)",
        reference="FAO ECOCROP data sheet, id 817",
        url="https://ecocrop.apps.fao.org/ecocrop/srv/en/dataSheet?id=817",
        condition=None,
        licence_note=_FAO_LICENCE_NOTE,
    ),
]

_CUCUMBER_WATERING_DOCS = [
    dict(
        title="Irrigation level for greenhouse cucumber (Behzadipour et al., 2024)",
        body=("In a greenhouse trial, cucumbers were irrigated at 80, 90 or 100% of soil field capacity "
              "or by conventional flooding. Yield was 9.5 kg per square metre at 80% and 11.89 at 100% "
              "of field capacity, close to the 12.23 under flooding, which used the most water "
              "(0.148 cubic metres per square metre against 0.102 at 80%). A sensor-controlled system set "
              "to 100% of field capacity then raised water-use efficiency by 15.6% over flooding the next season."),
        source="Behzadipour et al. (2024)",
        reference=("Behzadipour F, Ghasemi-Nejad-Raeini M, Mehdizadeh SA, Taki M, Moghadam BK, "
                   "Bavani MRZ (2024). Optimizing water use efficiency in greenhouse cucumber "
                   "cultivation: A comparative study of intelligent irrigation systems. PLOS ONE "
                   "19(10):e0311699. doi:10.1371/journal.pone.0311699"),
        url="https://doi.org/10.1371/journal.pone.0311699",
        condition=("Greenhouse soil culture (not soilless), Khuzestan, Iran, two cultivation stages "
                   "September 2021 to August 2023; cultivar not stated; observational data"),
        **_CC_BY,
    ),
]

_CUCUMBER_NUTRIENT_SOLUTION_DOCS = [
    dict(
        title="Nutrient-solution strength for coir-grown cucumber (He et al., 2024)",
        body=("Cucumber 'Deltastar' on coir slabs received nutrient solution at EC 2, 5 or 8 dS per metre. "
              "Photosynthesis and chlorophyll fell as EC rose, and measured primary and secondary "
              "metabolites generally peaked at 5 dS per metre. At 8 dS per metre the plants showed stress "
              "signs (more protein and amino acids, upregulated stress genes). The paper reports no yield "
              "figures, so it supports fruit-quality and physiology effects only."),
        source="He et al. (2024)",
        reference=("He L, Xu W, Zhou D, Yan J, Jin H, Zhang H, Cui J, Miao C, Zhang Y, Zhou Q, Yu J, Yu X, "
                   "Ding X (2024). The Impact of Nutrient Solution Electrical Conductivity on Leaf "
                   "Transcriptome Contributing to the Fruit Quality of Cucumber Grown in Coir Cultivation. "
                   "International Journal of Molecular Sciences 25(22):11864. doi:10.3390/ijms252211864"),
        url="https://doi.org/10.3390/ijms252211864",
        condition=("Greenhouse coir slabs, cv. Deltastar, EC 2/5/8 dS/m, drip irrigation with about 20% drainage; "
                   "authors in Shanghai, trial location and duration not stated; n = 3"),
        **_CC_BY,
    ),
    dict(
        title="Salinity and humidity response of hydroponic cucumber seedlings (Puppala et al., 2025)",
        body=("In recirculating hydroponics, two cucumber genotypes were harvested 24 days after planting at "
              "a vapour pressure deficit (VPD) of 3.1 or 1.9 kPa, with 0 or 30 mM NaCl. VPD did not "
              "change total dry weight, but 30 mM NaCl did: Addison fell from about 15.5 to 9.5 g and Proloog "
              "from about 13.8-13.4 to 10.3-9.9 g, whichever VPD applied. Tomato, tested alongside, was less "
              "affected by salt."),
        source="Puppala et al. (2025)",
        reference=("Puppala HK, Germer J, Asch F (2025). Genotypic Responses to Combined Effects of VPD and Salinity in "
                   "Hydroponically Grown Tomato and Cucumber. Plant-Environment Interactions "
                   "6(3):e70064. doi:10.1002/pei3.70064"),
        url="https://doi.org/10.1002/pei3.70064",
        condition=("Hydroponic recirculating boxes in growth chambers, University of Hohenheim, Germany, "
                   "genotypes Addison and Proloog, vegetative stage only (24 days), n = 3"),
        **_CC_BY,
    ),
]

_CUCUMBER_PEST_DOCS = [
    dict(
        title="A Pseudomonas endophyte against Pythium damping-off of cucumber (Amaradasa et al., 2024)",
        body=("Cucumber 'Marketmore 76' seedlings in pots were treated with the bacterial endophyte IALR1619 "
              "(a Pseudomonas) and then exposed to Pythium ultimum. With the pathogen added one day later, "
              "59.1% survived against 18.2% untreated; added five days later, 74.1% against 35.7%. Both "
              "differences were significant, though the two runs also used different inoculum forms."),
        source="Amaradasa et al. (2024)",
        reference=("Amaradasa BS, Mei C, He Y, Chretien RL, Doss M, Durham T, Lowman S (2024). Biocontrol "
                   "potential of endophytic Pseudomonas strain IALR1619 against two Pythium species in "
                   "cucumber and hydroponic lettuce. PLOS ONE 19(2):e0298514. doi:10.1371/journal.pone.0298514"),
        url="https://doi.org/10.1371/journal.pone.0298514",
        condition=("Seedlings in pots of potting mix (not soilless), indoors under LED, Virginia, USA, "
                   "about 10 days after inoculation; a biocontrol test, not field disease"),
        **_CC_BY,
    ),
    dict(
        title="Pseudomonas rhodesiae and glutamate against Pythium in cucumber (Takeuchi et al., 2024)",
        body=("Cucumber 'Tokiwajibai' was sown in pots of vermiculite infested with Pythium ultimum and "
              "treated with Pseudomonas rhodesiae HAI-0804. After two weeks, the strain raised shoot fresh "
              "weight significantly, but survival (47%, against 22% untreated) was not significantly "
              "different. Adding 1 mM glutamate lifted survival to 75%, a significant gain. The strain "
              "showed no direct antibiotic activity against the pathogen in the test used."),
        source="Takeuchi et al. (2024)",
        reference=("Takeuchi K, Ogiso M, Ota A, Nishimura K, Nishino C, Omori Y, Maeda M, Mizui R, Yamanaka H, "
                   "Ogino T, Seo S (2024). Pseudomonas rhodesiae HAI-0804 suppresses Pythium damping off and "
                   "root rot in cucumber by its efficient root colonization promoted by amendment with "
                   "glutamate. Frontiers in Microbiology 15:1485167. "
                   "doi:10.3389/fmicb.2024.1485167"),
        url="https://doi.org/10.3389/fmicb.2024.1485167",
        condition=("Pots of vermiculite (not soil, not hydroponic), growth chamber 26 C, 14 days, "
                   "12 pots per treatment, Japan"),
        **_CC_BY,
    ),
]

_CUCUMBER_PROPAGATION_DOCS = [
    dict(
        title="Grafting soilless cucumber onto rootstock (Phalakatshela et al., 2025)",
        body=("Cucumber 'Hoplita' was grafted onto the squash-hybrid rootstocks Flexifort or Ferro, "
              "with one or two roots, and compared with non-grafted plants. Early yield was 2.3-2.5 kg per "
              "plant when grafted against 1.5 non-grafted, and total yield 6.72-7.14 against 5.97. Yields of "
              "the grafted combinations were similar, so the authors favour single-root grafting for lower "
              "labour and input costs."),
        source="Phalakatshela et al. (2025)",
        reference=("Phalakatshela TA, Soundy P, Kubheka SF, Maboko MM (2025). Impact of Combined Rootstock "
                   "Cultivar and Grafting Method on Growth, Yield, and Quality of Soilless-Grown Cucumber "
                   "(Cucumis sativus L.) in a Non-Temperature-Controlled High Tunnel. Plants 14(24):3792. doi:10.3390/plants14243792"),
        url="https://doi.org/10.3390/plants14243792",
        condition=("Sawdust in 10 L bags, unheated plastic tunnel near Pretoria, South Africa, "
                   "September 2023 to January 2024, one scion, two rootstocks, four blocks; the bag may have "
                   "limited root growth"),
        **_CC_BY,
    ),
]

_SWEET_PEPPER_TEMPERATURE_DOCS = [
    dict(
        title="FAO ECOCROP on sweet pepper's optimal temperature",
        body=("The FAO ECOCROP data sheet for Capsicum annuum (id 618) states an optimal temperature "
              "range of 17-30 degrees C, bounded absolutely by 8-35 degrees C. No cultivation condition "
              "is stated, and the sheet covers the species as a whole, not sweet pepper alone."),
        source="FAO ECOCROP (id 618)",
        reference="FAO ECOCROP data sheet, id 618",
        url="https://ecocrop.apps.fao.org/ecocrop/srv/en/dataSheet?id=618",
        condition=None,
        licence_note=_FAO_LICENCE_NOTE,
    ),
]

_SWEET_PEPPER_NUTRIENT_SOLUTION_DOCS = [
    dict(
        title="Nutrient-to-water uptake ratios of hydroponic sweet pepper (Ropokis et al., 2018)",
        body=("Four sweet pepper cultivars (bell types 'Orangery' and 'Sondela', elongated 'Bellisa' and "
              "'Sammy') were grown in recirculating nutrient film technique in a heated glasshouse near "
              "Athens, and the amount of each nutrient removed per litre of water taken up was calculated "
              "over the crop cycle. The authors report macronutrient uptake concentrations of 11.7-13.7 "
              "mmol per litre for nitrogen, 6.2-9.0 for potassium, 2.4-3.7 for calcium, 1.0-1.5 for "
              "magnesium and 0.7-1.1 for phosphorus, clearly above the values found earlier in Dutch "
              "rockwool pepper, and for most nutrients the ratio fell as the crop aged. 'Sondela' took up "
              "the most calcium and magnesium per litre and 'Bellisa' the most potassium, while grafting "
              "'Sammy' onto a Capsicum annuum rootstock changed neither uptake ratios nor yield."),
        source="Ropokis et al. (2018)",
        reference=("Ropokis A, Ntatsi G, Kittas C, Katsoulas N, Savvas D (2018). Impact of Cultivar and Grafting "
                   "on Nutrient and Water Uptake by Sweet Pepper (Capsicum annuum L.) Grown Hydroponically "
                   "Under Mediterranean Climatic Conditions. Frontiers in Plant Science 9:1244. "
                   "doi:10.3389/fpls.2018.01244"),
        url="https://doi.org/10.3389/fpls.2018.01244",
        condition=("Closed NFT hydroponics in a heated glasshouse, Athens, Greece; transplanted 16 January "
                   "2014 and followed to about July; nutrient solution pH 5.6; one season, Mediterranean "
                   "climate"),
        **_CC_BY,
    ),
    dict(
        title="Nitrogen form and dissolved oxygen in hydroponic bell pepper (Roosta, 2024)",
        body=("Bell pepper 'California Wonder' plants were grown for ten weeks in aerated floating-culture "
              "buckets, fed 5 mM nitrogen as either nitrate or ammonium, with dissolved oxygen held at "
              "about 1.8, 2.6, 3.8 or 5.3 mg per litre. At the three lower oxygen levels nitrate-fed plants "
              "had more shoot and root mass than ammonium-fed plants, and the two nitrogen forms did not "
              "differ in shoot mass at 5.3 mg per litre. Photosynthetic rate was higher with nitrate at "
              "every oxygen level and lowest at 1.8 and 2.6 mg per litre, and the author concludes the "
              "oxygen level should not fall below 5.3 mg per litre."),
        source="Roosta (2024)",
        reference=("Roosta HR (2024). The responses of pepper plants to nitrogen form and dissolved oxygen "
                   "concentration of nutrient solution in hydroponics. BMC Plant Biology 24:281. "
                   "doi:10.1186/s12870-024-04943-7"),
        url="https://doi.org/10.1186/s12870-024-04943-7",
        condition=("Floating hydroponic culture, cv. California Wonder, glass greenhouse in Rafsanjan, Iran, "
                   "5 mM N as nitrate or ammonium, 10 weeks after transplanting, three replicates"),
        **_CC_BY,
    ),
    dict(
        title="Nutrient-solution EC for hydroponic sweet pepper (Ding et al., 2022)",
        body=("Sweet pepper 'Stayer RZ' plants with the tenth true leaf expanded were grown for 25 days "
              "in containers irrigated with nutrient solution at six EC levels, from 0.72 to 17.4 dS per "
              "metre, in a glasshouse near Shanghai. An EC of 2.9 gave the highest shoot fresh and dry weight; "
              "compared with it, shoot dry weight was 43.0% lower at 17.4, 34.4% lower at 11.6 and 16.5% "
              "lower at 0.72, and root respiration fell by 52%, 79% and 89% at EC 5.8, 11.6 and 17.4. At "
              "17.4 the plants almost stopped growing and gave no fruit, and the authors suggest irrigating "
              "below 5.8 dS per metre."),
        source="Ding et al. (2022)",
        reference=("Ding X, Zhang H, Qian T, He L, Jin H, Zhou Q, Yu J (2022). Nutrient Concentrations Induced "
                   "Abiotic Stresses to Sweet Pepper Seedlings in Hydroponic Culture. Plants 11(8):1098. "
                   "doi:10.3390/plants11081098"),
        url="https://doi.org/10.3390/plants11081098",
        condition=("Container hydroponics, cv. Stayer RZ, Venlo glasshouse in Shanghai, China, 2019-2020, "
                   "25 days of treatment at the vegetative stage, five replicates, one cultivar"),
        **_CC_BY,
    ),
]

_SWEET_PEPPER_PEST_DOCS = [
    dict(
        title="Plant-defence agents against aphids and thrips on greenhouse sweet pepper (Zayed et al., 2026)",
        body=("In a greenhouse near Damietta, Egypt, sweet pepper 'Top Star' was sprayed 27 days after "
              "planting with salicylic acid, potassium phosphite, effective microorganisms or imidacloprid, "
              "against a water-sprayed check, over the 2022 and 2023 seasons. All four reduced cotton aphid "
              "(Aphis gossypii) and onion thrips (Thrips tabaci) numbers relative to the check, with "
              "potassium phosphite most effective against aphids and imidacloprid against thrips. All also "
              "reduced the lacewing Chrysoperla carnea, imidacloprid most, while Orius insidiosus did not "
              "differ significantly among treatments. Dinotefuran was sprayed on every treated plot once "
              "infestation reached 3%, so this is not an insecticide-free test."),
        source="Zayed et al. (2026)",
        reference=("Zayed MS, Hegab MA, SaadAllah MS, Elnabawy EM, El-Beltagi HS, Abo-Ogiala A, El-Harariy A "
                   "(2026). Non-Conventional Agents Enhance Sweet Pepper (Capsicum annuum L. var. annuum) "
                   "Defense against Aphis Gossypii, Thrips Tabaci, and Their Predators Chrysoperla Carnea and "
                   "Orius Insidiosus. Global Challenges 10(2):e00590. doi:10.1002/gch2.202500590"),
        url="https://doi.org/10.1002/gch2.202500590",
        condition=("Commercial-scale greenhouse (about 4200 m2), cv. Top Star, Damietta, Egypt, two seasons "
                   "(2022, 2023), three replicate plots of 150 plants, foliar sprays with a dinotefuran rescue spray"),
        **_CC_BY,
    ),
    dict(
        title="Bacterial leaf spot of greenhouse sweet pepper and foliar silica nanoparticles with yeast (Awad-Allah et al., 2021)",
        body=("Sweet pepper 'Hybrid 702' grown in sterilised clay-and-sand pots in a greenhouse in Alexandria, "
              "Egypt was sprayed with silica nanoparticles (0-150 ppm), a baker's yeast (Saccharomyces "
              "cerevisiae) extract (0-5%), or both, and inoculated with Xanthomonas vesicatoria. Bacterial "
              "spot severity on a 1-7 scale, scored four weeks after inoculation, averaged 5.06 for untreated "
              "infected plants, 3.58 with 5% yeast alone, 2.06 with 150 ppm nanoparticles alone and 1.20 with "
              "both. Treated plants also grew more and set more fruit; this is one pot experiment with "
              "foliar sprays, not a soilless-system trial."),
        source="Awad-Allah et al. (2021)",
        reference=("Awad-Allah EFA, Shams AHM, Helaly AA (2021). Suppression of Bacterial Leaf Spot by Green "
                   "Synthesized Silica Nanoparticles and Antagonistic Yeast Improves Growth, Productivity and "
                   "Quality of Sweet Pepper. Plants 10(8):1689. doi:10.3390/plants10081689"),
        url="https://doi.org/10.3390/plants10081689",
        condition=("Greenhouse pot trial in sterilised clay-sand, NOT a soilless or hydroponic system; cv. Hybrid 702, "
                   "Alexandria, Egypt, artificial spray inoculation with X. vesicatoria at 1e8 CFU/mL, "
                   "five replicates, one season"),
        **_CC_BY,
    ),
    dict(
        title="Beauveria bassiana and a predatory mite against spider mites on greenhouse sweet pepper (Michereff-Filho et al., 2026)",
        body=("On potted greenhouse sweet pepper ('Dahra RX') infested with two-spotted spider mite "
              "(Tetranychus urticae), spraying Beauveria bassiana at 2x10^8 conidia per millilitre gave "
              "about 81% control of adult mites a week later, against about 53% for releasing the predatory "
              "mite Neoseiulus californicus alone; the high fungal dose also left predator numbers more than "
              "90% below those on unsprayed plants after 14 days. Releasing the predator 1, 3 or 6 days "
              "after a 10^8 spray cut its estimated population by 75%, 55% and 30%. At the 6-day interval "
              "the combination left fewer mite immatures and eggs than either agent alone, though adult "
              "mite numbers were no lower than with the fungus alone."),
        source="Michereff-Filho et al. (2026)",
        reference=("Michereff-Filho M, Lopes RB, da Silva PA, Fidelis EG, do Vale AM (2026). Combined use of "
                   "Beauveria bassiana and the predatory mite Neoseiulus californicus to suppress Tetranychus "
                   "urticae on greenhouse-grown sweet pepper. Experimental & Applied Acarology 97(3):41. "
                   "doi:10.1007/s10493-026-01190-3"),
        url="https://doi.org/10.1007/s10493-026-01190-3",
        condition=("Potted plants (soil-substrate mix) in a glass greenhouse, cv. Dahra RX, Embrapa, Brasilia, "
                   "Brazil, artificially infested, counts 7 and 14 days after treatment, one pest species"),
        **_CC_BY,
    ),
]

_SWEET_PEPPER_PROPAGATION_DOCS = [
    dict(
        title="Millicompost-based substrates for bell pepper seedlings (Antunes et al., 2025)",
        body=("Bell pepper ('ISLA Cascadura Ikeda') seedlings were raised for 35 days in 128-cell trays in a "
              "greenhouse in Seropedica, Brazil, on nine substrates built on millicompost (compost made "
              "from pruning waste with millipedes), alone or mixed with gliricidia shavings, elephant grass "
              "and coconut fibre, against a commercial substrate. Millicompost alone gave the largest "
              "seedlings (shoot dry mass 0.15 g) and the most stable root clods; mixes of one-third each "
              "millicompost, gliricidia and coconut fibre, or half millicompost and half gliricidia, gave "
              "seedlings 14-15 cm tall of excellent vigour with shoot dry mass 0.10-0.11 g, and the "
              "millicompost-elephant grass-coconut mix was the weakest."),
        source="Antunes et al. (2025)",
        reference=("de Sousa Antunes LF, Vaz AFS, dos Santos GCR, Ferreira TS, dos Santos RR, Alves RS, de "
                   "Almeida JC, de Almeida Leal MA, Correia MEF (2025). Replacing Commercial Substrate with "
                   "Millicompost: A Sustainable Approach Using Different Green Wastes Combined with "
                   "Millicompost for Bell Pepper Seedling Production in Urban Agriculture. ACS Omega "
                   "10(37):43129. doi:10.1021/acsomega.5c06388"),
        url="https://doi.org/10.1021/acsomega.5c06388",
        condition=("Seedling trays in a greenhouse, Embrapa Agrobiologia, Seropedica, Brazil, 27 June to 1 "
                   "August 2019, one cultivar, one trial; the authors suggest testing the mixes on other vegetables"),
        **_CC_BY,
    ),
]

_KALE_TEMPERATURE_DOCS = [
    dict(
        title="FAO ECOCROP on kale's optimal temperature",
        body=("The FAO ECOCROP data sheet for Brassica oleracea var. acephala (id 3867) states an "
              "optimal temperature range of 15-22 degrees C, bounded absolutely by "
              "7-30 degrees C. No cultivation condition is stated."),
        source="FAO ECOCROP (id 3867)",
        reference="FAO ECOCROP data sheet, id 3867",
        url="https://ecocrop.apps.fao.org/ecocrop/srv/en/dataSheet?id=3867",
        condition=None,
        licence_note=_FAO_LICENCE_NOTE,
    ),
]

_KALE_WATERING_DOCS = [
    dict(
        title="Nutrient spraying interval for aeroponic Tuscan kale (He et al., 2023)",
        body=("Tuscan kale was grown aeroponically in a Singapore greenhouse with a one-minute nutrient spray "
              "every 5, 30 or 60 minutes. After four weeks, the 60-minute plants had 61% less shoot fresh "
              "weight and 52% less shoot dry weight than the 5-minute plants, with a 53% smaller leaf area. "
              "Moving 4-week-old plants from 5 to 60 minutes for one final week kept their shoot growth in "
              "line with plants left on 5 minutes, whereas a move to 90 minutes stopped shoot growth. Water use "
              "itself was not measured, so any saving is inferred from the longer interval."),
        source="He et al. (2023)",
        reference=("He J, Chang C, Qin L, Lai CH (2023). Impacts of Deficit Irrigation on Photosynthetic "
                   "Performance, Productivity and Nutritional Quality of Aeroponically Grown Tuscan Kale "
                   "(Brassica oleracea L.) in a Tropical Greenhouse. International Journal of Molecular "
                   "Sciences 24(3):2014. doi:10.3390/ijms24032014"),
        url="https://doi.org/10.3390/ijms24032014",
        condition=("Aeroponic, tropical greenhouse in Singapore (23-38 C air, 28 C root zone, EC 2.0-2.4 mS/cm, "
                   "pH 5.0-5.5), about 5 weeks from transplanting. Label used: 'Tuscan kale (Brassica "
                   "oleracea L.)', cultivar not stated"),
        **_CC_BY,
    ),
    dict(
        title="Short drought before harvest in plant-factory kale (Yoon et al., 2020)",
        body=("Kale grown in a Korean plant-factory deep-flow system had all nutrient solution removed from the "
              "roots for 1 to 7 days before harvest at 42 days after transplanting. Leaf water potential fell "
              "from -3.86 MPa after 2 days to -6.27 MPa after 3 days and -8.19 MPa after 7 days, when plants "
              "wilted; photosystem II efficiency after 1 or 3 days (0.72-0.77) did not differ from the control "
              "(0.82). Three to four days raised phenolic and flavonoid concentrations per dry weight by about "
              "35% and 48%, but four days cut shoot dry weight by 8.0%, so the authors recommend three days."),
        source="Yoon et al. (2020)",
        reference=("Yoon HI, Zhang W, Son JE (2020). Optimal Duration of Drought Stress Near Harvest for Promoting "
                   "Bioactive Compounds and Antioxidant Capacity in Kale with or without UV-B Radiation in Plant "
                   "Factories. Plants 9(3):295. doi:10.3390/plants9030295"),
        url="https://doi.org/10.3390/plants9030295",
        condition=("Plant factory, deep flow technique, 20 C, 16 h light, Seoul National University, Korea; "
                   "three replicate plants per treatment. Label used: 'Brassica oleracea L. var. acephala, "
                   "cv. Manchoo collard'"),
        **_CC_BY,
    ),
]

_KALE_PEST_DOCS = [
    dict(
        title="Rice-straw mulch and green peach aphid on kale (Silva-Filho et al., 2014)",
        body=("In Vicosa, Brazil, field plots of kale mulched with rice straw received 83 winged green peach "
              "aphids over 15 days against 327 on bare soil, and winged and wingless aphids on caged leaves "
              "reached 31 per colony against 318. Canopy maximum temperature was higher under mulch (21-36 C "
              "against 18-32 C), and the authors link the lower aphid numbers partly to heat above 30 C; "
              "light reflection was not measured, and leaf macronutrient content did not differ."),
        source="Silva-Filho et al. (2014)",
        reference=("Silva-Filho R, Santos RHS, Tavares WS, Leite GLD, Wilcken CF, Serrao JE, Zanuncio JC (2014). "
                   "Rice-straw mulch reduces the green peach aphid, Myzus persicae (Hemiptera: Aphididae) "
                   "populations on kale, Brassica oleracea var. acephala (Brassicaceae) plants. PLoS ONE "
                   "9(4):e94174. doi:10.1371/journal.pone.0094174"),
        url="https://doi.org/10.1371/journal.pone.0094174",
        condition=("Soil-grown, not hydroponic: field plots (experiment 1) and 14 L pots placed in the field "
                   "(experiments 2 and 3), Vicosa, Brazil, 2012-2013 (dates in the paper are inconsistent). "
                   "Label used: 'Brassica oleracea var. acephala', a germplasm-bank clone"),
        **_CC_BY,
    ),
    dict(
        title="Bio-based sprays against black rot of kale (Nunez et al., 2018)",
        body=("Kale sprayed with a manure biofertilizer (20% v/v) cut black rot (Xanthomonas campestris pv. "
              "campestris) severity, measured as area under the disease progress curve, by 85% and 68% at five "
              "days after inoculation in two field runs, and still by 56% and 44% at 15 days. Raw milk and whey "
              "(10% v/v) and Bordeaux mixture also lowered severity, while lime sulphur lowered it least and "
              "raised it in the second run. Without the pathogen, plants given biofertilizer, Bordeaux mixture "
              "or raw milk yielded less than the water control."),
        source="Nunez et al. (2018)",
        reference=("Nunez AMP, Rodriguez GAA, Monteiro FP, Faria AF, Silva JCP, Monteiro ACA, Carvalho CV, Gomes "
                   "LAA, Souza RM, de Souza JT, Medeiros FHV (2018). Bio-based products control black rot "
                   "(Xanthomonas campestris pv. campestris) and increase the nutraceutical and antioxidant "
                   "components in kale. Scientific Reports 8:10199. doi:10.1038/s41598-018-28086-6"),
        url="https://doi.org/10.1038/s41598-018-28086-6",
        condition=("Drip-irrigated field with plastic mulch (two runs, October-December, rainy season) plus "
                   "greenhouse enzyme tests, Lavras, Brazil; plants inoculated 8 days after the first spray. "
                   "Label used: 'Brassica oleraceae var. acephala cv. Manteiga'"),
        **_CC_BY,
    ),
    dict(
        title="Root endophytic fungi and kale resistance to black rot and cabbage moth (Poveda et al., 2020)",
        body=("Roots of five Galician kale accessions grown in a field at Pontevedra yielded 33 fungal taxa, "
              "with a Fusarium and a Setophoma/Edenia type common to all accessions. In greenhouse tests, kale "
              "grown with nine of these fungi showed fewer black rot lesions from Xanthomonas campestris pv. "
              "campestris at 8 days after leaf inoculation with six of them, but at 15 days only Setophoma/"
              "Edenia and Acrocalymma still did. Against the cabbage moth Mamestra brassicae, only Setophoma/"
              "Edenia and Fusarium lowered the damage score (2.3 and 2.2 against 3.1), and leaf area eaten "
              "did not differ significantly."),
        source="Poveda et al. (2020)",
        reference=("Poveda J, Zabalgogeazcoa I, Soengas P, Rodriguez VM, Cartea ME, Abilleira R, Velasco P (2020). "
                   "Brassica oleracea var. acephala (kale) improvement by biological activity of root "
                   "endophytic fungi. Scientific Reports 10:20224. doi:10.1038/s41598-020-77215-7"),
        url="https://doi.org/10.1038/s41598-020-77215-7",
        condition=("Greenhouse, kale inoculated with one endophyte at a time in peat substrate, 10 plants per "
                   "isolate for the pest and pathogen tests, each isolate compared with the control by its own "
                   "t-test; Galicia, Spain. Label used: 'Brassica oleracea var. acephala'"),
        **_CC_BY,
    ),
]

_KALE_PROPAGATION_DOCS = [
    dict(
        title="Molybdenum nanoparticle seed priming of kale under salt (Zhao et al., 2026)",
        body=("Kale seeds were soaked for 12 h in water, 100 mg per litre molybdenum nanoparticles or sodium "
              "molybdate, then germinated for three days at 25 C with 150 mM NaCl. Salt cut germination by "
              "45% relative to unsalted seeds; nanoparticle priming raised germination by 18.75% and radicle "
              "fresh weight by 74.77% over molybdate priming. In a 10-day hydroponic stage at 150 mM NaCl, "
              "salt cut shoot fresh weight by 42.51%, and nanoparticle priming raised it by 36.45% over salt "
              "alone, against 6.25% for molybdate."),
        source="Zhao et al. (2026)",
        reference=("Zhao R, Wang L, Lu J, Tanveer M (2026). Molybdenum nanoparticle improved seed germination in "
                   "Kale via regulating ROS homeostasis and metabolites accumulation under salinity stress. "
                   "Frontiers in Plant Science 17:1823191. doi:10.3389/fpls.2026.1823191"),
        url="https://doi.org/10.3389/fpls.2026.1823191",
        condition=("Laboratory: Petri dishes on filter paper (germination) then half-strength Hoagland "
                   "hydroponics (seedlings, 2 days germinated plus 5 days acclimated plus 10 days at 150 mM NaCl). "
                   "Seeds of 'Brassica oleracea var. acephala' from a Beijing seed company, cultivar not named; "
                   "percentages are relative changes, absolute germination rates are shown only in a figure"),
        **_CC_BY,
    ),
    dict(
        title="Spermidine priming and kale seed germination in the cold (Cao et al., 2023)",
        body=("Seeds of two kale cultivars were soaked in 0.5 mM spermidine or water and germinated at 25 C or "
              "13 C. At 13 C untreated 'Nagoya' seeds still reached 90.67% germination (96.00% at 25 C), but "
              "untreated 'Pigeon' only 70.67% (97.33% at 25 C). Spermidine raised 'Pigeon' to 82.67% and its "
              "germination energy from 53.33% to 77.33%, and made no significant difference to 'Nagoya'."),
        source="Cao et al. (2023)",
        reference=("Cao D, Huang Y, Mei G, Zhang S, Wu H, Zhao T (2023). Spermidine enhances chilling tolerance of "
                   "kale seeds by modulating ROS and phytohormone metabolism. PLOS ONE 18(8):e0289563. "
                   "doi:10.1371/journal.pone.0289563"),
        url="https://doi.org/10.1371/journal.pone.0289563",
        condition=("Laboratory germination boxes, 50 seeds per box with four replicates, 13 C chilling versus "
                   "25 C, germination percentage at day 7, energy at day 3. Label used: 'Brassica oleracea var. "
                   "acephala L.', cultivars 'Nagoya' (tolerant) and 'Pigeon' (sensitive); seed from Zhejiang "
                   "Academy of Agricultural Sciences"),
        **_CC_BY,
    ),
]

_TOMATO_TOPIC_GROUPS = [
    (TOPIC_OPTIMAL_TEMPERATURE, _TOMATO_TEMPERATURE_DOCS),
    ("nutrient-solution", _TOMATO_NUTRIENT_SOLUTION_DOCS),
    ("pest-and-disease", _TOMATO_PEST_DOCS),
    ("propagation", _TOMATO_PROPAGATION_DOCS),
]

_CUCUMBER_TOPIC_GROUPS = [
    (TOPIC_OPTIMAL_TEMPERATURE, _CUCUMBER_TEMPERATURE_DOCS),
    ("watering-needs", _CUCUMBER_WATERING_DOCS),
    ("nutrient-solution", _CUCUMBER_NUTRIENT_SOLUTION_DOCS),
    ("pest-and-disease", _CUCUMBER_PEST_DOCS),
    ("propagation", _CUCUMBER_PROPAGATION_DOCS),
]

_SWEET_PEPPER_TOPIC_GROUPS = [
    (TOPIC_OPTIMAL_TEMPERATURE, _SWEET_PEPPER_TEMPERATURE_DOCS),
    ("nutrient-solution", _SWEET_PEPPER_NUTRIENT_SOLUTION_DOCS),
    ("pest-and-disease", _SWEET_PEPPER_PEST_DOCS),
    ("propagation", _SWEET_PEPPER_PROPAGATION_DOCS),
]

_KALE_TOPIC_GROUPS = [
    (TOPIC_OPTIMAL_TEMPERATURE, _KALE_TEMPERATURE_DOCS),
    ("watering-needs", _KALE_WATERING_DOCS),
    ("pest-and-disease", _KALE_PEST_DOCS),
    ("propagation", _KALE_PROPAGATION_DOCS),
]

# (crop, category, topic groups) per crop, in seeding order.
_CROP_SPECS = [
    (CROP, MAIN_CATEGORY, _TOPIC_GROUPS),
    (LETTUCE, LETTUCE_CATEGORY, _LETTUCE_TOPIC_GROUPS),
    (STRAWBERRY, STRAWBERRY_CATEGORY, _STRAWBERRY_TOPIC_GROUPS),
    (TOMATO, TOMATO_CATEGORY, _TOMATO_TOPIC_GROUPS),
    (CUCUMBER, CUCUMBER_CATEGORY, _CUCUMBER_TOPIC_GROUPS),
    (SWEET_PEPPER, SWEET_PEPPER_CATEGORY, _SWEET_PEPPER_TOPIC_GROUPS),
    (KALE, KALE_CATEGORY, _KALE_TOPIC_GROUPS),
]


_DOC_DEFAULTS = dict(
    published=True,
    read_directly=True,
    via=None,
    condition=None,
    licence_note=None,
)


def _get_or_create(session: Session, model, defaults: dict, **lookup):
    instance = session.query(model).filter_by(**lookup).one_or_none()
    if instance is not None:
        for key, value in defaults.items():
            setattr(instance, key, value)
        return instance
    instance = model(**lookup, **defaults)
    session.add(instance)
    session.flush()  # populate instance.id
    return instance


def _seed_crop(session: Session, crop_spec: dict, category_spec: dict, topic_groups: list) -> None:
    crop = _get_or_create(
        session,
        Crop,
        {"common_name": crop_spec["common_name"], "scientific_name": crop_spec["scientific_name"],
         "ecocrop_id": crop_spec["ecocrop_id"]},
        slug=crop_spec["slug"],
    )
    main_category = _get_or_create(
        session, MainCategory, {"name": category_spec["name"]}, slug=category_spec["slug"],
    )
    sub_category = _get_or_create(
        session,
        SubCategory,
        {"name": SUB_CATEGORY["name"]},
        main_category_id=main_category.id,
        slug=SUB_CATEGORY["slug"],
    )

    for topic, docs in topic_groups:
        for doc in docs:
            fields = {**_DOC_DEFAULTS, "topic": topic, **doc}
            title = fields.pop("title")
            source = fields.pop("source")
            fields["sub_category_id"] = sub_category.id
            # `source` is part of the key: nothing makes (crop, title) unique, so without it
            # we would overwrite somebody else's document with the same title.
            _get_or_create(
                session,
                Item,
                fields,
                crop_id=crop.id,
                title=title,
                source=source,
            )


def main() -> None:
    with Session(sync_engine) as session:
        for crop_spec, category_spec, topic_groups in _CROP_SPECS:
            _seed_crop(session, crop_spec, category_spec, topic_groups)
        session.commit()


if __name__ == "__main__":
    main()
