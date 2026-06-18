import os

# ── Root path to your GCSE question papers ────────────────────────────────────
GCSE_PATH = r"C:\Users\Thanuja\OneDrive\Desktop\GCSE"

# ── Supabase storage bucket name ──────────────────────────────────────────────
STORAGE_BUCKET = "question-images"

# ── Students ──────────────────────────────────────────────────────────────────
STUDENTS = ["M1", "M2"]

# ── Difficulty colours ────────────────────────────────────────────────────────
DIFFICULTY_COLOURS = {
    "Bronze": "#CD7F32",
    "Silver": "#A0A0A0",
    "Gold":   "#DAA520",
}

SCORE_COLOURS = {
    "strong":  "#2e7d32",   # ≥ 75%
    "ok":      "#f57c00",   # 50–74%
    "weak":    "#c62828",   # < 50%
    "none":    "#e0e0e0",   # not attempted
}

# ── Full GCSE topic taxonomy ──────────────────────────────────────────────────
TOPICS = {

    "Maths": {
        "Number": [
            "Calculations & place value",
            "Fractions, decimals & percentages",
            "Indices & surds",
            "Standard form",
            "Bounds & accuracy",
        ],
        "Algebra": [
            "Expressions & simplifying",
            "Expanding & factorising",
            "Linear equations",
            "Quadratic equations",
            "Simultaneous equations",
            "Formulae & rearranging",
            "Functions & inverse functions",
            "Inequalities",
            "Algebraic proof",
            "Sequences",
        ],
        "Graphs": [
            "Straight line graphs",
            "Quadratic graphs",
            "Other functions (cubic, reciprocal, exponential)",
            "Real-life graphs",
            "Kinematic graphs",
            "Gradients & areas under graphs",
        ],
        "Ratio & Proportion": [
            "Ratio",
            "Direct & inverse proportion",
            "Percentage change",
            "Growth & decay",
            "Units & compound measures",
        ],
        "Geometry": [
            "Angles & polygons",
            "2D area & perimeter",
            "Circles & circle theorems",
            "3D volume & surface area",
            "Transformations",
            "Constructions & loci",
            "Similarity & congruence",
        ],
        "Trigonometry": [
            "Pythagoras theorem",
            "SOH CAH TOA",
            "Sine & cosine rules",
            "3D trigonometry",
            "Vectors",
        ],
        "Statistics": [
            "Data collection & sampling",
            "Charts & diagrams",
            "Averages & spread",
            "Cumulative frequency & box plots",
            "Histograms",
            "Scatter graphs & correlation",
        ],
        "Probability": [
            "Basic probability",
            "Combined events & tree diagrams",
            "Conditional probability",
        ],
    },

    "Chemistry": {
        "Atomic Structure": [
            "Atoms, elements & compounds",
            "Mixtures & separation techniques",
            "Atomic structure & the periodic table",
            "Electronic structure",
            "Isotopes & relative atomic mass",
        ],
        "Bonding": [
            "Ionic bonding",
            "Covalent bonding",
            "Metallic bonding",
            "Structure & properties of materials",
        ],
        "Quantitative Chemistry": [
            "Moles & mass calculations",
            "Concentration & volumes",
            "Yield & atom economy",
            "Titrations",
        ],
        "Chemical Changes": [
            "Reactivity series & metal extraction",
            "Electrolysis",
            "Acids, bases & pH",
            "Neutralisation & salt preparation",
        ],
        "Energy Changes": [
            "Exothermic & endothermic reactions",
            "Bond energies & calculations",
            "Electrochemical cells",
        ],
        "Rate & Equilibrium": [
            "Factors affecting rate of reaction",
            "Reversible reactions & equilibrium",
            "Le Chatelier's principle",
        ],
        "Organic Chemistry": [
            "Crude oil, hydrocarbons & fractional distillation",
            "Alkanes & combustion",
            "Alkenes & addition reactions",
            "Alcohols & carboxylic acids",
            "Polymers",
        ],
        "Analysis": [
            "Purity & formulations",
            "Chromatography",
            "Identification of gases & ions",
            "Flame tests & spectroscopy",
        ],
        "Earth & Atmosphere": [
            "Earth's structure & plate tectonics",
            "Carbon cycle & Earth's atmosphere",
            "Climate change & carbon footprint",
        ],
        "Using Resources": [
            "Finite & renewable resources",
            "Water treatment",
            "Life cycle assessment",
            "Corrosion, alloys & ceramics",
        ],
    },

    "Physics": {
        "Energy": [
            "Energy stores & transfers",
            "Specific heat capacity",
            "Power & efficiency",
            "National grid & energy resources",
        ],
        "Electricity": [
            "Current, voltage & resistance",
            "Series & parallel circuits",
            "Electrical power & energy",
            "Static electricity",
            "Mains electricity & safety",
        ],
        "Particle Model": [
            "Density & matter",
            "Changes of state & latent heat",
            "Gas laws & pressure",
        ],
        "Atomic Structure": [
            "Structure of the atom",
            "Radioactive decay & nuclear radiation",
            "Half-life & uses of radiation",
            "Nuclear fission & fusion",
        ],
        "Forces": [
            "Resultant forces & free body diagrams",
            "Newton's laws of motion",
            "Work, energy & power",
            "Momentum & conservation",
            "Pressure in fluids",
            "Circular motion & satellites",
        ],
        "Waves": [
            "Wave properties & calculations",
            "Reflection & refraction",
            "Sound waves & hearing",
            "Electromagnetic spectrum",
            "Uses & hazards of EM waves",
        ],
        "Magnetism & Electromagnetism": [
            "Magnets & magnetic fields",
            "Electromagnetism & solenoids",
            "Motor effect & Fleming's left hand rule",
            "Electromagnetic induction & generators",
            "Transformers",
        ],
        "Space": [
            "Solar system & orbits",
            "Life cycle of stars",
            "Red-shift & the expanding universe",
            "Big Bang theory",
        ],
    },

    "Biology": {
        "Cell Biology": [
            "Animal & plant cell structure",
            "Microscopy & scale",
            "Diffusion, osmosis & active transport",
            "Mitosis & the cell cycle",
            "Meiosis & stem cells",
        ],
        "Organisation": [
            "Digestive system & enzymes",
            "Blood & circulatory system",
            "Heart structure & disease",
            "Plant tissues & organs",
            "Transpiration & translocation",
        ],
        "Infection & Response": [
            "Pathogens & spread of disease",
            "Immune system & phagocytosis",
            "Vaccines & antibiotics",
            "Drug development & testing",
        ],
        "Bioenergetics": [
            "Photosynthesis & limiting factors",
            "Aerobic respiration",
            "Anaerobic respiration & fermentation",
            "Exercise & metabolism",
        ],
        "Homeostasis & Response": [
            "Nervous system & reflex arc",
            "The brain & the eye",
            "Hormones & the endocrine system",
            "Blood glucose control & diabetes",
            "Hormones in reproduction",
            "Contraception & fertility treatments",
        ],
        "Inheritance, Variation & Evolution": [
            "DNA, genes & chromosomes",
            "Inheritance & Punnett squares",
            "Sex determination & inherited disorders",
            "Variation & mutation",
            "Evolution & natural selection",
            "Classification of living organisms",
        ],
        "Ecology": [
            "Ecosystems & food chains",
            "Biotic & abiotic factors",
            "Adaptations & competition",
            "Biodiversity & conservation",
            "Human impact on the environment",
            "Sustainability & waste management",
        ],
    },
}

SUBJECTS = list(TOPICS.keys())
