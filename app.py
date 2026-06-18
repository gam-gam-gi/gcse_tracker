"""
app.py  ·  GCSE Tracker
Run:   streamlit run app.py
"""

import os, json, base64, re
from pathlib import Path
from datetime import date, timedelta, datetime

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import anthropic
from dotenv import load_dotenv
from supabase import create_client
from streamlit_drawable_canvas import st_canvas
from PIL import Image
import io, numpy as np

from config import TOPICS, SUBJECTS, STUDENTS, DIFFICULTY_COLOURS, SCORE_COLOURS, STORAGE_BUCKET
from pipeline import process_paper_from_bytes, already_processed_by_name

load_dotenv()

# ── Streamlit page config ─────────────────────────────────────────────────────
st.set_page_config(
    page_title="GCSE Tracker",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Clients ───────────────────────────────────────────────────────────────────
def get_supabase():
    return create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY"))

@st.cache_resource
def get_ai():
    return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ─────────────────────────────────────────────────────────────────────────────
# PIN LOGIN
# ─────────────────────────────────────────────────────────────────────────────
def login_screen():
    st.title("📚 GCSE Tracker")
    st.markdown("### Please log in")
    st.divider()
    role = st.radio("Who are you?", ["Parent", "M1", "M2", "M3"], horizontal=True)
    pin  = st.text_input("PIN", type="password", max_chars=6,
                          placeholder="Enter your PIN")
    if st.button("🔓 Login", type="primary", use_container_width=True):
        pins = {
            "Parent": os.getenv("PARENT_PIN", "1234"),
            "M1":     os.getenv("M1_PIN",     "1234"),
            "M2":     os.getenv("M2_PIN",     "1234"),
            "M3":     os.getenv("M3_PIN",     "1234"),
        }
        if pin == pins[role]:
            st.session_state.mode    = "parent" if role == "Parent" else "student"
            st.session_state.student = role
            st.rerun()
        elif pin:
            st.error("Incorrect PIN — try again")

# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def score_colour(pct):
    if pct is None:  return SCORE_COLOURS["none"]
    if pct >= 75:    return SCORE_COLOURS["strong"]
    if pct >= 50:    return SCORE_COLOURS["ok"]
    return SCORE_COLOURS["weak"]

def topic_scores_for(sb, student: str, subject: str) -> pd.DataFrame:
    """Return DataFrame with columns: topic_area, topic, avg_pct, attempts"""
    res = (sb.table("attempts")
             .select("score, max_score, questions(topic_id, topics(name, topic_areas(name)))")
             .eq("student_name", student)
             .execute())
    rows = []
    for a in res.data:
        q = a.get("questions") or {}
        t = q.get("topics") or {}
        area = (t.get("topic_areas") or {}).get("name", "Unknown")
        rows.append({
            "topic_area": area,
            "topic":      t.get("name", "Unknown"),
            "score":      a["score"],
            "max_score":  a["max_score"] or 1,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # Filter by subject (need a join — proxy via area names being unique to subject)
    df["pct"] = df["score"] / df["max_score"] * 100
    return (df.groupby(["topic_area", "topic"])
              .agg(avg_pct=("pct", "mean"), attempts=("pct", "count"))
              .reset_index())

def heatmap_fig(df: pd.DataFrame, title: str):
    if df.empty:
        fig = go.Figure()
        fig.add_annotation(text="No attempts yet", xref="paper", yref="paper",
                           x=0.5, y=0.5, showarrow=False, font=dict(size=16))
        fig.update_layout(title=title, height=300)
        return fig

    colours = [score_colour(p) for p in df["avg_pct"]]
    fig = go.Figure(go.Bar(
        x=df["topic"],
        y=df["avg_pct"],
        marker_color=colours,
        text=[f"{p:.0f}%" for p in df["avg_pct"]],
        textposition="outside",
        hovertemplate="<b>%{x}</b><br>Avg: %{y:.1f}%<extra></extra>",
    ))
    fig.update_layout(
        title=title,
        yaxis=dict(title="Avg score %", range=[0, 115]),
        xaxis=dict(tickangle=-30),
        height=360,
        margin=dict(t=50, b=120),
        plot_bgcolor="white",
    )
    return fig

# ─────────────────────────────────────────────────────────────────────────────
# PARENT MODE PAGES
# ─────────────────────────────────────────────────────────────────────────────

def page_dashboard(sb):
    st.title("📊 Dashboard")
    col_m1, col_m2 = st.columns(2)

    for col, student in zip([col_m1, col_m2], STUDENTS):
        with col:
            st.subheader(f"🎓 {student}")

            # Summary stats
            att = sb.table("attempts").select("score, max_score").eq("student_name", student).execute()
            total_q = len(att.data)
            if total_q:
                total_score = sum(a["score"] for a in att.data)
                total_max   = sum((a["max_score"] or 1) for a in att.data)
                overall_pct = total_score / total_max * 100
            else:
                overall_pct = None

            a1, a2 = st.columns(2)
            a1.metric("Questions attempted", total_q)
            a2.metric("Overall score", f"{overall_pct:.0f}%" if overall_pct else "—")

            # Topic heatmap (Maths only for MVP)
            df = topic_scores_for(sb, student, "Maths")
            st.plotly_chart(heatmap_fig(df, f"{student} · Topic scores"), use_container_width=True)


def page_process_papers(sb):
    st.title("⚙️ Process Papers")

    # ── Upload new paper ──────────────────────────────────────────────────
    st.subheader("Upload a new paper")
    c1, c2 = st.columns(2)
    subject    = c1.selectbox("Subject",    SUBJECTS)
    paper_type = c2.selectbox("Paper type", ["Paper 1", "Paper 2", "Paper 3"])

    qp_file = st.file_uploader("📄 Question Paper PDF", type=["pdf"])
    ms_file = st.file_uploader("📋 Mark Scheme PDF (optional — improves marking accuracy)",
                                type=["pdf"])

    if qp_file:
        if already_processed_by_name(qp_file.name):
            st.warning(f"'{qp_file.name}' has already been processed.")
            if not st.checkbox("Process again anyway"):
                st.stop()

        if st.button("🚀 Process Paper", type="primary", use_container_width=True):
            qp_bytes = qp_file.read()
            ms_bytes = ms_file.read() if ms_file else None

            progress_bar = st.progress(0.0)
            status_text  = st.empty()
            log_area     = st.empty()
            log_lines    = []

            def cb(msg, pct):
                progress_bar.progress(min(pct, 1.0))
                status_text.text(msg)
                log_lines.append(msg)
                log_area.code("\n".join(log_lines[-15:]))

            try:
                result = process_paper_from_bytes(
                    qp_bytes      = qp_bytes,
                    filename      = qp_file.name,
                    subject       = subject,
                    paper_type    = paper_type,
                    ms_bytes      = ms_bytes,
                    progress_callback = cb,
                )
                st.success(f"✅ Done — {result['questions_added']} questions added to the bank!")
                st.balloons()
            except Exception as e:
                st.error(f"Error: {e}")

    # ── Already processed papers ──────────────────────────────────────────
    st.divider()
    st.subheader("Processed papers")
    papers = (sb.table("papers")
                .select("filename, paper_type, total_questions, processed_at, subjects(name)")
                .eq("processed", True)
                .order("processed_at", desc=True)
                .execute())
    if papers.data:
        rows = []
        for p in papers.data:
            rows.append({
                "Subject":    (p.get("subjects") or {}).get("name", "?"),
                "Paper type": p["paper_type"],
                "File":       p["filename"],
                "Questions":  p["total_questions"],
                "Date":       (p.get("processed_at") or "")[:10],
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No papers processed yet — upload one above.")


def page_question_bank(sb):
    st.title("📚 Question Bank")

    # ── Filters ───────────────────────────────────────────────────────────
    fc1, fc2, fc3, fc4 = st.columns(4)
    subject_f = fc1.selectbox("Subject",    ["All"] + SUBJECTS)
    area_f    = fc2.selectbox("Area",       ["All"])   # populated dynamically below
    diff_f    = fc3.selectbox("Difficulty", ["All", "Bronze", "Silver", "Gold"])
    search_f  = fc4.text_input("Search description")
    student_f = st.selectbox("Assign selected to", ["—"] + STUDENTS + ["Both"])

    # ── Fetch ─────────────────────────────────────────────────────────────
    query = (sb.table("questions")
               .select("id, question_number, brief_description, difficulty, marks, image_url, "
                       "papers(filename, paper_type, subjects(name)), "
                       "topics(name, topic_areas(name))")
               .order("question_number"))
    if diff_f != "All":
        query = query.eq("difficulty", diff_f)
    if search_f:
        query = query.ilike("brief_description", f"%{search_f}%")

    items = query.limit(500).execute().data

    # Filter by subject in Python
    if subject_f != "All":
        items = [q for q in items
                 if (q.get("papers") or {}).get("subjects", {}).get("name") == subject_f]

    if not items:
        st.info("No questions found for those filters.")
        return

    st.caption(f"{len(items)} questions")

    # ── Group: Subject → Area → [questions] ───────────────────────────────
    from collections import defaultdict
    grouped = defaultdict(lambda: defaultdict(list))

    for q in items:
        subj = (q.get("papers") or {}).get("subjects", {}).get("name", "Unknown")
        area = ((q.get("topics") or {}).get("topic_areas") or {}).get("name", "Other")
        grouped[subj][area].append(q)

    # ── Render ────────────────────────────────────────────────────────────
    for subj in sorted(grouped):
        if len(grouped) > 1:
            st.markdown(f"## 📖 {subj}")

        for area in sorted(grouped[subj]):
            qs = grouped[subj][area]
            with st.expander(f"**{area}** — {len(qs)} questions", expanded=False):

                # ── Bulk assign bar ───────────────────────────────────────
                if student_f != "—":
                    targets = STUDENTS if student_f == "Both" else [student_f]
                    bar_col1, bar_col2 = st.columns([3, 1])
                    with bar_col1:
                        st.caption(f"Assign all {len(qs)} {area} questions to {', '.join(targets)}")
                    with bar_col2:
                        if st.button(f"Assign all", key=f"bulk_{subj}_{area}",
                                     type="primary", use_container_width=True):
                            added = 0
                            for q in qs:
                                for s in targets:
                                    try:
                                        sb.table("assignments").insert({
                                            "question_id":   q["id"],
                                            "student_name":  s,
                                            "assigned_date": date.today().isoformat(),
                                        }).execute()
                                        added += 1
                                    except Exception:
                                        pass  # already assigned
                            st.success(f"✅ {added} questions assigned to {', '.join(targets)}")
                    st.divider()

                # ── Individual questions ──────────────────────────────────
                for q in qs:
                    topic = (q.get("topics") or {}).get("name", "?")
                    diff  = q.get("difficulty", "Silver")
                    pt    = (q.get("papers") or {}).get("paper_type", "")

                    col_info, col_img, col_btn = st.columns([3, 2, 1])

                    with col_info:
                        badge = {"Bronze": "🥉", "Silver": "🥈", "Gold": "🥇"}.get(diff, "•")
                        st.markdown(
                            f"**Q{q['question_number']}** · {topic}  \n"
                            f"{badge} {diff} · {q['marks']} marks · *{pt}*"
                        )
                        if q.get("brief_description"):
                            st.caption(q["brief_description"][:80])

                    with col_img:
                        if q.get("image_url"):
                            st.image(q["image_url"], use_container_width=True)

                    with col_btn:
                        if student_f != "—":
                            if st.button("Assign", key=f"a_{q['id']}",
                                         use_container_width=True):
                                for s in targets:
                                    try:
                                        sb.table("assignments").insert({
                                            "question_id":   q["id"],
                                            "student_name":  s,
                                            "assigned_date": date.today().isoformat(),
                                        }).execute()
                                    except Exception:
                                        pass
                                st.success(f"→ {', '.join(targets)}")

                    st.divider()


def page_weekly_report(sb):
    st.title("📅 Weekly Report & Attempt History")

    # Time filter
    tf1, tf2 = st.columns([2, 2])
    days_back = tf1.selectbox("Show", [7, 14, 30, 90], format_func=lambda d: f"Last {d} days")
    week_ago  = (date.today() - timedelta(days=days_back)).isoformat()

    for student in STUDENTS:
        st.subheader(f"🎓 {student}")

        # All attempts in the period
        res = (sb.table("attempts")
                 .select("id, score, max_score, attempt_date, round, "
                         "working_image_url, claude_feedback, question_id, "
                         "questions(id, brief_description, difficulty, marks, "
                         "papers(subjects(name)), topics(name))")
                 .eq("student_name", student)
                 .gte("attempt_date", week_ago)
                 .order("attempt_date", desc=True)
                 .execute())

        if not res.data:
            st.info(f"No activity in the last {days_back} days.")
            st.divider()
            continue

        # Summary stats
        total_score = sum(a["score"] for a in res.data)
        total_max   = sum((a["max_score"] or 1) for a in res.data)
        avg_pct     = total_score / total_max * 100 if total_max else 0
        sc1, sc2, sc3 = st.columns(3)
        sc1.metric("Attempts", len(res.data))
        sc2.metric("Total score", f"{total_score}/{total_max}")
        sc3.metric("Average", f"{avg_pct:.0f}%")

        # Group by question for history view
        from collections import defaultdict
        q_attempts = defaultdict(list)
        for a in res.data:
            q_attempts[a["question_id"]].append(a)

        # Table summary
        rows = []
        for a in res.data:
            q    = a.get("questions") or {}
            rows.append({
                "Date":        a["attempt_date"][:10],
                "Subject":     ((q.get("papers") or {}).get("subjects") or {}).get("name", "?"),
                "Topic":       (q.get("topics") or {}).get("name", "?"),
                "Description": (q.get("brief_description") or "")[:55],
                "Difficulty":  q.get("difficulty", "?"),
                "Attempt":     f"#{a.get('round', 1)}",
                "Score":       f"{a['score']}/{a['max_score'] or q.get('marks',1)}",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        # ── Per-question history with working, override, reassign ─────────
        with st.expander(f"📸 View working & manage ({len(q_attempts)} questions)"):
            for qid, attempts in q_attempts.items():
                latest = attempts[0]   # most recent first
                q      = latest.get("questions") or {}
                subj   = ((q.get("papers") or {}).get("subjects") or {}).get("name", "?")
                topic  = (q.get("topics") or {}).get("name", "?")
                desc   = (q.get("brief_description") or "")[:70]
                max_m  = latest.get("max_score") or q.get("marks", 1) or 1

                st.markdown(f"**{subj} · {topic}**  \n_{desc}_")

                # Show all attempts for this question
                for att in reversed(attempts):   # oldest first
                    rnd     = att.get("round", 1)
                    score   = att["score"]
                    icon    = "✅" if score >= max_m * 0.7 else "❌"
                    a_col, b_col, c_col = st.columns([3, 1, 1])

                    with a_col:
                        st.caption(
                            f"{icon} Attempt #{rnd} · {att['attempt_date'][:10]} · "
                            f"**{score}/{max_m}** · {att.get('claude_feedback','')[:80]}"
                        )
                        if att.get("working_image_url"):
                            st.image(att["working_image_url"], use_container_width=True)

                    with b_col:
                        # Score override
                        new_s = st.number_input(
                            "Score", 0, max_m, score,
                            key=f"ov_{att['id']}",
                            label_visibility="collapsed"
                        )
                        if new_s != score:
                            if st.button("✏️ Fix", key=f"fix_{att['id']}",
                                         use_container_width=True):
                                sb.table("attempts").update({
                                    "score":           new_s,
                                    "is_correct":      new_s >= max_m * 0.7,
                                    "claude_feedback": f"Score corrected by parent: {new_s}/{max_m}",
                                }).eq("id", att["id"]).execute()
                                st.success("Updated")
                                st.rerun()

                    with c_col:
                        st.write("")   # spacing

                # ── Reassign button (uses latest attempt's assignment) ──
                assign_res = (sb.table("assignments")
                                .select("id, status, round")
                                .eq("question_id", qid)
                                .eq("student_name", student)
                                .order("round", desc=True)
                                .limit(1)
                                .execute())

                if assign_res.data:
                    asgn       = assign_res.data[0]
                    curr_rnd   = asgn.get("round", 1)
                    curr_status = asgn.get("status", "completed")

                    if curr_status == "completed":
                        if st.button(
                            f"🔄 Reassign to {student} (Attempt #{curr_rnd + 1})",
                            key=f"reassign_{student}_{qid}",
                            use_container_width=True
                        ):
                            sb.table("assignments").update({
                                "status": "pending",
                                "round":  curr_rnd + 1,
                            }).eq("id", asgn["id"]).execute()
                            st.success(f"Reassigned — {student} will see this as Attempt #{curr_rnd + 1}")
                            st.rerun()
                    else:
                        st.info("⏳ Pending — student hasn't submitted yet")

                st.divider()

        st.write("")


# ─────────────────────────────────────────────────────────────────────────────
# STUDENT MODE PAGES
# ─────────────────────────────────────────────────────────────────────────────

def student_dashboard(sb, student: str):
    st.title(f"🏠 {student} — My Dashboard")
    week_ago = (date.today() - timedelta(days=7)).isoformat()

    # Stats
    all_att  = sb.table("attempts").select("score,max_score").eq("student_name", student).execute()
    week_att = (sb.table("attempts")
                  .select("score,max_score")
                  .eq("student_name", student)
                  .gte("attempt_date", week_ago)
                  .execute())

    c1, c2, c3 = st.columns(3)
    c1.metric("Total questions done", len(all_att.data))
    c2.metric("This week", len(week_att.data))
    if week_att.data:
        ws = sum(a["score"] for a in week_att.data)
        wm = sum((a["max_score"] or 1) for a in week_att.data)
        c3.metric("This week score", f"{ws/wm*100:.0f}%")
    else:
        c3.metric("This week score", "—")

    # Topic heatmap
    df = topic_scores_for(sb, student, "Maths")
    st.plotly_chart(heatmap_fig(df, "My topic scores (Maths)"), use_container_width=True)

    # Pending assignments — use status column
    assigned = (sb.table("assignments")
                  .select("id, question_id, assigned_date, status, "
                          "questions(brief_description, difficulty, marks, "
                          "papers(subjects(name)), topics(name))")
                  .eq("student_name", student)
                  .eq("status", "pending")
                  .execute())

    pending = assigned.data
    st.subheader(f"📋 Pending ({len(pending)})")
    if not pending:
        st.success("All caught up! 🎉")
    else:
        for a in pending[:10]:
            q    = a.get("questions") or {}
            subj = ((q.get("papers") or {}).get("subjects") or {}).get("name", "?")
            rnd  = a.get("round", 1)
            rnd_label = f" · Attempt {rnd}" if rnd > 1 else ""
            st.markdown(
                f"- **{subj}** · {(q.get('topics') or {}).get('name','?')} · "
                f"{q.get('brief_description','')[:60]} · "
                f"*{q.get('difficulty','?')}* · {q.get('marks',1)} marks{rnd_label}"
            )


def save_working_image(student: str, question_id: int, image_data) -> str:
    """Save canvas to Supabase Storage, return public URL."""
    img = Image.fromarray(image_data.astype("uint8"), "RGBA")
    bg  = Image.new("RGB", img.size, (255, 255, 255))
    bg.paste(img, mask=img.split()[3])
    buf = io.BytesIO()
    bg.save(buf, format="PNG")
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"workings/{student}/q{question_id}_{ts}.png"
    try:
        get_supabase().storage.from_(STORAGE_BUCKET).upload(
            path=path, file=buf.getvalue(),
            file_options={"content-type": "image/png", "upsert": "true"},
        )
        return get_supabase().storage.from_(STORAGE_BUCKET).get_public_url(path)
    except Exception:
        return ""


def preprocess_canvas(image_data) -> tuple[bytes, str]:
    """
    Convert canvas RGBA → clean high-contrast PNG for Claude OCR.
    Returns (png_bytes, base64_string).
    """
    from PIL import ImageEnhance, ImageFilter

    img = Image.fromarray(image_data.astype("uint8"), "RGBA")
    bg  = Image.new("RGB", img.size, (255, 255, 255))
    bg.paste(img, mask=img.split()[3])

    # Scale up 2× so Claude can read small handwriting clearly
    w, h   = bg.size
    big    = bg.resize((w * 2, h * 2), Image.LANCZOS)

    # Boost contrast so ink is pure black on white
    big = ImageEnhance.Contrast(big).enhance(2.5)
    big = ImageEnhance.Sharpness(big).enhance(2.0)

    buf = io.BytesIO()
    big.save(buf, format="PNG")
    png_bytes = buf.getvalue()
    return png_bytes, base64.b64encode(png_bytes).decode()


def student_practice(sb, student: str):
    st.title("\U0001f4dd Practice Questions")

    # Only show pending assignments (status='pending')
    assigned = (sb.table("assignments")
                  .select("id, question_id, round, "
                          "questions(id, question_number, brief_description, "
                          "difficulty, marks, image_url, answer_guide, mark_scheme_text, "
                          "papers(filename, paper_type, subjects(name)), "
                          "topics(name, topic_areas(name)))")
                  .eq("student_name", student)
                  .eq("status", "pending")
                  .execute())

    pool = [(a["id"], a.get("round", 1), a["questions"])
            for a in assigned.data if a.get("questions")]

    total_done = (sb.table("attempts").select("id", count="exact")
                   .eq("student_name", student).execute()).count or 0

    if not pool:
        st.success("\U0001f389 No pending questions! Ask your parent to assign more.")
        return

    subjects_in = list({(item[2].get("papers") or {}).get("subjects", {}).get("name", "?")
                        for item in pool})
    chosen = st.selectbox("Subject", ["All"] + sorted(subjects_in))
    if chosen != "All":
        pool = [item for item in pool
                if (item[2].get("papers") or {}).get("subjects", {}).get("name") == chosen]
    if not pool:
        st.info("No pending questions for that subject.")
        return

    for key, default in [("practice_idx", 0), ("canvas_reset", 0),
                          ("draw_mode", "pen"), ("submitted_id", None), ("last_feedback", None)]:
        if key not in st.session_state:
            st.session_state[key] = default

    idx          = st.session_state.practice_idx % len(pool)
    assign_id, rnd, q = pool[idx]
    subj  = (q.get("papers") or {}).get("subjects", {}).get("name", "?")
    topic = (q.get("topics") or {}).get("name", "?")
    area  = ((q.get("topics") or {}).get("topic_areas") or {}).get("name", "?")
    diff  = q.get("difficulty", "Silver")
    badge = {"Bronze": "\U0001f949", "Silver": "\U0001f948", "Gold": "\U0001f947"}.get(diff, "\u2022")

    total = len(pool) + total_done
    st.progress(total_done / total if total else 0,
                text=f"{total_done}/{total} questions done")

    rnd_label = f" · 🔄 Attempt {rnd}" if rnd > 1 else ""
    st.markdown(f"### Q{q['question_number']} · {subj}  \n{badge} **{diff}** · {area} > {topic} · **{q['marks']} marks**{rnd_label}")

    st.divider()

    # ── Toolbar ───────────────────────────────────────────────────────────
    tc1, tc2, tc3, tc4 = st.columns([1, 1, 1, 3])
    with tc1:
        if st.button("🖊️ Pen", use_container_width=True,
                     type="primary" if st.session_state.draw_mode == "pen" else "secondary",
                     key="btn_pen"):
            st.session_state.draw_mode = "pen"; st.rerun()
    with tc2:
        if st.button("⬜ Erase", use_container_width=True,
                     type="primary" if st.session_state.draw_mode == "erase" else "secondary",
                     key="btn_erase"):
            st.session_state.draw_mode = "erase"; st.rerun()
    with tc3:
        if st.button("🗑️ Clear", use_container_width=True, key="btn_clear"):
            st.session_state.canvas_reset += 1; st.rerun()
    with tc4:
        stroke_size = st.slider("Size", 1, 20,
                                15 if st.session_state.draw_mode == "erase" else 3,
                                key="stroke_slider", label_visibility="collapsed")

    canvas_data   = None
    typed_working = ""

    # ── Side-by-side: question LEFT · canvas RIGHT ────────────────────────
    col_q, col_w = st.columns([2, 3], gap="medium")

    canvas_data   = None
    typed_working = ""
    photo_file    = None

    with col_q:
        st.markdown("**📄 Question**")
        with st.container(height=580, border=True):
            if q.get("image_url"):
                st.image(q["image_url"], use_container_width=True)
            else:
                st.info(q.get("brief_description", ""))

    with col_w:
        st.markdown("**✏️ Your working**")
        tab_draw, tab_type, tab_photo = st.tabs(["✏️ Draw", "⌨️ Type", "📷 Upload photo"])

        with tab_draw:
            canvas_result = st_canvas(
                stroke_width     = stroke_size,
                stroke_color     = "#ffffff" if st.session_state.draw_mode == "erase" else "#c00000",
                background_color = "#ffffff",
                height           = 520,
                drawing_mode     = "freedraw",
                update_streamlit = True,
                key = f"canvas_{q['id']}_{st.session_state.canvas_reset}",
            )
            if canvas_result.image_data is not None:
                canvas_data = canvas_result.image_data

        with tab_type:
            typed_working = st.text_area("Steps", height=440,
                placeholder="e.g.\n2x + 3 = 11\n2x = 8\nx = 4",
                key=f"typed_{q['id']}", label_visibility="collapsed")

        with tab_photo:
            st.caption("Write on paper, take a photo, upload it here")
            photo_file = st.file_uploader(
                "Choose photo",
                type=["jpg", "jpeg", "png"],
                key=f"photo_{q['id']}",
                label_visibility="collapsed",
            )
            if photo_file:
                st.image(photo_file, caption="Your working", use_container_width=True)

    # ── Final answer ──────────────────────────────────────────────────────
    st.divider()
    st.markdown("### ✏️ Final Answer")
    st.info("💡 Always type your final answer here — Claude cross-checks it against the mark scheme.")
    final_ans = st.text_input(
        "Type your final answer",
        placeholder="e.g.  x = 4   or   180   or   13.2 cm",
        key=f"fin_{q['id']}",
    )

    c1, c2 = st.columns(2)
    with c1:
        submit = st.button("\u2705 Submit", type="primary", use_container_width=True)
    with c2:
        if st.button("\u23ed\ufe0f Skip", use_container_width=True):
            st.session_state.practice_idx += 1
            st.session_state.canvas_reset += 1
            st.session_state.last_feedback = None
            st.rerun()

    if st.session_state.submitted_id == q["id"] and st.session_state.last_feedback:
        fb = st.session_state.last_feedback
        if fb["is_correct"]:
            st.success(f"\u2705  {fb['score']}/{fb['max_s']} \u2014 {fb['comment']}")
        else:
            st.warning(f"\u274c  {fb['score']}/{fb['max_s']} \u2014 {fb['comment']}")
        if fb.get("model_answer"):
            with st.expander("\U0001f4d6 Model answer"):
                st.markdown(fb["model_answer"])
        if st.button("\u27a1\ufe0f Next question", type="primary", use_container_width=True):
            st.session_state.practice_idx += 1
            st.session_state.canvas_reset += 1
            st.session_state.last_feedback = None
            st.session_state.submitted_id  = None
            st.rerun()
        return

    if submit:
        canvas_used = canvas_data is not None and int(canvas_data.sum()) > 0
        photo_used  = photo_file is not None

        if not canvas_used and not photo_used and not typed_working.strip() and not final_ans.strip():
            st.warning("Add your working — draw, type, or upload a photo.")
            return

        combined = ""
        if typed_working.strip(): combined += f"Working:\n{typed_working.strip()}\n\n"
        if final_ans.strip():     combined += f"Final answer: {final_ans.strip()}"
        ms_text = q.get("mark_scheme_text") or ""

        with st.spinner("Marking …"):
            if photo_used:
                photo_bytes = photo_file.read()
                mime        = "image/jpeg" if photo_file.name.lower().endswith((".jpg",".jpeg")) else "image/png"
                feedback    = mark_answer_photo(q, photo_bytes, mime, final_ans, ms_text)
                working_url = save_working_photo(student, q["id"], photo_bytes, mime)
            elif canvas_used:
                feedback    = mark_answer_canvas(q, canvas_data, final_ans, ms_text)
                working_url = save_working_image(student, q["id"], canvas_data)
            else:
                feedback    = mark_answer(q, combined, ms_text)
                working_url = ""

        score   = feedback["score"]
        max_s   = q["marks"]
        correct = score >= max_s * 0.7

        sb.table("attempts").insert({
            "question_id": q["id"], "student_name": student,
            "student_answer": final_ans or typed_working or "(photo/canvas)",
            "score": score, "max_score": max_s, "is_correct": correct,
            "claude_feedback": feedback["comment"],
            "working_image_url": working_url,
            "round": rnd,
        }).execute()

        sb.table("assignments").update({"status": "completed"}).eq("id", assign_id).execute()

        st.session_state.submitted_id  = q["id"]
        st.session_state.last_feedback = {
            "score": score, "max_s": max_s, "is_correct": correct,
            "comment": feedback["comment"], "model_answer": feedback.get("model_answer", "")
        }
        st.rerun()


def save_working_photo(student: str, question_id: int,
                       photo_bytes: bytes, mime: str) -> str:
    """Save uploaded photo to Supabase Storage, return public URL."""
    ext  = "jpg" if "jpeg" in mime else "png"
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"workings/{student}/q{question_id}_{ts}.{ext}"
    try:
        get_supabase().storage.from_(STORAGE_BUCKET).upload(
            path=path, file=photo_bytes,
            file_options={"content-type": mime, "upsert": "true"},
        )
        return get_supabase().storage.from_(STORAGE_BUCKET).get_public_url(path)
    except Exception:
        return ""


def mark_answer_photo(q: dict, photo_bytes: bytes, mime: str,
                      final_answer: str, ms_text: str = "") -> dict:
    """Mark a photo of handwritten working."""
    max_marks  = q.get("marks", 1)
    img_b64    = base64.b64encode(photo_bytes).decode()
    ms_section = f"\nOfficial mark scheme:\n{ms_text}\n" if ms_text else "\nNo mark scheme — use your knowledge.\n"

    content = [
        {
            "type": "text",
            "text": f"""You are a GCSE examiner marking a photo of handwritten student working.

IMPORTANT handwriting reading notes:
- A diagonal stroke "/" is almost always the number "1" not a slash
- Read numbers carefully before any arithmetic checks

Question: {q.get('brief_description', '')}
Subject: {(q.get('papers') or {}).get('subjects', {}).get('name', 'Maths')}
Max marks: {max_marks}
Student's typed final answer: {final_answer or '(see working in photo)'}
{ms_section}
Award M marks for correct method, A marks for correct answer, B marks for independent statements.
Give partial credit proportionally for multi-mark questions.

Return JSON only:
{{
  "score": <integer 0 to {max_marks}>,
  "comment": "<1-2 sentences: specific feedback on method and answer>",
  "model_answer": "<full worked solution if less than full marks, else empty>"
}}""",
        },
        {
            "type": "image",
            "source": {"type": "base64", "media_type": mime, "data": img_b64},
        },
    ]

    resp = get_ai().messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{"role": "user", "content": content}],
    )
    return _parse_mark_json(resp.content[0].text.strip())


def _parse_mark_json(raw: str) -> dict:
    raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\n?```$", "", raw, flags=re.MULTILINE)
    try:
        return json.loads(raw.strip())
    except Exception:
        return {"score": 0, "comment": raw[:200], "model_answer": ""}


def mark_answer_canvas(q: dict, image_data, final_answer: str,
                       ms_text: str = "") -> dict:
    """Mark handwritten working from canvas image."""
    max_marks = q.get("marks", 1)

    # Preprocess for better OCR — 2× scale, high contrast
    _, img_b64 = preprocess_canvas(image_data)

    ms_section = (f"\nOfficial mark scheme:\n{ms_text}\n"
                  if ms_text else "\nNo mark scheme available — use your knowledge.\n")

    content = [
        {
            "type": "text",
            "text": f"""You are a GCSE examiner marking handwritten student working.

IMPORTANT handwriting reading notes:
- A diagonal stroke "/" is almost always the number "1" not a slash
- "1" is often written with serifs or diagonal starts — read generously
- If a number could be 1 or 7, prefer 1 unless context says otherwise
- Read numbers carefully before doing any arithmetic checks
- Do NOT override what is written unless you are certain it is wrong

Question: {q.get('brief_description', '')}
Subject: {(q.get('papers') or {}).get('subjects', {}).get('name', 'Maths')}
Max marks: {max_marks}
{ms_section}
STUDENT'S TYPED FINAL ANSWER: "{final_answer or 'not provided'}"

Marking steps — follow in order:
1. Read the handwritten working in the image carefully
2. Check if the METHOD is correct → award M marks
3. Cross-check the TYPED FINAL ANSWER against the correct answer in the mark scheme
   - If typed answer is correct → award A mark (accuracy mark)
   - If typed answer is wrong or missing → check handwritten answer in image
   - Only withhold A mark if the final answer is genuinely wrong
4. Award B marks for any independent correct statements

Return JSON only:
{{
  "score": <integer 0 to {max_marks}>,
  "comment": "<1-2 sentences: specific feedback — state what the final answer was and whether method/answer were correct>",
  "model_answer": "<full worked solution if student got less than full marks, else empty>"
}}""",
        },
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": img_b64},
        },
    ]

    resp = get_ai().messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{"role": "user", "content": content}],
    )
    return _parse_mark_json(resp.content[0].text.strip())


def mark_answer(q: dict, student_answer: str, ms_text: str = "") -> dict:
    """Mark a typed answer with optional mark scheme."""
    max_marks = q.get("marks", 1)
    ms_section = (f"\nOfficial mark scheme:\n{ms_text}\n"
                  if ms_text else "\nNo mark scheme — use your knowledge.\n")

    # Extract final answer line if present
    final_line = ""
    for line in student_answer.split("\n"):
        if line.lower().startswith("final answer"):
            final_line = line.split(":", 1)[-1].strip()

    prompt = f"""You are a GCSE examiner marking a student's typed answer.

Question: {q.get('brief_description', '')}
Subject: {(q.get('papers') or {}).get('subjects', {}).get('name', 'Maths')}
Max marks: {max_marks}
{ms_section}
Student's response:
{student_answer}

{f'Student typed final answer: "{final_line}"' if final_line else ''}

Marking steps:
1. Check if the METHOD/WORKING is correct → award M marks
2. Cross-check the FINAL ANSWER against the correct answer in the mark scheme
   - If final answer matches → award A mark
   - If final answer is wrong but method is correct → M marks only
3. Award B marks for independent correct statements
4. Give partial credit proportionally for multi-mark questions
5. 1-mark questions: correct or not, no partial credit

Return JSON only:
{{
  "score": <integer 0 to {max_marks}>,
  "comment": "<1-2 sentences: state whether method and final answer were correct>",
  "model_answer": "<full worked solution if student got less than full marks, else empty>"
}}"""

    resp = get_ai().messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_mark_json(resp.content[0].text.strip())


def student_progress(sb, student: str):
    st.title(f"📈 {student} — My Progress")

    all_att = (sb.table("attempts")
                 .select("score, max_score, attempt_date, "
                         "questions(difficulty, topics(name, topic_areas(name)))")
                 .eq("student_name", student)
                 .order("attempt_date")
                 .execute())

    if not all_att.data:
        st.info("No attempts yet — start practising!")
        return

    # Build dataframe
    rows = []
    for a in all_att.data:
        q    = a.get("questions") or {}
        t    = q.get("topics") or {}
        area = (t.get("topic_areas") or {}).get("name", "?")
        rows.append({
            "date":       a["attempt_date"][:10],
            "topic_area": area,
            "topic":      t.get("name", "?"),
            "difficulty": q.get("difficulty", "?"),
            "score":      a["score"],
            "max_score":  a["max_score"] or 1,
            "pct":        a["score"] / (a["max_score"] or 1) * 100,
        })
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])

    # Weekly trend line
    weekly = (df.set_index("date")
                .resample("W")["pct"]
                .mean()
                .reset_index())
    fig_trend = px.line(weekly, x="date", y="pct",
                        title="Weekly average score",
                        labels={"pct": "Avg %", "date": "Week"})
    fig_trend.update_traces(line_color="#1a3c6e", line_width=2)
    fig_trend.update_layout(yaxis_range=[0, 105])
    st.plotly_chart(fig_trend, use_container_width=True)

    # Topic breakdown
    topic_df = (df.groupby(["topic_area", "topic"])
                  .agg(avg_pct=("pct", "mean"), attempts=("pct", "count"))
                  .reset_index()
                  .sort_values("avg_pct"))
    st.plotly_chart(heatmap_fig(topic_df, "Score by topic"), use_container_width=True)

    # Difficulty breakdown
    diff_df = (df.groupby("difficulty")
                 .agg(avg_pct=("pct", "mean"), attempts=("pct", "count"))
                 .reset_index())
    fig_diff = px.bar(diff_df, x="difficulty", y="avg_pct",
                      color="difficulty",
                      color_discrete_map=DIFFICULTY_COLOURS,
                      title="Score by difficulty",
                      text_auto=".0f")
    fig_diff.update_layout(yaxis_range=[0, 115], showlegend=False)
    st.plotly_chart(fig_diff, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# ROUTING
# ─────────────────────────────────────────────────────────────────────────────

def parent_app(sb):
    with st.sidebar:
        st.title("📚 GCSE Tracker")
        st.caption("Parent mode")
        page = st.radio("", [
            "📊 Dashboard",
            "⚙️ Process Papers",
            "📚 Question Bank",
            "📅 Weekly Report",
        ], label_visibility="collapsed")
        st.divider()
        if st.button("🚪 Log out"):
            for k in ["mode", "student"]:
                st.session_state.pop(k, None)
            st.rerun()

    if page == "📊 Dashboard":       page_dashboard(sb)
    elif page == "⚙️ Process Papers": page_process_papers(sb)
    elif page == "📚 Question Bank":  page_question_bank(sb)
    elif page == "📅 Weekly Report":  page_weekly_report(sb)


def student_app(sb, student: str):
    with st.sidebar:
        st.title("📚 GCSE Tracker")
        st.caption(f"Student: **{student}**")
        page = st.radio("", [
            "🏠 Dashboard",
            "📝 Practice",
            "📈 My Progress",
        ], label_visibility="collapsed")
        st.divider()
        if st.button("🚪 Log out"):
            for k in ["mode", "student"]:
                st.session_state.pop(k, None)
            st.rerun()

    if page == "🏠 Dashboard":       student_dashboard(sb, student)
    elif page == "📝 Practice":      student_practice(sb, student)
    elif page == "📈 My Progress":   student_progress(sb, student)


def main():
    if "mode" not in st.session_state:
        login_screen()
        return

    sb = get_supabase()
    if st.session_state.mode == "parent":
        parent_app(sb)
    else:
        student_app(sb, st.session_state.get("student", "M1"))


if __name__ == "__main__":
    main()
