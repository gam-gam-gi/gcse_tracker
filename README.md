# GCSE Tracker

Progress tracker for M1 and M2 — classifies GCSE papers with Claude AI, tracks scores by topic.

---

## Setup (one-time, ~15 minutes)

### Step 1 — Create a free Supabase account
1. Go to https://supabase.com and sign up (free)
2. Click **New project**, give it a name (e.g. `gcse-tracker`)
3. Wait ~2 minutes for it to initialise

### Step 2 — Create the database tables
1. In your Supabase project, click **SQL Editor** in the left sidebar
2. Click **New query**
3. Open `database.sql` from this folder, paste the entire contents, click **Run**
4. You should see "Success. No rows returned"

### Step 3 — Get your Supabase credentials
1. In Supabase, go to **Project Settings → API**
2. Copy **Project URL** and **anon / public** key

### Step 4 — Fill in your API keys
1. Copy `.env.template` → rename to `.env`
2. Fill in the three values:
```
ANTHROPIC_API_KEY=sk-ant-...        ← your Anthropic API key
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=eyJ...
```

### Step 5 — Install Python dependencies
```bash
pip install -r requirements.txt
```

### Step 6 — Run one-time setup
```bash
python setup.py
```
This creates the storage bucket and seeds all subjects and topics.
You should see a list of ✓ messages ending with "Setup complete".

### Step 7 — Start the app
```bash
streamlit run app.py
```

Your browser opens automatically. Choose **Parent** on your machine.

---

## Setting up M1 and M2 laptops

1. Copy this entire `gcse_tracker` folder to each laptop (USB stick or shared folder)
2. On each laptop, install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy your `.env` file into the folder (same credentials — they share the same database)
4. Run:
   ```bash
   streamlit run app.py
   ```
5. Choose **Student** and select M1 or M2

That's it. Both students connect to the same Supabase database automatically.

---

## Folder structure for your GCSE papers

```
C:\Users\Thanuja\OneDrive\Desktop\GCSE\
├── Maths\
│   ├── Paper 1\
│   │   ├── nov2024_p1.pdf
│   │   └── june2023_p1.pdf
│   ├── Paper 2\
│   │   └── nov2024_p2.pdf
│   └── Paper 3\
│       └── ...
├── Chemistry\
│   ├── nov2024_chem.pdf
│   └── june2023_chem.pdf
├── Physics\
│   └── ...
└── Biology\
    └── ...
```

Maths needs Paper 1 / Paper 2 / Paper 3 sub-folders.
Chemistry, Physics and Biology PDFs sit directly in their subject folder.

---

## Weekly workflow

**You (parent):**
1. Drop new PDF papers into the right folder
2. Open the app → ⚙️ Process Papers → click **Process all pending**
3. Go to 📚 Question Bank → assign questions to M1, M2, or Both

**M1 and M2:**
1. Open the app → 📝 Practice
2. Work through assigned questions
3. Type answer → click Submit → Claude marks it instantly
4. View progress in 📈 My Progress

**You (parent), weekly:**
- Dashboard shows side-by-side topic heatmap for both students
- 📅 Weekly Report shows that week's activity

---

## Costs

All costs go to your Anthropic API account.

| Task | Cost |
|------|------|
| Classify one exam paper | ~1p |
| Mark one student answer | ~0.05p |
| Typical week (both students) | ~15–20p |
| Full year | < £10 |

Supabase is free at this scale (well under the free tier limits).

---

## Troubleshooting

**"SUPABASE_URL missing"** — make sure `.env` file exists (not `.env.template`) and contains your keys.

**"Subject not found in database"** — run `python setup.py` again.

**"No PDF files found"** — check `GCSE_PATH` in `config.py` matches your folder exactly.

**Images not showing** — make sure the Supabase storage bucket is set to **public** (setup.py does this automatically).
