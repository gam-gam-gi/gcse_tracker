"""
pipeline.py  ·  Walk GCSE_PATH folders, extract questions from PDFs,
classify with Claude API, store images + metadata in Supabase.
"""

import os, json, base64, re
from pathlib import Path
from datetime import datetime

import fitz                          # PyMuPDF
from dotenv import load_dotenv
from supabase import create_client
import anthropic

from config import GCSE_PATH, TOPICS, SUBJECTS, STORAGE_BUCKET

load_dotenv()

# ── Clients (initialised lazily) ──────────────────────────────────────────────
_sb  = None
_ai  = None

def supabase():
    global _sb
    if _sb is None:
        _sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY"))
    return _sb

def ai_client():
    global _ai
    if _ai is None:
        _ai = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _ai


# ── Folder scanner ────────────────────────────────────────────────────────────

def _is_mark_scheme(filename: str) -> bool:
    """Detect if a PDF is a mark scheme by its filename."""
    n = filename.lower()
    return any(x in n for x in [" ms", "_ms", "-ms", "ms.", "mark scheme",
                                  "mark_scheme", "markscheme", "solutions",
                                  "answer"])

def _find_mark_scheme(folder: Path, qp_name: str) -> Path | None:
    """Find a mark scheme PDF in the same folder as a question paper."""
    ms_files = [f for f in folder.glob("*.pdf") if _is_mark_scheme(f.name)]
    if not ms_files:
        return None
    if len(ms_files) == 1:
        return ms_files[0]
    # Multiple MS files — pick the one with the most similar name to the QP
    qp_stem = qp_name.lower().replace("qp", "").replace("question", "")
    for ms in ms_files:
        if qp_stem[:6] in ms.stem.lower():
            return ms
    return ms_files[0]

def scan_folders():
    """Return list of (pdf_path, subject, paper_type, ms_path_or_None)."""
    root = Path(GCSE_PATH)
    results = []

    for subject in SUBJECTS:
        subject_dir = root / subject
        if not subject_dir.exists():
            continue

        sub_dirs = [p for p in sorted(subject_dir.iterdir()) if p.is_dir()]

        if sub_dirs:
            for sub in sub_dirs:
                paper_type = sub.name
                qp_files = [f for f in sorted(sub.glob("*.pdf"))
                            if not _is_mark_scheme(f.name)]
                for pdf in qp_files:
                    ms = _find_mark_scheme(sub, pdf.name)
                    results.append((pdf, subject, paper_type, ms))
        else:
            qp_files = [f for f in sorted(subject_dir.glob("*.pdf"))
                        if not _is_mark_scheme(f.name)]
            for pdf in qp_files:
                ms = _find_mark_scheme(subject_dir, pdf.name)
                results.append((pdf, subject, "Paper 1", ms))

    return results


# ── Already-processed check ───────────────────────────────────────────────────

def already_processed(pdf_path: Path) -> bool:
    res = (supabase()
           .table("papers")
           .select("id")
           .eq("file_path", str(pdf_path))
           .eq("processed", True)
           .execute())
    return bool(res.data)


# ── PDF → page images ─────────────────────────────────────────────────────────

def pdf_to_page_images(pdf_path: Path, dpi: int = 150):
    """Return list of (page_number, png_bytes) for every page."""
    doc   = fitz.open(str(pdf_path))
    pages = []
    mat   = fitz.Matrix(dpi / 72, dpi / 72)
    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=mat)
        pages.append((i + 1, pix.tobytes("png")))
    doc.close()
    return pages


# ── Stitch multiple page images vertically ────────────────────────────────────

def stitch_page_images(png_bytes_list: list) -> bytes:
    """Combine multiple page PNG bytes into one tall image."""
    from PIL import Image
    import io
    images = [Image.open(io.BytesIO(b)).convert("RGB") for b in png_bytes_list]
    total_h = sum(img.height for img in images)
    max_w   = max(img.width  for img in images)
    combined = Image.new("RGB", (max_w, total_h), (255, 255, 255))
    y = 0
    for img in images:
        combined.paste(img, (0, y))
        y += img.height
    buf = io.BytesIO()
    combined.save(buf, format="PNG")
    return buf.getvalue()


