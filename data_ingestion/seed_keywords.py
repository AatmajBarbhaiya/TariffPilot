"""
Populate hs_taxonomy.keywords — the synonym layer for Signal 1 (SQL keyword match).

WHY
---
The official HS descriptions are terse and jargon-y ("Ammunition; shotgun
cartridges"), so a user typing "shotgun shells" or "MRI machine" matches nothing.
Signal 1 scores a query by how many of its tokens hit `description` OR `keywords`,
so this column carries the *lay synonyms, common product names, brand-ish terms
and misspellings* that the description omits — NOT a restatement of the description.

These were hand-drafted against each code's description (medical domain +
plain-English synonyms) and are meant to be human-skimmed. Idempotent: re-running
overwrites keywords for the listed codes only. See README §4 (Signal 1) / TODO #1.
"""
import sqlite3
from pathlib import Path

# Anchor the DB to <project>/Database/ regardless of the caller's CWD.
_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = str(_ROOT / "Database" / "tariff_pilot.db")

# hs6 -> comma-separated synonyms/lay terms (lowercase). Additive to `description`.
KEYWORDS = {
    # ------------------------------------------------------------------ ammunition
    "930510": "pistol parts, revolver parts, handgun parts, gun parts, pistol accessories, magazines, grips, slides, barrels, handgun components",
    "930520": "shotgun parts, rifle parts, long gun parts, gun parts, stocks, barrels, chokes, rifle accessories, shotgun accessories, sporting gun components",
    "930591": "military weapon parts, military firearm parts, machine gun parts, army weapon parts, military arms components, service rifle parts",
    "930599": "firearm parts, gun parts, weapon accessories, firearm accessories, non-military gun parts, gun components",
    "930621": "shotgun shells, shells, shotshells, shotgun ammo, buckshot, birdshot, 12 gauge shells, 20 gauge, cartridges, ammunition",
    "930629": "shotgun cartridge parts, wads, cartridge wads, primers, empty shells, shotshell components, cartridge cases",
    "930630": "cartridges, rifle cartridges, pistol cartridges, bullets, rounds, ammo, ammunition, centerfire, rimfire, brass, cartridge cases, projectiles",
    "930690": "ammunition, ammo, munitions, rounds, projectiles, bombs, grenades, missiles, warheads, ordnance, shot, pellets",
    # -------------------------------------------------------------------- medical
    "300120": "gland extract, organ extract, glandular extract, organotherapy, thyroid extract, adrenal extract, endocrine extract",
    "300190": "heparin, anticoagulant, blood thinner, animal substances, human substances, therapeutic substances, organ preparations",
    "300212": "antisera, antiserum, blood serum, blood fractions, plasma fractions, immune serum, antivenom, antitoxin",
    "300213": "immunological products, monoclonal antibodies, antibodies, immunoglobulins, bulk biologics, unmixed immunologicals",
    "300214": "immunological products, monoclonal antibodies, antibodies, immunoglobulins, biologics, retail immunologicals",
    "300215": "immunological products, antibodies, immunoglobulins, biologics, retail biologics, immune products",
    "300241": "vaccine, vaccines, immunization, immunisation, jab, shot, inoculation, human vaccine, flu shot, mrna vaccine, toxoid, covid vaccine",
    "300242": "veterinary vaccine, animal vaccine, pet vaccine, livestock vaccine, rabies vaccine, vet vaccine, animal immunization",
    "300249": "toxins, microbial cultures, bacterial cultures, ferments, cultures, biological toxins",
    "300251": "cell therapy, car-t, cell cultures, stem cell therapy, cellular therapy, regenerative medicine, cell-based therapy",
    "300259": "cell cultures, cultured cells, research cell lines, cell lines",
    "300290": "toxins, microbial cultures, cultures of micro-organisms, ferments, biologicals",
    "300310": "penicillin, streptomycin, antibiotics, bulk antibiotics, antibiotic medicament, bulk medicine, not retail",
    "300320": "antibiotics, bulk antibiotics, antibiotic medicament, tetracycline, cephalosporin, not retail",
    "300331": "insulin, bulk insulin, diabetes medicine, antidiabetic, insulin medicament, not retail",
    "300339": "hormones, hormone medicament, steroid, endocrine medicine, bulk hormones, not retail",
    "300341": "ephedrine, alkaloids, decongestant, bulk ephedrine, controlled substance, ephedrine medicament",
    "300342": "pseudoephedrine, sudafed, alkaloids, decongestant, bulk pseudoephedrine, controlled substance",
    "300343": "norephedrine, phenylpropanolamine, alkaloids, controlled substance, bulk",
    "300349": "alkaloids, morphine, codeine, opioid, narcotic, controlled substance, alkaloid medicament, bulk, not retail",
    "300360": "antimalarial, malaria medicine, chloroquine, artemisinin, quinine, bulk antimalarial",
    "300390": "medicaments, medicine, drugs, bulk medicine, generic medicament, pharmaceutical, not retail",
    "300410": "penicillin, streptomycin, antibiotics, antibiotic tablets, amoxicillin, retail antibiotics, packaged medicine",
    "300420": "antibiotics, antibiotic tablets, retail antibiotics, cephalosporin, tetracycline, azithromycin, packaged medicine",
    "300431": "insulin, insulin pen, insulin vial, diabetes medicine, retail insulin, packaged insulin",
    "300432": "corticosteroids, steroid, prednisone, cortisone, hydrocortisone, steroid medicine, hormone medicine",
    "300439": "hormones, hormone tablets, thyroid medicine, estrogen, testosterone, hormone therapy, packaged hormones",
    "300441": "ephedrine, decongestant, ephedrine tablets, controlled substance, retail, packaged",
    "300442": "pseudoephedrine, sudafed, decongestant, cold medicine, retail pseudoephedrine, packaged",
    "300443": "norephedrine, phenylpropanolamine, decongestant, retail, packaged",
    "300449": "alkaloids, morphine, codeine, opioid, narcotic painkiller, controlled substance, retail, packaged",
    "300450": "vitamins, vitamin supplements, multivitamin, vitamin tablets, vitamin c, vitamin d, supplements, packaged vitamins",
    "300460": "antimalarial, malaria tablets, chloroquine, artemisinin, quinine, retail antimalarial, packaged",
    "300490": "medicaments, medicine, drugs, tablets, capsules, pills, generic medicine, retail medicine, pharmaceutical, packaged medication",
    "300510": "adhesive dressing, bandage, band-aid, plaster, adhesive bandage, wound dressing, sticking plaster, medical tape",
    "300590": "gauze, bandage, wadding, cotton wool, dressing, medicated gauze, wound care, surgical dressing",
    "300610": "catgut, sutures, surgical suture, stitches, tissue adhesive, surgical glue, absorbable suture, laminaria, haemostatics",
    "300630": "contrast agent, contrast medium, x-ray contrast, barium, diagnostic reagent, imaging contrast, radiocontrast",
    "300640": "dental cement, dental filling, bone cement, bone reconstruction cement, tooth filling, dental restorative",
    "300650": "first aid kit, first aid box, medical kit, emergency kit, first aid supplies",
    "300660": "contraceptive, birth control, spermicide, hormonal contraceptive, contraceptive pill, family planning",
    "300670": "surgical gel, lubricant gel, ultrasound gel, medical lubricant, coupling gel, examination gel",
    "300691": "ostomy, colostomy, ostomy bag, colostomy bag, stoma, ostomy appliance, ileostomy",
    "300692": "waste pharmaceuticals, expired medicine, pharmaceutical waste, medical waste, expired drugs",
    "300693": "placebo, clinical trial kit, blinded trial, clinical trial supplies, trial medication",
    "901811": "ecg, ekg, electrocardiograph, heart monitor, cardiac monitor, ecg machine, electrocardiogram",
    "901812": "ultrasound, ultrasound machine, ultrasonic scanner, sonography, sonogram, echo, diagnostic ultrasound",
    "901813": "mri, mri machine, mri scanner, magnetic resonance imaging, mri apparatus",
    "901814": "scintigraphy, gamma camera, nuclear medicine scanner, scintigraphic, spect",
    "901819": "electro-diagnostic, eeg, emg, patient monitor, diagnostic apparatus, physiological monitor, electrodiagnostic",
    "901820": "uv apparatus, infrared apparatus, uv lamp, infrared lamp, phototherapy, uv therapy, infrared therapy",
    "901831": "syringe, syringes, hypodermic syringe, disposable syringe, injection syringe, insulin syringe, needle",
    "901832": "needle, needles, hypodermic needle, suture needle, injection needle, medical needle, tubular needle",
    "901839": "catheter, cannula, cannulae, iv catheter, urinary catheter, intravenous catheter, drainage catheter, tube",
    "901841": "dental drill, dental engine, dental handpiece, dental drill engine, dentist drill",
    "901849": "dental instruments, dental tools, dental equipment, dentist instruments, dental appliances",
    "901850": "ophthalmic instruments, eye instruments, ophthalmoscope, slit lamp, optometry equipment, eye examination equipment",
    "901890": "medical instruments, surgical instruments, medical devices, surgical tools, medical appliances, surgical equipment",
    "901910": "massage apparatus, mechano-therapy, massage machine, physiotherapy equipment, aptitude testing apparatus",
    "901920": "oxygen therapy, aerosol therapy, ventilator, respirator, oxygen concentrator, nebulizer, artificial respiration, ozone therapy",
    "902000": "gas mask, respirator, breathing apparatus, breathing appliance, oxygen mask, protective breathing",
    "902110": "orthopaedic appliance, fracture appliance, orthosis, brace, splint, orthopedic brace, cast, support brace",
    "902121": "artificial teeth, false teeth, dentures, dental prosthesis, prosthetic teeth",
    "902129": "dental fittings, crowns, bridges, dental prosthetics, dental appliances",
    "902131": "artificial joint, prosthesis, prosthetic, hip replacement, knee replacement, joint implant, artificial limb",
    "902139": "prosthesis, prosthetic, implant, artificial body part, artificial organ, prosthetic limb",
    "902140": "hearing aid, hearing aids, hearing device, deaf aid, hearing amplifier",
    "902150": "pacemaker, cardiac pacemaker, heart pacemaker, pacing device",
    "902190": "implant, prosthetic appliance, worn appliance, implanted device, assistive device, disability appliance",
    "902212": "x-ray machine, ct scanner, computed tomography, ct scan, cat scan, radiography, x-ray apparatus",
    "902213": "dental x-ray, dental radiography, dental x-ray machine, dental imaging",
    "902214": "x-ray machine, medical x-ray, radiography apparatus, radiotherapy, x-ray imaging",
    "902219": "x-ray apparatus, industrial x-ray, non-medical x-ray, radiography",
    "902221": "radiotherapy, gamma radiation apparatus, radiation therapy, nuclear medicine, ionising radiation apparatus",
    "902229": "radiation apparatus, industrial radiation, ionising radiation, non-medical radiotherapy",
    "902230": "x-ray tube, x-ray tubes, radiography tube",
    "902290": "x-ray parts, x-ray accessories, x-ray generator, high tension generator, x-ray components, control panel",
    # ---------------------------------------------------------------------- watches
    "910111": "luxury watch, gold watch, precious metal watch, analog watch, dress watch, electric watch with precious case",
    "910119": "luxury watch, gold watch, precious metal watch, digital watch, quartz watch, opto-electronic display watch",
    "910121": "automatic watch, self-winding watch, luxury automatic watch, gold automatic watch, mechanical automatic watch",
    "910129": "manual wind watch, hand-wound watch, mechanical watch, luxury mechanical watch, gold mechanical watch",
    "910191": "pocket watch, gold pocket watch, luxury pocket watch, electric pocket watch, precious metal stopwatch",
    "910199": "pocket watch, gold pocket watch, mechanical pocket watch, antique pocket watch, non-electric pocket watch",
    "910211": "analog watch, wristwatch, quartz analog watch, mechanical display watch, steel watch, everyday watch",
    "910212": "digital watch, quartz watch, electronic watch, led watch, lcd watch, sports watch",
    "910219": "smartwatch, combination display watch, hybrid watch, electronic wristwatch, digital analog watch",
    "910221": "automatic watch, self-winding watch, mechanical watch, steel automatic watch, sports automatic watch",
    "910229": "manual wind watch, hand-wound watch, mechanical wristwatch, classic watch, steel mechanical watch",
    "910291": "pocket watch, electric pocket watch, steel pocket watch, non-precious pocket watch, stopwatch",
    "910299": "pocket watch, mechanical pocket watch, steel pocket watch, antique pocket watch, non-electric pocket watch",
    # ----------------------------------------------------------------------- liquor
    "220820": "brandy, cognac, armagnac, grappa, grape spirit, pisco, grape brandy, distilled wine spirit",
    "220830": "whisky, whiskey, scotch, bourbon, single malt, blended whisky, rye whiskey, irish whiskey",
    "220840": "rum, dark rum, white rum, spiced rum, cachaca, sugar cane spirit, rhum",
    "220850": "gin, geneva, dry gin, london dry gin, sloe gin, jenever, genever",
    "220860": "vodka, potato vodka, grain vodka, flavored vodka, plain vodka",
    "220870": "liqueur, cordial, flavored spirit, cream liqueur, herbal liqueur, fruit liqueur, schnapps",
    "220890": "tequila, mezcal, soju, baijiu, aquavit, other spirits, spirituous beverage, hard liquor, distilled spirit",
}


def seed_keywords():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    db_codes = {r[0] for r in cur.execute("SELECT hs6 FROM hs_taxonomy")}

    # Guard: surface any drift between this map and the actual taxonomy.
    missing_in_map = sorted(db_codes - KEYWORDS.keys())
    unknown_in_map = sorted(KEYWORDS.keys() - db_codes)
    if missing_in_map:
        print(f"⚠ {len(missing_in_map)} taxonomy code(s) have no keywords here: "
              f"{missing_in_map}")
    if unknown_in_map:
        print(f"⚠ {len(unknown_in_map)} keyword code(s) not in taxonomy (skipped): "
              f"{unknown_in_map}")

    updated = 0
    for hs6, kw in KEYWORDS.items():
        if hs6 not in db_codes:
            continue
        cur.execute("UPDATE hs_taxonomy SET keywords = ? WHERE hs6 = ?", (kw, hs6))
        updated += cur.rowcount

    conn.commit()
    total, with_kw = cur.execute(
        "SELECT COUNT(*), COUNT(keywords) FROM hs_taxonomy").fetchone()
    conn.close()
    print(f"✓ keywords set on {updated} rows. Coverage: {with_kw}/{total} codes.")


if __name__ == "__main__":
    seed_keywords()
