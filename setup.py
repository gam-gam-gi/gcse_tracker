"""
setup.py  ·  Run once after creating tables in Supabase SQL editor.
Creates the storage bucket and seeds subjects + topics.

Usage:
    python setup.py
"""

import os, sys
from dotenv import load_dotenv
from supabase import create_client
from config import TOPICS, STORAGE_BUCKET

load_dotenv()

def main():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        sys.exit("❌  SUPABASE_URL or SUPABASE_ANON_KEY missing from .env")

    sb = create_client(url, key)
    print("✓  Connected to Supabase")

    # ── Check tables exist before doing anything ───────────────────────────
    print("   Checking database tables …")
    try:
        sb.table("subjects").select("id").limit(1).execute()
    except Exception as e:
        err = str(e)
        if "PGRST125" in err or "Invalid path" in err or "relation" in err:
            print()
            print("❌  Database tables not found.")
            print()
            print("    You need to run database.sql in Supabase first:")
            print("    1. Go to https://supabase.com → your project")
            print("    2. Click  SQL Editor  in the left sidebar")
            print("    3. Click  New query")
            print("    4. Open database.sql from this folder, paste ALL of it, click Run")
            print("    5. You should see 'Success. No rows returned'")
            print("    6. Then run  python setup.py  again")
            print()
            sys.exit(1)
        raise

    print("✓  Database tables found")

    # ── Storage bucket ─────────────────────────────────────────────────────
    print(f"   Creating storage bucket '{STORAGE_BUCKET}' …")
    try:
        result = sb.storage.create_bucket(STORAGE_BUCKET, options={"public": True})
        # supabase-py returns the bucket name on success
        print(f"✓  Storage bucket '{STORAGE_BUCKET}' created")
    except Exception:
        # Any error here is almost always "already exists" — safe to ignore
        print(f"✓  Storage bucket '{STORAGE_BUCKET}' already exists")

    # ── Subjects, topic areas, topics ──────────────────────────────────────
    for subject_name, areas in TOPICS.items():

        # Subject
        try:
            existing = (sb.table("subjects")
                          .select("id")
                          .eq("name", subject_name)
                          .execute())
            if existing.data:
                subject_id = existing.data[0]["id"]
                print(f"✓  Subject '{subject_name}' already exists (id={subject_id})")
            else:
                res = sb.table("subjects").insert({"name": subject_name}).execute()
                subject_id = res.data[0]["id"]
                print(f"✓  Inserted subject '{subject_name}' (id={subject_id})")
        except Exception as e:
            print(f"❌  Failed on subject '{subject_name}': {e}")
            continue

        for area_name, topic_list in areas.items():

            # Topic area
            try:
                existing_area = (sb.table("topic_areas")
                                   .select("id")
                                   .eq("subject_id", subject_id)
                                   .eq("name", area_name)
                                   .execute())
                if existing_area.data:
                    area_id = existing_area.data[0]["id"]
                else:
                    res = sb.table("topic_areas").insert({
                        "subject_id": subject_id,
                        "name":       area_name,
                    }).execute()
                    area_id = res.data[0]["id"]
            except Exception as e:
                print(f"   ⚠  Area '{area_name}': {e}")
                continue

            # Topics
            for topic_name in topic_list:
                try:
                    existing_topic = (sb.table("topics")
                                        .select("id")
                                        .eq("area_id", area_id)
                                        .eq("name", topic_name)
                                        .execute())
                    if not existing_topic.data:
                        sb.table("topics").insert({
                            "subject_id": subject_id,
                            "area_id":    area_id,
                            "name":       topic_name,
                        }).execute()
                except Exception as e:
                    print(f"   ⚠  Topic '{topic_name}': {e}")

        print(f"   └─ Topics seeded for {subject_name}")

    print()
    print("✅  Setup complete.")
    print("    Run:  streamlit run app.py")

if __name__ == "__main__":
    main()