# ── Upload one page image to Supabase Storage ─────────────────────────────────

def upload_page_image(paper_id: int, page_num: int, png_bytes: bytes) -> str:
    """Upload PNG bytes → Supabase Storage → return public URL."""
    path = f"papers/{paper_id}/page_{page_num:03d}.png"
    try:
        supabase().storage.from_(STORAGE_BUCKET).upload(
            path=path,
            file=png_bytes,
            file_options={"content-type": "image/png", "upsert": "true"},
        )
    except Exception as e:
        if "already exists" not in str(e).lower():
            raise
    url = supabase().storage.from_(STORAGE_BUCKET).get_public_url(path)
    return url


# ── Claude classification ─────────────────────────────────────────────────────

CLASSIFY_SYSTEM = """You are a GCSE examiner classifying exam questions.
Return ONLY a valid JSON array — no markdown, no explanation, no extra text.
Each element is one question object exactly as specified."""

def _topics_str(subject: str) -> str:
    lines = []
    for area, topics in TOPICS[subject].items():
        for t in topics:
            lines.append(f"  {area} >>> {t}")
    return "\n".join(lines)


def classify_paper(page_images: list, subject: str, paper_type: str) -> list:
    """
    Send all page images to Claude.
    Returns list of dicts:
      {number, page, marks, topic_area, topic, difficulty, brief_description, answer_guide}
    """
    content = []

    for page_num, png_bytes in page_images:
        b64 = base64.standard_b64encode(png_bytes).decode()
        content.append({"type": "text", "text": f"--- Page {page_num} ---"})
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        })

    topic_list = _topics_str(subject)
    content.append({
        "type": "text",
        "text": f"""
This is a GCSE Higher {subject} exam paper ({paper_type}).

Identify EVERY numbered question (e.g. 1, 2, 3 — not sub-parts like 1a, 1b).
Some questions span multiple pages — include ALL page numbers for those.

For each question return one JSON object:

{{
  "number": "1",
  "pages": [1],
  "marks": 3,
  "topic_area": "Algebra",
  "topic": "Linear equations",
  "difficulty": "Bronze",
  "brief_description": "One sentence describing what the question asks",
  "answer_guide": "Key answer facts for auto-marking"
}}

CRITICAL RULES:
1. "pages" must be an array e.g. [3] or [3,4] — include EVERY page the question appears on
2. "topic_area" and "topic" MUST be copied EXACTLY from the list below — do not paraphrase or invent names
3. Pick the single closest matching topic from the list

EXACT topic names to use (format: topic_area >>> topic):
{topic_list}

Difficulty:
  Bronze = 1–2 marks, recall / single-step
  Silver = 3–4 marks, method application
  Gold   = 5+ marks, multi-step / problem solving

Return ONLY the JSON array. No other text.
""",
    })

    response = ai_client().messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8000,
        system=CLASSIFY_SYSTEM,
        messages=[{"role": "user", "content": content}],
    )

    raw = response.content[0].text.strip()

    # Strip markdown code fences if present
    raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\n?```$",       "", raw, flags=re.MULTILINE)
    raw = raw.strip()

    # Try clean parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Try extracting a complete JSON array
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Recover truncated JSON — find last complete {...} object and close the array
    if raw.startswith("["):
        last_brace = raw.rfind("}")
        if last_brace > 0:
            recovered = raw[:last_brace + 1] + "]"
            try:
                result = json.loads(recovered)
                print(f"   ⚠  Recovered truncated JSON — got {len(result)} questions (response was cut off)")
                return result
            except json.JSONDecodeError:
                pass

    raise ValueError(f"Could not parse Claude response as JSON: {raw[:300]}")


# ── Topic lookup ──────────────────────────────────────────────────────────────

_topic_cache:    dict = {}
_subject_topics: dict = {}   # subject_name → list of {id, name, area}

STOP_WORDS = {"and", "the", "of", "in", "a", "an", "to", "for", "&", "its",
              "their", "with", "by", "from", "at", "on", "as"}

def _load_subject_topics(subject_name: str):
    """Cache all topics for a subject in memory."""
    if subject_name in _subject_topics:
        return
    sub = supabase().table("subjects").select("id").eq("name", subject_name).execute()
    if not sub.data:
        _subject_topics[subject_name] = []
        return
    subject_id = sub.data[0]["id"]
    rows = (supabase()
            .table("topics")
            .select("id, name, topic_areas(name)")
            .eq("subject_id", subject_id)
            .execute())
    _subject_topics[subject_name] = [
        {"id": r["id"],
         "name": r["name"],
         "area": (r.get("topic_areas") or {}).get("name", "")}
        for r in rows.data
    ]


def get_topic_id(subject_name: str, area_name: str, topic_name: str) -> int | None:
    key = (subject_name, area_name, topic_name)
    if key in _topic_cache:
        return _topic_cache[key]

    _load_subject_topics(subject_name)
    rows = _subject_topics.get(subject_name, [])
    if not rows:
        return None

    tn_lower  = topic_name.lower().strip()
    area_lower = area_name.lower().strip()

    # 1. Exact match on both topic + area
    for r in rows:
        if r["name"].lower() == tn_lower and r["area"].lower() == area_lower:
            _topic_cache[key] = r["id"]; return r["id"]

    # 2. Exact match on topic name only
    for r in rows:
        if r["name"].lower() == tn_lower:
            _topic_cache[key] = r["id"]; return r["id"]

    # 3. Substring match (either direction)
    for r in rows:
        rn = r["name"].lower()
        if tn_lower in rn or rn in tn_lower:
            _topic_cache[key] = r["id"]; return r["id"]

    # 4. Word-overlap score (best match with ≥2 meaningful words in common)
    needle_words = set(tn_lower.split()) - STOP_WORDS
    best_id, best_score = None, 0
    for r in rows:
        hay_words = set(r["name"].lower().split()) - STOP_WORDS
        score = len(needle_words & hay_words)
        if score > best_score:
            best_score, best_id = score, r["id"]
    if best_score >= 2:
        _topic_cache[key] = best_id; return best_id

    # 5. Area-only fallback — if area matches, return first topic in that area
    for r in rows:
        if r["area"].lower() == area_lower:
            _topic_cache[key] = r["id"]; return r["id"]

    return None


# ── Main: process one PDF ─────────────────────────────────────────────────────

def process_paper(pdf_path: Path, subject: str, paper_type: str,
                  progress_callback=None, ms_path: Path | None = None) -> dict:
    """
    Full pipeline for one PDF.
    progress_callback(message: str, pct: float)  — optional UI hook.
    Returns {"paper_id": int, "questions_added": int, "skipped": bool}
    """

    def log(msg, pct=0.0):
        if progress_callback:
            progress_callback(msg, pct)
        else:
            print(msg)

    if already_processed(pdf_path):
        log(f"⏭  Skipping (already processed): {pdf_path.name}", 1.0)
        return {"skipped": True}

    log(f"📄  Processing: {pdf_path.name}", 0.05)

    # 1. Insert paper row (unprocessed)
    sub_res = (supabase()
               .table("subjects")
               .select("id")
               .eq("name", subject)
               .execute())
    if not sub_res.data:
        raise ValueError(f"Subject '{subject}' not found in database. Run setup.py first.")
    subject_id = sub_res.data[0]["id"]

    paper_res = supabase().table("papers").insert({
        "subject_id":  subject_id,
        "paper_type":  paper_type,
        "filename":    pdf_path.name,
        "file_path":   str(pdf_path),
        "processed":   False,
    }).execute()
    paper_id = paper_res.data[0]["id"]

    # 2. Convert pages to images
    log(f"🖼   Converting pages …", 0.10)
    page_images = pdf_to_page_images(pdf_path)
    log(f"   {len(page_images)} pages converted", 0.20)

    # Keep bytes in memory for stitching multi-page questions
    page_bytes = {page_num: png_bytes for page_num, png_bytes in page_images}

    # 3. Upload page images to Supabase Storage
    log("☁️   Uploading page images …", 0.25)
    page_urls: dict[int, str] = {}
    for i, (page_num, png_bytes) in enumerate(page_images):
        url = upload_page_image(paper_id, page_num, png_bytes)
        page_urls[page_num] = url
        pct = 0.25 + 0.25 * (i / len(page_images))
        log(f"   Uploaded page {page_num}/{len(page_images)}", pct)

    # 4. Classify with Claude
    log("🤖  Classifying questions with Claude …", 0.50)
    questions = classify_paper(page_images, subject, paper_type)
    log(f"   {len(questions)} questions identified", 0.70)

    # 5. Insert questions
    added = 0
    for i, q in enumerate(questions):
        topic_id = get_topic_id(subject, q.get("topic_area", ""), q.get("topic", ""))

        # Handle pages — support both "pages": [3,4] and legacy "page": 3
        q_pages = q.get("pages", None)
        if q_pages is None:
            q_pages = [q.get("page", 1)]
        if isinstance(q_pages, int):
            q_pages = [q_pages]
        q_pages = sorted(set(q_pages))  # deduplicate and sort

        # Build image URL — stitch if multi-page
        if len(q_pages) > 1:
            imgs = [page_bytes[p] for p in q_pages if p in page_bytes]
            if len(imgs) > 1:
                stitched = stitch_page_images(imgs)
                stitch_path = f"papers/{paper_id}/q{q.get('number', i+1)}_combined.png"
                try:
                    supabase().storage.from_(STORAGE_BUCKET).upload(
                        path=stitch_path,
                        file=stitched,
                        file_options={"content-type": "image/png", "upsert": "true"},
                    )
                except Exception:
                    pass
                image_url = supabase().storage.from_(STORAGE_BUCKET).get_public_url(stitch_path)
            else:
                image_url = page_urls.get(q_pages[0], "")
        else:
            image_url = page_urls.get(q_pages[0], "")

        supabase().table("questions").insert({
            "paper_id":           paper_id,
            "topic_id":           topic_id,
            "question_number":    str(q.get("number", i + 1)),
            "brief_description":  q.get("brief_description", ""),
            "image_url":          image_url,
            "page_number":        q_pages[0],
            "difficulty":         q.get("difficulty", "Silver"),
            "marks":              int(q.get("marks", 1)),
            "answer_guide":       q.get("answer_guide", ""),
            "mark_scheme_text":   "",   # filled in next step if MS available
        }).execute()
        added += 1
        pct = 0.70 + 0.28 * (i / len(questions))
        log(f"   Saved Q{q.get('number', i+1)} (pages {q_pages})", pct)

    # 6. Extract mark scheme if available
    if ms_path and ms_path.exists():
        log(f"📋  Reading mark scheme: {ms_path.name}", 0.95)
        try:
            ms_images = pdf_to_page_images(ms_path, dpi=120)
            ms_content = extract_mark_scheme(ms_images, questions)
            # Update each question with its mark scheme text
            q_rows = (supabase().table("questions")
                      .select("id, question_number")
                      .eq("paper_id", paper_id)
                      .execute())
            for row in q_rows.data:
                ms_text = ms_content.get(str(row["question_number"]), "")
                if ms_text:
                    supabase().table("questions").update({
                        "mark_scheme_text": ms_text
                    }).eq("id", row["id"]).execute()
            log(f"   Mark scheme extracted for {len(ms_content)} questions", 0.98)
        except Exception as e:
            log(f"   ⚠  Mark scheme extraction failed: {e}", 0.98)

    # 7. Mark paper as processed
    supabase().table("papers").update({
        "processed":        True,
        "processed_at":     datetime.utcnow().isoformat(),
        "total_questions":  added,
    }).eq("id", paper_id).execute()

    log(f"✅  Done: {pdf_path.name} — {added} questions added", 1.0)
    return {"paper_id": paper_id, "questions_added": added, "skipped": False}


# ── Mark scheme extraction ────────────────────────────────────────────────────

def extract_mark_scheme(ms_page_images: list, questions: list) -> dict:
    """Read mark scheme pages and return {question_number: mark_scheme_text}."""
    content = []
    for page_num, png_bytes in ms_page_images:
        b64 = base64.standard_b64encode(png_bytes).decode()
        content.append({"type": "text", "text": f"--- Mark Scheme Page {page_num} ---"})
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        })

    q_numbers = [str(q.get("number", i+1)) for i, q in enumerate(questions)]
    content.append({
        "type": "text",
        "text": f"""This is a GCSE mark scheme.
Extract the marking points for each question number listed below.
Include ALL mark scheme guidance: B marks, M marks, A marks, accepted alternatives, common errors noted.

Questions to find: {q_numbers}

Return ONLY a JSON object — no other text:
{{
  "1": "B1 for correct answer. Accept equivalent forms.",
  "2": "M1 for correct method. A1 for final answer. Allow ±0.1 tolerance.",
  "3": "..."
}}

If a question is not found in the mark scheme, omit it from the JSON."""
    })

    response = ai_client().messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=6000,
        messages=[{"role": "user", "content": content}],
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\n?```$",       "", raw, flags=re.MULTILINE)
    try:
        return json.loads(raw.strip())
    except Exception:
        return {}


# ── Process all unprocessed papers ───────────────────────────────────────────

def process_all(progress_callback=None) -> list:
    papers = scan_folders()
    results = []
    for item in papers:
        pdf_path, subject, paper_type = item[0], item[1], item[2]
        ms_path = item[3] if len(item) > 3 else None
        try:
            r = process_paper(pdf_path, subject, paper_type,
                              progress_callback=progress_callback,
                              ms_path=ms_path)
            results.append({"file": pdf_path.name, **r})
        except Exception as e:
            msg = f"❌  Error processing {pdf_path.name}: {e}"
            if progress_callback:
                progress_callback(msg, 0)
            else:
                print(msg)
            results.append({"file": pdf_path.name, "error": str(e)})
    return results


if __name__ == "__main__":
    process_all()


# ── Web upload versions (no local file path needed) ───────────────────────────

def already_processed_by_name(filename: str) -> bool:
    """Check if a paper filename has already been processed."""
    res = (supabase()
           .table("papers")
           .select("id")
           .eq("filename", filename)
           .eq("processed", True)
           .execute())
    return bool(res.data)


def pdf_to_page_images_from_bytes(pdf_bytes: bytes, dpi: int = 150):
    """Convert PDF bytes → list of (page_number, png_bytes)."""
    doc   = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    mat   = fitz.Matrix(dpi / 72, dpi / 72)
    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=mat)
        pages.append((i + 1, pix.tobytes("png")))
    doc.close()
    return pages


def process_paper_from_bytes(qp_bytes: bytes, filename: str,
                              subject: str, paper_type: str,
                              ms_bytes: bytes | None = None,
                              progress_callback=None) -> dict:
    """
    Process an uploaded PDF (bytes) — web version of process_paper().
    Returns {"paper_id": int, "questions_added": int}
    """
    def log(msg, pct=0.0):
        if progress_callback:
            progress_callback(msg, pct)
        else:
            print(msg)

    log(f"📄  Processing: {filename}", 0.05)

    # Get subject ID
    sub_res = supabase().table("subjects").select("id").eq("name", subject).execute()
    if not sub_res.data:
        raise ValueError(f"Subject '{subject}' not found. Run setup.py first.")
    subject_id = sub_res.data[0]["id"]

    # Insert paper row
    paper_res = supabase().table("papers").insert({
        "subject_id": subject_id,
        "paper_type": paper_type,
        "filename":   filename,
        "file_path":  f"web_upload/{filename}",
        "processed":  False,
    }).execute()
    paper_id = paper_res.data[0]["id"]

    # Convert pages
    log("🖼   Converting pages …", 0.10)
    page_images = pdf_to_page_images_from_bytes(qp_bytes)
    page_bytes  = {pn: pb for pn, pb in page_images}
    log(f"   {len(page_images)} pages converted", 0.20)

    # Upload page images
    log("☁️   Uploading page images …", 0.25)
    page_urls: dict[int, str] = {}
    for i, (page_num, png_bytes) in enumerate(page_images):
        url = upload_page_image(paper_id, page_num, png_bytes)
        page_urls[page_num] = url
        log(f"   Uploaded page {page_num}/{len(page_images)}",
            0.25 + 0.25 * (i / len(page_images)))

    # Classify with Claude
    log("🤖  Classifying with Claude …", 0.50)
    questions = classify_paper(page_images, subject, paper_type)
    log(f"   {len(questions)} questions identified", 0.70)

    # Insert questions
    added = 0
    for i, q in enumerate(questions):
        topic_id = get_topic_id(subject, q.get("topic_area", ""), q.get("topic", ""))

        q_pages = q.get("pages", [q.get("page", 1)])
        if isinstance(q_pages, int): q_pages = [q_pages]
        q_pages = sorted(set(q_pages))

        if len(q_pages) > 1:
            imgs = [page_bytes[p] for p in q_pages if p in page_bytes]
            if len(imgs) > 1:
                stitched = stitch_page_images(imgs)
                spath = f"papers/{paper_id}/q{q.get('number', i+1)}_combined.png"
                try:
                    supabase().storage.from_(STORAGE_BUCKET).upload(
                        path=spath, file=stitched,
                        file_options={"content-type": "image/png", "upsert": "true"})
                except Exception:
                    pass
                image_url = supabase().storage.from_(STORAGE_BUCKET).get_public_url(spath)
            else:
                image_url = page_urls.get(q_pages[0], "")
        else:
            image_url = page_urls.get(q_pages[0], "")

        supabase().table("questions").insert({
            "paper_id":          paper_id,
            "topic_id":          topic_id,
            "question_number":   str(q.get("number", i + 1)),
            "brief_description": q.get("brief_description", ""),
            "image_url":         image_url,
            "page_number":       q_pages[0],
            "difficulty":        q.get("difficulty", "Silver"),
            "marks":             int(q.get("marks", 1)),
            "answer_guide":      q.get("answer_guide", ""),
            "mark_scheme_text":  "",
        }).execute()
        added += 1
        log(f"   Saved Q{q.get('number', i+1)} (pages {q_pages})",
            0.70 + 0.25 * (i / len(questions)))

    # Mark scheme
    if ms_bytes:
        log("📋  Reading mark scheme …", 0.95)
        try:
            ms_images  = pdf_to_page_images_from_bytes(ms_bytes, dpi=120)
            ms_content = extract_mark_scheme(ms_images, questions)
            q_rows = (supabase().table("questions")
                      .select("id, question_number")
                      .eq("paper_id", paper_id).execute())
            for row in q_rows.data:
                ms_text = ms_content.get(str(row["question_number"]), "")
                if ms_text:
                    supabase().table("questions").update({
                        "mark_scheme_text": ms_text
                    }).eq("id", row["id"]).execute()
            log(f"   Mark scheme extracted for {len(ms_content)} questions", 0.98)
        except Exception as e:
            log(f"   ⚠  Mark scheme failed: {e}", 0.98)

    # Mark complete
    supabase().table("papers").update({
        "processed":       True,
        "processed_at":    datetime.utcnow().isoformat(),
        "total_questions": added,
    }).eq("id", paper_id).execute()

    log(f"✅  Done: {filename} — {added} questions added", 1.0)
    return {"paper_id": paper_id, "questions_added": added}
