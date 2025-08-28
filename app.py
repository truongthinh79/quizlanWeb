# -*- coding: utf-8 -*-
"""
QuizLAN - single-file Flask app (student-home + admin/login)
- Student-only homepage: choose class -> name -> enter quiz code -> take quiz
- Admin login at /admin/login (hidden from student homepage)
- Admin can upload students.xlsx/csv, manage quizzes/questions, view/export results & logs
- Anti-cheat logging (blur events) saved to data/logs.json
- Import .docx for questions, JSON storage, auto-create templates/static
"""
import os
import re
import json
import uuid
import secrets
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from io import BytesIO
from werkzeug.utils import secure_filename

from flask import (
    Flask, render_template, request, redirect, url_for, flash, session,
    jsonify, abort, send_file
)

# Optional libs
try:
    from docx import Document
except Exception:
    Document = None

try:
    import pandas as pd
except Exception:
    pd = None

# ---------------- Config & Paths ----------------
APP_TITLE = "QuizLAN"
DEFAULT_ADMIN_PASSWORD = "admin123"
SECRET_KEY = os.environ.get("QUIZLAN_SECRET_KEY", secrets.token_hex(16))

BASE_DIR = Path(".")
UPLOAD_DIR = BASE_DIR / "uploads"
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
STATIC_UPLOADS_DIR = STATIC_DIR / "uploads"

QUIZZES_FILE = DATA_DIR / "quizzes.json"
QUESTIONS_FILE = DATA_DIR / "questions.json"
STUDENTS_FILE = DATA_DIR / "students.json"      # list of {"id","name","class"}
SUBMISSIONS_FILE = DATA_DIR / "submissions.json"
ANSWERS_FILE = DATA_DIR / "answers.json"
LOGS_FILE = DATA_DIR / "logs.json"
CONFIG_FILE = DATA_DIR / "config.json"

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=2)

# ---------------- Utilities ----------------

def ensure_dirs_and_assets():
    UPLOAD_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)
    TEMPLATES_DIR.mkdir(exist_ok=True)
    STATIC_DIR.mkdir(exist_ok=True)
    STATIC_UPLOADS_DIR.mkdir(exist_ok=True)
    # ensure default json files
    if not QUIZZES_FILE.exists():
        save_data(QUIZZES_FILE, [])
    if not QUESTIONS_FILE.exists():
        save_data(QUESTIONS_FILE, {})
    if not STUDENTS_FILE.exists():
        save_data(STUDENTS_FILE, [])
    if not SUBMISSIONS_FILE.exists():
        save_data(SUBMISSIONS_FILE, [])
    if not ANSWERS_FILE.exists():
        save_data(ANSWERS_FILE, {})
    if not LOGS_FILE.exists():
        save_data(LOGS_FILE, {})
    if not CONFIG_FILE.exists():
        save_data(CONFIG_FILE, {"ADMIN_PASSWORD": DEFAULT_ADMIN_PASSWORD})
    # templates & static
    files = {
        TEMPLATES_DIR / "base.html": BASE_HTML,
        TEMPLATES_DIR / "index.html": INDEX_HTML,
        TEMPLATES_DIR / "quiz.html": QUIZ_HTML,
        TEMPLATES_DIR / "admin_login.html": ADMIN_LOGIN_HTML,
        TEMPLATES_DIR / "admin.html": ADMIN_HTML,
        TEMPLATES_DIR / "results.html": RESULTS_HTML,
        TEMPLATES_DIR / "logs.html": LOGS_HTML,
        STATIC_DIR / "app.js": APP_JS,
        STATIC_DIR / "style.css": APP_CSS,
    }
    for path, content in files.items():
        if not path.exists():
            path.write_text(content, encoding="utf-8")

def load_data(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default if default is not None else None

def save_data(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_quizzes():
    return load_data(QUIZZES_FILE, [])

def save_quizzes(qs):
    save_data(QUIZZES_FILE, qs)

def get_questions():
    return load_data(QUESTIONS_FILE, {})

def save_questions(qs):
    save_data(QUESTIONS_FILE, qs)

def get_students():
    return load_data(STUDENTS_FILE, [])

def save_students(lst):
    save_data(STUDENTS_FILE, lst)

def get_submissions():
    return load_data(SUBMISSIONS_FILE, [])

def save_submissions(s):
    save_data(SUBMISSIONS_FILE, s)

def get_answers():
    return load_data(ANSWERS_FILE, {})

def save_answers(a):
    save_data(ANSWERS_FILE, a)

def get_logs():
    return load_data(LOGS_FILE, {})

def save_logs(l):
    save_data(LOGS_FILE, l)

def get_config():
    return load_data(CONFIG_FILE, {"ADMIN_PASSWORD": DEFAULT_ADMIN_PASSWORD})

def save_config(c):
    save_data(CONFIG_FILE, c)

def get_admin_password():
    cfg = get_config()
    return cfg.get("ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD)

# ---------------- Parser for .docx (flexible) ----------------

def parse_docx_questions(docx_source):
    if Document is None:
        raise RuntimeError("python-docx chưa được cài. Hãy pip install python-docx")
    doc = Document(docx_source)
    blocks = []
    buf = []
    for para in doc.paragraphs:
        line = (para.text or "").strip()
        if line:
            line = line.replace("：", ":")
            line = re.sub(r"\s+", " ", line)
        if not line:
            if buf:
                blocks.append("\n".join(buf).strip())
                buf = []
            continue
        buf.append(line)
    if buf:
        blocks.append("\n".join(buf).strip())

    questions = []
    for blk in blocks:
        lines = [ln.strip() for ln in blk.splitlines() if ln.strip()]
        if not lines:
            continue
        if not re.match(r"^(q|câu\s*hỏi)\s*[:.\-]?", lines[0], flags=re.I):
            continue
        qtext = re.sub(r"^(q|câu\s*hỏi)\s*[:.\-]?\s*", "", lines[0], flags=re.I).strip()
        opts = []
        answer_line = None
        for l in lines[1:]:
            if re.match(r"^(answer|ans|đáp\s*án)\b", l, flags=re.I):
                if ":" in l:
                    answer_line = l.split(":",1)[1].strip()
                else:
                    answer_line = re.sub(r"^(answer|ans|đáp\s*án)\b\s*", "", l, flags=re.I).strip()
                continue
            m = re.match(r"^([A-Za-z])\s*[\)\.\-:]\s*(.+)$", l)
            if m:
                label = m.group(1).upper()
                text = m.group(2).strip()
                opts.append({"label": label, "text": text, "is_correct": 0})
                continue
            m2 = re.match(r"^([A-Za-z])\s+(.+)$", l)
            if m2 and len(l) > 3:
                label = m2.group(1).upper()
                text = m2.group(2).strip()
                opts.append({"label": label, "text": text, "is_correct": 0})
                continue
        if len(opts) < 2:
            continue
        correct = []
        if answer_line:
            correct = [x.strip().upper() for x in re.split(r"[,; ]+", answer_line) if x.strip()]
        if not correct:
            continue
        multi = 1 if len(correct) > 1 else 0
        for o in opts:
            if o["label"] in correct:
                o["is_correct"] = 1
        for idx, o in enumerate(opts):
            o["label"] = chr(65 + idx)
        questions.append({"text": qtext, "multi": multi, "options": opts})
    return questions

# ---------------- Helper: students by class ----------------

def students_by_class():
    ss = get_students()
    classes = {}
    for s in ss:
        cls = s.get("class","Chưa phân lớp") or "Chưa phân lớp"
        if cls not in classes:
            classes[cls] = []
        classes[cls].append({"id": s["id"], "name": s["name"]})
    return classes

# ---------------- Public routes ----------------

@app.route("/")
def index():
    # student-only homepage: select class -> name -> join
    classes = sorted(list(students_by_class().keys()))
    return render_template("index.html", app_title=APP_TITLE, classes=classes)

@app.get("/api/classes")
def api_classes():
    # return mapping class -> list of names
    classes = students_by_class()
    return jsonify(classes)

@app.post("/api/register")
def api_register():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    cls = (data.get("class") or "").strip()
    if not name or not cls:
        return jsonify({"error":"Thiếu tên hoặc lớp"}), 400
    # try find an existing student id by name+class, else create
    students = get_students()
    student = next((s for s in students if s["name"]==name and s.get("class","")==cls), None)
    if not student:
        user_id = str(uuid.uuid4())
        students.append({"id": user_id, "name": name, "class": cls, "created_at": datetime.utcnow().isoformat()})
        save_students(students)
        student_id = user_id
    else:
        student_id = student["id"]
    session["student_id"] = student_id
    session["name"] = name
    session["class"] = cls
    session.permanent = True
    return jsonify({"ok": True, "student_id": student_id, "message":"Đã lưu thông tin học sinh"})

@app.post("/api/check_code")
def api_check_code():
    data = request.get_json(force=True)
    code = (data.get("access_code") or "").strip()
    if not code:
        return jsonify({"error":"Thiếu mã truy cập"}), 400
    quizzes = get_quizzes()
    quiz = next((q for q in quizzes if q["access_code"]==code and q.get("is_active", True)), None)
    if not quiz:
        return jsonify({"error":"Mã truy cập không hợp lệ hoặc kỳ thi đã đóng"}), 404
    return jsonify({"ok": True, "quiz_id": quiz["id"]})

@app.post("/api/join")
def api_join():
    data = request.get_json(force=True)
    code = (data.get("access_code") or "").strip()
    if not code:
        return jsonify({"error":"Thiếu mã truy cập"}), 400
    quizzes = get_quizzes()
    quiz = next((q for q in quizzes if q["access_code"]==code and q.get("is_active", True)), None)
    if not quiz:
        return jsonify({"error":"Mã truy cập không hợp lệ hoặc kỳ thi đã đóng"}), 404
    student_id = session.get("student_id")
    if not student_id:
        return jsonify({"error":"Bạn chưa đăng ký (chọn lớp & tên)"}), 401
    submissions = get_submissions()
    sub = next((s for s in submissions if s["student_id"]==student_id and s["quiz_id"]==quiz["id"]), None)
    if sub:
        sub_id = sub["id"]
    else:
        sub_id = str(uuid.uuid4())
        submissions.append({
            "id": sub_id,
            "student_id": student_id,
            "quiz_id": quiz["id"],
            "started_at": datetime.utcnow().isoformat(),
            "finished_at": None,
            "score": None,
            "total": None
        })
        save_submissions(submissions)
    return jsonify({"ok": True, "quiz_id": quiz["id"], "submission_id": sub_id})

@app.get("/quiz/<quiz_id>")
def quiz_page(quiz_id):
    quizzes = get_quizzes()
    quiz = next((q for q in quizzes if q["id"]==quiz_id), None)
    if not quiz:
        abort(404)
    student_name = session.get("name","")
    return render_template("quiz.html", quiz_id=quiz_id, title=quiz["title"], duration=quiz["duration_seconds"], app_title=APP_TITLE, student_name=student_name)

@app.get("/api/quiz/<quiz_id>")
def api_quiz(quiz_id):
    quizzes = get_quizzes()
    quiz = next((q for q in quizzes if q["id"]==quiz_id), None)
    if not quiz:
        return jsonify({"error":"Quiz không tồn tại"}), 404
    questions_data = get_questions()
    qs = questions_data.get(quiz_id, [])
    if not qs:
        return jsonify({"error":"Chưa có câu hỏi cho kỳ thi này. Vui lòng import/cấu hình trong admin."}), 404
    import random
    qs_copy = [json.loads(json.dumps(q)) for q in qs]
    random.shuffle(qs_copy)
    for q in qs_copy:
        random.shuffle(q["options"])
    questions = []
    for q in qs_copy:
        opt_list = [{"label": o["label"], "text": o["text"], "image": o.get("image")} for o in q["options"]]
        questions.append({
            "id": q["id"],
            "text": q["text"],
            "image": q.get("image"),
            "multi": bool(q.get("multi", 0)),
            "options": opt_list
        })
    return jsonify({
        "title": quiz["title"],
        "duration_seconds": quiz["duration_seconds"],
        "questions": questions
    })

@app.post("/api/submit/<quiz_id>")
def api_submit(quiz_id):
    data = request.get_json(force=True)
    answers_map = data.get("answers") or {}
    student_id = session.get("student_id")
    if not student_id:
        return jsonify({"error":"Chưa đăng ký (chọn lớp & tên)" }), 401
    submissions = get_submissions()
    sub = next((s for s in submissions if s["student_id"]==student_id and s["quiz_id"]==quiz_id), None)
    if not sub:
        return jsonify({"error":"Chưa tham gia kỳ thi"}), 400
    questions_data = get_questions()
    qs = questions_data.get(quiz_id, [])
    total = len(qs)
    score = 0
    answers_data = get_answers()
    if sub["id"] not in answers_data:
        answers_data[sub["id"]] = []
    for q in qs:
        correct = sorted([o["label"] for o in q["options"] if o.get("is_correct")])
        sel = sorted(answers_map.get(str(q["id"]), []))
        if correct and sel == correct:
            score += 1
        answers_data[sub["id"]].append({
            "question_id": q["id"],
            "selected": sel
        })
    sub["finished_at"] = datetime.utcnow().isoformat()
    sub["score"] = score
    sub["total"] = total
    save_submissions(submissions)
    save_answers(answers_data)
    return jsonify({"ok": True, "score": score, "total": total})

@app.post("/log_event")
def log_event():
    data = request.get_json(force=True)
    quiz_id = data.get("quiz_id")
    student = data.get("student") or session.get("name") or "unknown"
    event = data.get("event", "blur")
    if not quiz_id:
        return jsonify({"error":"missing quiz_id"}), 400
    logs = get_logs()
    if quiz_id not in logs:
        logs[quiz_id] = {}
    if student not in logs[quiz_id]:
        logs[quiz_id][student] = []
    logs[quiz_id][student].append({"time": datetime.utcnow().isoformat(), "event": event})
    save_logs(logs)
    return jsonify({"ok": True})

# ---------------- Admin: login is at /admin/login ----------------

def require_admin():
    return session.get("admin", False)

@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    # dedicated admin login page
    if request.method == "POST":
        pwd = request.form.get("password","")
        if pwd == get_admin_password():
            session["admin"] = True
            flash("Đăng nhập thành công", "success")
            return redirect(url_for("admin_dashboard"))
        flash("Sai mật khẩu admin", "danger")
    return render_template("admin_login.html", app_title=APP_TITLE)

@app.route("/admin")
def admin_root():
    # redirect to login if not admin
    if not require_admin():
        return redirect(url_for("admin_login"))
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/dashboard", methods=["GET","POST"])
def admin_dashboard():
    if not require_admin():
        return redirect(url_for("admin_login"))
    quizzes = get_quizzes()
    quizzes = sorted(quizzes, key=lambda q: q.get("created_at",""), reverse=True)
    questions = get_questions()
    students = get_students()
    students = sorted(students, key=lambda s: (s.get("class", ""), s["name"]))
    classes = sorted(list(students_by_class().keys()))
    logs = get_logs()
    return render_template("admin.html", app_title=APP_TITLE, logged_in=True, quizzes=quizzes, questions=questions, students=students, classes=classes, logs=logs)

@app.get("/admin/logs")
def admin_logs():
    if not require_admin():
        abort(403)
    logs = get_logs()
    quizzes = get_quizzes()
    annotated = {}
    for q in quizzes:
        annotated[q["id"]] = {"meta": q, "logs": logs.get(q["id"], {})}
    return render_template("logs.html", annotated=annotated, app_title=APP_TITLE)

@app.post("/admin/change_password")
def admin_change_password():
    if not require_admin():
        abort(403)
    newpw = request.form.get("new_password","").strip()
    confirm = request.form.get("confirm_password","").strip()
    if not newpw or newpw != confirm:
        flash("Mật khẩu trống hoặc không khớp", "warning")
        return redirect(url_for("admin_dashboard"))
    cfg = get_config()
    cfg["ADMIN_PASSWORD"] = newpw
    save_config(cfg)
    flash("Đã đổi mật khẩu admin.", "success")
    return redirect(url_for("admin_dashboard"))

@app.get("/logout")
def logout():
    session.clear()
    flash("Đã đăng xuất.", "info")
    return redirect(url_for("admin_login"))

@app.post("/admin/create")
def admin_create():
    if not require_admin():
        abort(403)
    title = request.form.get("title","").strip()
    duration_minutes = int(request.form.get("duration","0") or 0)
    access_code = request.form.get("access_code", str(uuid.uuid4())[:6]).strip()
    if not title or duration_minutes <= 0:
        flash("Thiếu tiêu đề hoặc thời lượng hợp lệ", "warning")
        return redirect(url_for("admin_dashboard"))
    quizzes = get_quizzes()
    quiz_id = str(uuid.uuid4())
    quizzes.append({
        "id": quiz_id,
        "title": title,
        "access_code": access_code,
        "duration_seconds": duration_minutes * 60,
        "is_active": True,
        "created_at": datetime.utcnow().isoformat()
    })
    save_quizzes(quizzes)
    questions = get_questions()
    if quiz_id not in questions:
        questions[quiz_id] = []
    save_questions(questions)
    flash(f"Đã tạo kỳ thi '{title}' (mã: {access_code}).", "success")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/update/<quiz_id>")
def admin_update(quiz_id):
    if not require_admin():
        abort(403)
    quizzes = get_quizzes()
    quiz = next((q for q in quizzes if q["id"]==quiz_id), None)
    if not quiz:
        flash("Kỳ thi không tồn tại", "warning")
        return redirect(url_for("admin_dashboard"))
    title = request.form.get("title", quiz["title"]).strip()
    duration_minutes = int(request.form.get("duration", max(1, quiz["duration_seconds"] // 60)) or (quiz["duration_seconds"] // 60))
    access_code = request.form.get("access_code", quiz["access_code"]).strip()
    if not title or duration_minutes <= 0:
        flash("Thiếu tiêu đề hoặc thời lượng hợp lệ", "warning")
        return redirect(url_for("admin_dashboard"))
    quiz["title"] = title
    quiz["duration_seconds"] = duration_minutes * 60
    quiz["access_code"] = access_code
    save_quizzes(quizzes)
    flash("Đã cập nhật kỳ thi.", "success")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/toggle/<quiz_id>")
def admin_toggle(quiz_id):
    if not require_admin():
        abort(403)
    quizzes = get_quizzes()
    for q in quizzes:
        if q["id"] == quiz_id:
            q["is_active"] = not q.get("is_active", True)
            break
    save_quizzes(quizzes)
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/delete/<quiz_id>")
def admin_delete(quiz_id):
    if not require_admin():
        abort(403)
    quizzes = [q for q in get_quizzes() if q["id"] != quiz_id]
    save_quizzes(quizzes)
    questions = get_questions()
    if isinstance(questions, dict) and quiz_id in questions:
        questions.pop(quiz_id)
    save_questions(questions)
    submissions = [s for s in get_submissions() if s["quiz_id"] != quiz_id]
    save_submissions(submissions)
    answers = get_answers()
    save_answers({k:v for k,v in answers.items() if any(s["id"]==k for s in submissions)})
    logs = get_logs()
    if isinstance(logs, dict) and quiz_id in logs:
        logs.pop(quiz_id)
    save_logs(logs)
    flash("Đã xóa kỳ thi và dữ liệu liên quan.", "info")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/import/<quiz_id>")
def admin_import(quiz_id):
    if not require_admin():
        abort(403)
    f = request.files.get("docx")
    if not f:
        flash("Chưa chọn file .docx", "warning")
        return redirect(url_for("admin_dashboard"))
    try:
        file_stream = BytesIO(f.read())
        parsed_questions = parse_docx_questions(file_stream)
        if not parsed_questions:
            flash("Không tìm thấy câu hỏi hợp lệ trong file. Vui lòng kiểm tra định dạng.", "warning")
            return redirect(url_for("admin_dashboard"))
        questions = get_questions()
        if quiz_id not in questions:
            questions[quiz_id] = []
        for pq in parsed_questions:
            q_id = str(uuid.uuid4())
            questions[quiz_id].append({
                "id": q_id,
                "text": pq["text"],
                "multi": pq["multi"],
                "options": pq["options"]
            })
        save_questions(questions)
        flash(f"Đã import {len(parsed_questions)} câu hỏi vào kỳ thi.", "success")
    except Exception as e:
        flash(f"Lỗi import: {e}", "danger")
    return redirect(url_for("admin_dashboard"))

# Questions CRUD
@app.post("/admin/questions/<quiz_id>/create")
def admin_q_create(quiz_id):
    if not require_admin():
        abort(403)
    q_text = (request.form.get("q_text") or "").strip()
    q_multi_flag = request.form.get("q_multi") == "on"
    labels = request.form.getlist("opt_label")
    texts = request.form.getlist("opt_text")
    corrects = request.form.getlist("opt_correct")
    opt_images = request.files.getlist("opt_image")
    q_image_file = request.files.get("q_image")
    q_image = None
    if q_image_file and q_image_file.filename:
        filename = secure_filename(str(uuid.uuid4()) + '_' + q_image_file.filename)
        q_image_file.save(STATIC_UPLOADS_DIR / filename)
        q_image = 'uploads/' + filename
    options = []
    for i in range(min(len(labels), len(texts))):
        lab = (labels[i] or "").strip()
        txt = (texts[i] or "").strip()
        if not txt:
            continue
        is_corr = False
        for c in corrects:
            if c.strip() and c.strip().upper() == (lab or "").upper():
                is_corr = True
        opt = {"label": (lab or "").upper(), "text": txt, "is_correct": 1 if is_corr else 0}
        if i < len(opt_images) and opt_images[i].filename:
            filename = secure_filename(str(uuid.uuid4()) + '_' + opt_images[i].filename)
            opt_images[i].save(STATIC_UPLOADS_DIR / filename)
            opt["image"] = 'uploads/' + filename
        options.append(opt)
    if len(options) < 2 or not q_text or not any(o["is_correct"] for o in options):
        flash("Thiếu nội dung câu hỏi/đáp án, hoặc chưa chọn đáp án đúng.", "warning")
        return redirect(url_for("admin_dashboard"))
    for idx, o in enumerate(options):
        o["label"] = chr(65 + idx)
    is_multi = 1 if q_multi_flag or sum(1 for o in options if o["is_correct"]) > 1 else 0
    questions = get_questions()
    if quiz_id not in questions:
        questions[quiz_id] = []
    questions[quiz_id].append({
        "id": str(uuid.uuid4()),
        "text": q_text,
        "image": q_image,
        "multi": is_multi,
        "options": options
    })
    save_questions(questions)
    flash("Đã thêm câu hỏi.", "success")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/questions/<quiz_id>/<q_id>/update")
def admin_q_update(quiz_id, q_id):
    if not require_admin():
        abort(403)
    q_text = (request.form.get("q_text") or "").strip()
    q_multi_flag = request.form.get("q_multi") == "on"
    labels = request.form.getlist("opt_label")
    texts = request.form.getlist("opt_text")
    corrects = request.form.getlist("opt_correct")
    opt_images = request.files.getlist("opt_image")
    q_image_file = request.files.get("q_image")
    questions = get_questions()
    qs = questions.get(quiz_id, [])
    q = next((x for x in qs if x["id"]==q_id), None)
    if not q:
        flash("Không tìm thấy câu hỏi", "warning")
        return redirect(url_for("admin_dashboard"))
    q_image = q.get("image")
    if q_image_file and q_image_file.filename:
        filename = secure_filename(str(uuid.uuid4()) + '_' + q_image_file.filename)
        q_image_file.save(STATIC_UPLOADS_DIR / filename)
        q_image = 'uploads/' + filename
    options = []
    for i in range(min(len(labels), len(texts))):
        lab = (labels[i] or "").strip()
        txt = (texts[i] or "").strip()
        if not txt:
            continue
        is_corr = False
        for c in corrects:
            if c.strip() and c.strip().upper() == (lab or "").upper():
                is_corr = True
        opt = {"label": (lab or "").upper(), "text": txt, "is_correct": 1 if is_corr else 0}
        if i < len(opt_images) and opt_images[i].filename:
            filename = secure_filename(str(uuid.uuid4()) + '_' + opt_images[i].filename)
            opt_images[i].save(STATIC_UPLOADS_DIR / filename)
            opt["image"] = 'uploads/' + filename
        options.append(opt)
    if len(options) < 2 or not q_text or not any(o["is_correct"] for o in options):
        flash("Thiếu nội dung câu hỏi/đáp án, hoặc chưa chọn đáp án đúng.", "warning")
        return redirect(url_for("admin_dashboard"))
    for idx, o in enumerate(options):
        o["label"] = chr(65 + idx)
    is_multi = 1 if q_multi_flag or sum(1 for o in options if o["is_correct"]) > 1 else 0
    q["text"] = q_text
    q["image"] = q_image
    q["multi"] = is_multi
    q["options"] = options
    save_questions(questions)
    flash("Đã cập nhật câu hỏi.", "success")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/questions/<quiz_id>/<q_id>/delete")
def admin_q_delete(quiz_id, q_id):
    if not require_admin():
        abort(403)
    questions = get_questions()
    qs = questions.get(quiz_id, [])
    new_qs = [x for x in qs if x["id"] != q_id]
    questions[quiz_id] = new_qs
    save_questions(questions)
    flash("Đã xóa câu hỏi.", "info")
    return redirect(url_for("admin_dashboard"))

# Students CRUD
@app.post("/admin/students/create")
def admin_s_create():
    if not require_admin():
        abort(403)
    name = (request.form.get("s_name") or "").strip()
    cls = (request.form.get("s_class") or "").strip()
    if not name or not cls:
        flash("Thiếu tên hoặc lớp", "warning")
        return redirect(url_for("admin_dashboard"))
    students = get_students()
    exists = next((s for s in students if s["name"]==name and s.get("class","")==cls), None)
    if exists:
        flash("Học sinh đã tồn tại trong lớp này", "warning")
        return redirect(url_for("admin_dashboard"))
    user_id = str(uuid.uuid4())
    students.append({"id": user_id, "name": name, "class": cls, "created_at": datetime.utcnow().isoformat()})
    save_students(students)
    flash("Đã thêm học sinh.", "success")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/students/<s_id>/update")
def admin_s_update(s_id):
    if not require_admin():
        abort(403)
    name = (request.form.get("s_name") or "").strip()
    cls = (request.form.get("s_class") or "").strip()
    if not name or not cls:
        flash("Thiếu tên hoặc lớp", "warning")
        return redirect(url_for("admin_dashboard"))
    students = get_students()
    s = next((x for x in students if x["id"]==s_id), None)
    if not s:
        flash("Không tìm thấy học sinh", "warning")
        return redirect(url_for("admin_dashboard"))
    s["name"] = name
    s["class"] = cls
    save_students(students)
    flash("Đã cập nhật học sinh.", "success")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/students/<s_id>/delete")
def admin_s_delete(s_id):
    if not require_admin():
        abort(403)
    students = [x for x in get_students() if x["id"] != s_id]
    save_students(students)
    flash("Đã xóa học sinh.", "info")
    return redirect(url_for("admin_dashboard"))

# Upload students from Excel (or CSV)
@app.post("/admin/upload_students")
def admin_upload_students():
    if not require_admin():
        abort(403)
    f = request.files.get("students_file")
    if not f:
        flash("Chưa chọn file students (Excel/CSV).", "warning")
        return redirect(url_for("admin_dashboard"))
    if pd is None:
        flash("pandas chưa được cài. Hãy pip install pandas để upload file Excel/CSV.", "warning")
        return redirect(url_for("admin_dashboard"))
    try:
        # read into DataFrame
        ext = (f.filename or "").lower()
        df = None
        content = f.read()
        try:
            if ext.endswith(".csv"):
                import io
                df = pd.read_csv(io.BytesIO(content))
            else:
                df = pd.read_excel(BytesIO(content))
        except Exception as e:
            flash(f"Lỗi đọc file: {e}", "danger")
            return redirect(url_for("admin_dashboard"))
        # normalize column names
        cols = [c.lower() for c in df.columns]
        # find class and name columns
        class_col = None
        name_col = None
        for c in df.columns:
            cl = c.lower()
            if cl in ("class","lop","lớp","lop_hoc","grade","room"):
                class_col = c
            if cl in ("name","ten","họ tên","hoten","fullname"):
                name_col = c
        # fallback heuristics
        if name_col is None:
            # pick first string-like column
            for c in df.columns:
                if df[c].dtype == object:
                    name_col = c
                    break
        if class_col is None:
            # set default class for all
            df["_class_default"] = "Chưa phân lớp"
            class_col = "_class_default"
        if name_col is None:
            flash("Không tìm thấy cột tên trong file.", "warning")
            return redirect(url_for("admin_dashboard"))
        # iterate rows and add students
        students = get_students()
        added = 0
        for _, row in df.iterrows():
            nm = str(row.get(name_col,"")).strip()
            cl = str(row.get(class_col,"")).strip()
            if not nm:
                continue
            exists = next((s for s in students if s["name"]==nm and s.get("class","")==cl), None)
            if not exists:
                students.append({"id": str(uuid.uuid4()), "name": nm, "class": cl, "created_at": datetime.utcnow().isoformat()})
                added += 1
        save_students(students)
        flash(f"Đã thêm {added} học sinh từ file.", "success")
    except Exception as e:
        flash(f"Lỗi khi import học sinh: {e}", "danger")
    return redirect(url_for("admin_dashboard"))

# Export logs to Excel/CSV
@app.get("/admin/export_logs")
def admin_export_logs():
    if not require_admin():
        abort(403)
    logs = get_logs()
    if not logs:
        flash("Chưa có logs để xuất.", "info")
        return redirect(url_for("admin_dashboard"))
    if pd is None:
        flash("pandas chưa được cài. Hãy pip install pandas openpyxl để xuất Excel.", "warning")
        return redirect(url_for("admin_dashboard"))
    rows = []
    quizzes = get_quizzes()
    qmap = {q["id"]: q for q in quizzes}
    for qid, students in logs.items():
        qtitle = qmap.get(qid, {}).get("title", qid)
        for student, events in students.items():
            for e in events:
                rows.append({
                    "quiz_id": qid,
                    "quiz_title": qtitle,
                    "student": student,
                    "time": e.get("time"),
                    "event": e.get("event")
                })
    df = pd.DataFrame(rows)
    filename = f"logs_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    df.to_excel(filename, index=False)
    return send_file(filename, as_attachment=True, download_name=filename)

@app.get("/admin/export_students")
def admin_export_students():
    if not require_admin():
        abort(403)
    students = get_students()
    if pd is None:
        # export CSV manual
        csv_lines = ["id,name,class,created_at"]
        for s in students:
            csv_lines.append(f'{s["id"]},"{s["name"]}","{s.get("class","")}",{s.get("created_at","")}')
        fname = f"students_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
        with open(fname, "w", encoding="utf-8") as f:
            f.write("\n".join(csv_lines))
        return send_file(fname, as_attachment=True, download_name=fname)
    else:
        df = pd.DataFrame(students)
        filename = f"students_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
        df.to_excel(filename, index=False)
        return send_file(filename, as_attachment=True, download_name=filename)

@app.get("/admin/export_results")
def admin_export_results():
    if not require_admin():
        abort(403)
    if pd is None:
        flash("pandas chưa được cài. Hãy pip install pandas openpyxl để xuất Excel.", "warning")
        return redirect(url_for("admin_dashboard"))
    submissions = get_submissions()
    students = get_students()
    quizzes = get_quizzes()
    rows = []
    for s in submissions:
        student = next((u for u in students if u["id"]==s["student_id"]), {})
        quiz = next((q for q in quizzes if q["id"]==s["quiz_id"]), {})
        rows.append({
            "ID": s["id"][:8]+"...",
            "Học sinh": student.get("name","-"),
            "Lớp": student.get("class","-"),
            "Kỳ thi": quiz.get("title","-"),
            "Điểm": f"{s.get('score')}/{s.get('total')}" if s.get('total') else "-",
            "Thời gian bắt đầu": s.get("started_at"),
            "Thời gian kết thúc": s.get("finished_at") or "-"
        })
    df = pd.DataFrame(rows)
    filename = f"results_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    df.to_excel(filename, index=False)
    return send_file(filename, as_attachment=True, download_name=filename)

# ---------------- Templates & Static (embedded) ----------------

BASE_HTML = r"""{% macro flash_msgs() %}
  {% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
      <div class="container mt-3">
        {% for cat, msg in messages %}
          <div class="alert alert-{{ 'info' if cat=='info' else cat }} alert-dismissible fade show" role="alert">
            {{ msg }}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
          </div>
        {% endfor %}
      </div>
    {% endif %}
  {% endwith %}
{% endmacro %}
<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{{ app_title }} - {% block title %}{% endblock %}</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" crossorigin="anonymous">
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
<nav class="navbar navbar-expand-lg navbar-dark bg-dark">
  <div class="container-fluid">
    <a class="navbar-brand" href="/">{{ app_title }}</a>
    <div class="collapse navbar-collapse">
      <ul class="navbar-nav me-auto">
        <li class="nav-item"><a class="nav-link" href="/">Trang chủ</a></li>
      </ul>
    </div>
  </div>
</nav>
{{ flash_msgs() }}
<main class="container my-4">
  {% block content %}{% endblock %}
</main>
<footer class="bg-dark text-light text-center py-3 mt-4">
  <small>© {{ app_title }} — chạy LAN bằng Flask + JSON</small>
</footer>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js" crossorigin="anonymous"></script>
<script src="{{ url_for('static', filename='app.js') }}"></script>
</body>
</html>
"""

INDEX_HTML = r"""{% extends 'base.html' %}{% block title %}Trang chủ{% endblock %}
{% block content %}
<div class="row justify-content-center">
  <div class="col-md-6">
    <div class="card shadow-sm">
      <div class="card-header bg-success text-white">Học sinh - Tham gia kỳ thi</div>
      <div class="card-body">
        <button class="btn btn-primary mb-3" onclick="toggleStudentCodeForm()">Bắt đầu</button>
        <div id="student-code-form" style="display:none;">
          <div class="mb-3 input-group">
            <input id="access_code" class="form-control" placeholder="Mã kỳ thi (VD: ABC123)">
            <button class="btn btn-success" onclick="checkCode()">Xác nhận</button>
          </div>
          <p id="codeStatus"></p>
        </div>
        <div id="student-form" style="display:none;">
          <div class="mb-3">
            <label class="form-label">Lớp</label>
            <select id="classSel" class="form-select" onchange="onClassChange()">
              <option value="">-- Chọn lớp --</option>
              {% for c in classes %}
                <option value="{{ c }}">{{ c }}</option>
              {% endfor %}
            </select>
          </div>
          <div class="mb-3">
            <label class="form-label">Học sinh</label>
            <select id="nameSel" class="form-select">
              <option value="">-- Chọn tên --</option>
            </select>
          </div>
          <button class="btn btn-success" onclick="joinQuiz()">Vào thi</button>
          <p id="joinStatus"></p>
          <hr>
          <p class="text-muted">Nếu tên không có trong danh sách, liên hệ giáo viên để được thêm vào lớp hoặc giáo viên có thể thêm thủ công trong phần Admin.</p>
        </div>
      </div>
    </div>
  </div>
  <div class="col-md-6">
    <div class="card shadow-sm">
      <div class="card-header bg-primary text-white">Giáo viên - Quản trị</div>
      <div class="card-body">
        <button class="btn btn-primary mb-3" onclick="toggleTeacherForm()">Đăng nhập</button>
        <div id="teacher-form" style="display:none;">
          <form method="post" action="/admin/login">
            <div class="mb-3"><input type="password" name="password" class="form-control" placeholder="Mật khẩu admin" required></div>
            <button class="btn btn-primary">Đăng nhập</button>
          </form>
          <p class="mt-2"><small>Mật khẩu mặc định: admin123 — đổi trong Admin.</small></p>
        </div>
      </div>
    </div>
  </div>
</div>
{% endblock %}"""

QUIZ_HTML = r"""{% extends 'base.html' %}{% block title %}{{ title }}{% endblock %}
{% block content %}
<div class="card shadow-sm">
  <div class="card-header bg-primary text-white d-flex justify-content-between align-items-center">
    <div><h5 class="mb-0">{{ title }}</h5><small class="text-light">Làm bài: không rời tab trong thời gian làm</small></div>
    <div class="fs-3" id="timer" style="display:none;">--:--</div>
  </div>
  <div class="card-body">
    <div id="anticheat-banner" style="position:fixed; right:12px; bottom:12px; z-index:9999; display:none;"></div>
    <form id="quizForm"></form>
    <div class="mt-3">
      <button id="submitBtn" class="btn btn-success" onclick="submitQuiz('{{ quiz_id }}')">Nộp bài</button>
      <p id="submitStatus" class="mt-2"></p>
    </div>
  </div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/mathjax/2.7.7/MathJax.js?config=TeX-MML-AM_CHTML"></script>
<script>
  const QUIZ_ID = '{{ quiz_id }}';
  const DURATION = {{ duration }}; // seconds
  const STUDENT_NAME = {{ student_name|tojson }};
</script>
{% endblock %}"""

ADMIN_LOGIN_HTML = r"""{% extends 'base.html' %}{% block title %}Admin login{% endblock %}
{% block content %}
<div class="row justify-content-center">
  <div class="col-md-6">
    <div class="card shadow-sm">
      <div class="card-header bg-primary text-white">Đăng nhập Giáo viên / Admin</div>
      <div class="card-body">
        <form method="post" action="/admin/login">
          <div class="mb-3"><input type="password" name="password" class="form-control" placeholder="Mật khẩu admin" required></div>
          <button class="btn btn-primary">Đăng nhập</button>
        </form>
        <p class="mt-2"><small>Mật khẩu mặc định: admin123 — đổi trong Admin.</small></p>
      </div>
    </div>
  </div>
</div>
{% endblock %}"""

ADMIN_HTML = r"""{% extends 'base.html' %}{% block title %}Quản trị{% endblock %}
{% block content %}
{% if not logged_in %}
  <p>Vui lòng đăng nhập tại <code>/admin/login</code></p>
{% else %}
<div class="row">
  <div class="col-md-5">
    <div class="card mb-3 shadow-sm">
      <div class="card-header bg-primary text-white">Tạo kỳ thi</div>
      <div class="card-body">
        <form method="post" action="/admin/create">
          <div class="mb-3"><label class="form-label">Tiêu đề</label><input name="title" class="form-control" required></div>
          <div class="mb-3"><label class="form-label">Thời lượng (phút)</label><input name="duration" type="number" min="1" class="form-control" required></div>
          <div class="mb-3"><label class="form-label">Mã truy cập</label><input name="access_code" class="form-control" placeholder="Tự tạo nếu bỏ trống"></div>
          <button class="btn btn-primary">Tạo</button>
        </form>
      </div>
    </div>

    <div class="card mb-3 shadow-sm">
      <div class="card-header bg-info text-white">Upload danh sách học sinh</div>
      <div class="card-body">
        <form method="post" action="/admin/upload_students" enctype="multipart/form-data">
          <div class="mb-2"><label class="form-label">Upload file học sinh (Excel/CSV)</label></div>
          <div class="mb-2"><input type="file" name="students_file" accept=".xls,.xlsx,.csv" class="form-control" required></div>
          <button class="btn btn-success mb-2">Upload</button>
        </form>
        <a class="btn btn-outline-primary mb-2" href="/admin/export_students">Xuất danh sách học sinh</a>
        <hr>
        <form method="post" action="/admin/change_password">
          <div class="mb-2"><label class="form-label">Đổi mật khẩu admin</label></div>
          <div class="mb-2"><input type="password" name="new_password" class="form-control" placeholder="Mật khẩu mới" required></div>
          <div class="mb-2"><input type="password" name="confirm_password" class="form-control" placeholder="Xác nhận mật khẩu" required></div>
          <button class="btn btn-warning">Đổi mật khẩu</button>
        </form>
      </div>
    </div>

    <div class="card mb-3 shadow-sm">
      <div class="card-header bg-secondary text-white">Xuất / Logs</div>
      <div class="card-body">
        <a class="btn btn-success mb-2" href="/admin/export_results">Xuất kết quả ra Excel</a>
        <a class="btn btn-secondary mb-2" href="/admin/export_logs">Xuất logs (blur events)</a>
        <a class="btn btn-outline-danger mb-2" href="/logout">Đăng xuất</a>
      </div>
    </div>
  </div>

  <div class="col-md-7">
    <div class="card shadow-sm">
      <div class="card-header bg-primary text-white">Danh sách kỳ thi</div>
      <div class="card-body">
        {% if quizzes|length==0 %}
          <p>Chưa có kỳ thi.</p>
        {% else %}
          <div class="table-responsive">
            <table class="table table-striped">
              <thead><tr><th>Tiêu đề</th><th>Mã</th><th>Thời lượng</th><th>Trạng thái</th><th>Hành động</th></tr></thead>
              <tbody>
              {% for q in quizzes %}
                <tr>
                  <td>{{ q.title }}</td>
                  <td><code>{{ q.access_code }}</code></td>
                  <td>{{ (q.duration_seconds//60) }} phút</td>
                  <td><span class="badge bg-{{ 'success' if q.is_active else 'secondary' }}">{{ 'Mở' if q.is_active else 'Đóng' }}</span></td>
                  <td>
                    <button class="btn btn-sm btn-warning edit-btn" data-id="{{ q.id }}" data-title="{{ q.title }}" data-duration="{{ q.duration_seconds//60 }}" data-code="{{ q.access_code }}">Sửa</button>
                    <form method="post" action="/admin/delete/{{ q.id }}" style="display:inline" onsubmit="return confirm('Xóa vĩnh viễn?');">
                      <button class="btn btn-sm btn-danger">Xóa</button>
                    </form>
                    <form method="post" action="/admin/toggle/{{ q.id }}" style="display:inline">
                      <button class="btn btn-sm btn-secondary">{{ 'Đóng' if q.is_active else 'Mở' }}</button>
                    </form>
                    <a class="btn btn-sm btn-primary" href="/quiz/{{ q.id }}" target="_blank">Mở đề</a>
                    <details class="mt-2">
                      <summary>Import / Quản lý câu hỏi ({{ (questions.get(q.id) or [])|length }})</summary>
                      <div class="mt-2">
                        <form method="post" action="/admin/import/{{ q.id }}" enctype="multipart/form-data">
                          <input type="file" name="docx" accept=".docx" required>
                          <button class="btn btn-sm btn-info">Import .docx</button>
                        </form>
                        <button class="btn btn-sm btn-success mt-2" onclick="openQModal('{{ q.id }}')">+ Thêm câu hỏi</button>
                        {% set qs = questions.get(q.id) or [] %}
                        {% if qs|length==0 %}
                          <p class="text-muted mt-2">Chưa có câu hỏi.</p>
                        {% else %}
                          <div class="table-responsive mt-2">
                            <table class="table table-bordered table-sm">
                              <thead><tr><th>#</th><th>Nội dung</th><th>Hình</th><th>Kiểu</th><th>Đáp án</th><th></th></tr></thead>
                              <tbody>
                                {% for item in qs %}
                                  <tr>
                                    <td>{{ loop.index }}</td>
                                    <td>{{ item.text }}</td>
                                    <td>{% if item.image %}<img src="{{ url_for('static', filename=item.image) }}" width="50">{% endif %}</td>
                                    <td>{{ 'Nhiều' if item.multi else 'Một' }}</td>
                                    <td>
                                      {% for o in item.options %}
                                        <div>{{ o.label }}) {{ o.text }} {% if o.image %}<img src="{{ url_for('static', filename=o.image) }}" width="50">{% endif %} {% if o.is_correct %}<span class="badge bg-success">Đúng</span>{% endif %}</div>
                                      {% endfor %}
                                    </td>
                                    <td>
                                      <button class="btn btn-sm btn-outline-warning" onclick="openQModal('{{ q.id }}','{{ item.id }}', {{ item|tojson }})">Sửa</button>
                                      <form method="post" action="/admin/questions/{{ q.id }}/{{ item.id }}/delete" style="display:inline" onsubmit="return confirm('Xóa câu hỏi?');">
                                        <button class="btn btn-sm btn-outline-danger">Xóa</button>
                                      </form>
                                    </td>
                                  </tr>
                                {% endfor %}
                              </tbody>
                            </table>
                          </div>
                        {% endif %}
                      </div>
                    </details>
                  </td>
                </tr>
              {% endfor %}
              </tbody>
            </table>
          </div>
        {% endif %}
      </div>
    </div>

    <div class="card shadow-sm mt-3">
      <div class="card-header bg-secondary text-white d-flex justify-content-between align-items-center">
        <span>Danh sách học sinh</span>
        <button class="btn btn-sm btn-success" onclick="openSModal()">+ Thêm học sinh</button>
      </div>
      <div class="card-body">
        <div class="row mb-3">
          <div class="col-md-6">
            <label class="form-label">Lọc theo lớp</label>
            <select id="classFilter" class="form-select" onchange="filterStudents()">
              <option value="">-- Tất cả lớp --</option>
              {% for cls in classes %}
                <option value="{{ cls }}">{{ cls }}</option>
              {% endfor %}
            </select>
          </div>
          <div class="col-md-6">
            <label class="form-label">Tìm tên</label>
            <input id="nameFilter" class="form-control" placeholder="Tìm tên học sinh" oninput="filterStudents()">
          </div>
        </div>
        <p><small>Tổng: <span id="studentCount">{{ students|length }}</span> học sinh</small></p>
        <div class="table-responsive">
          <table class="table table-sm" id="studentTable">
            <thead><tr><th>#</th><th>Tên</th><th>Lớp</th><th>Thêm lúc</th><th>Hành động</th></tr></thead>
            <tbody>
              {% for s in students %}
                <tr data-class="{{ s.class|default('Chưa phân lớp') }}"><td>{{ loop.index }}</td><td>{{ s.name }}</td><td>{{ s.class|default('Chưa phân lớp') }}</td><td>{{ s.created_at }}</td>
                  <td>
                    <button class="btn btn-sm btn-outline-warning" onclick="openSModal('{{ s.id }}', {{ s|tojson }})">Sửa</button>
                    <form method="post" action="/admin/students/{{ s.id }}/delete" style="display:inline" onsubmit="return confirm('Xóa học sinh?');">
                      <button class="btn btn-sm btn-outline-danger">Xóa</button>
                    </form>
                  </td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- Edit modal & question modal -->
<div class="modal fade" id="editModal" tabindex="-1">
  <div class="modal-dialog">
    <form method="post" id="editForm" class="modal-content">
      <div class="modal-header"><h5 class="modal-title">Sửa kỳ thi</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
      <div class="modal-body">
        <div class="mb-3"><label class="form-label">Tiêu đề</label><input name="title" id="editTitle" class="form-control" required></div>
        <div class="mb-3"><label class="form-label">Thời lượng (phút)</label><input name="duration" id="editDuration" type="number" min="1" class="form-control" required></div>
        <div class="mb-3"><label class="form-label">Mã truy cập</label><input name="access_code" id="editCode" class="form-control"></div>
      </div>
      <div class="modal-footer"><button class="btn btn-primary">Lưu</button></div>
    </form>
  </div>
</div>

<div class="modal fade" id="qModal" tabindex="-1">
  <div class="modal-dialog modal-lg">
    <form method="post" id="qForm" class="modal-content" enctype="multipart/form-data">
      <div class="modal-header"><h5 id="qModalLabel" class="modal-title">Câu hỏi</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
      <div class="modal-body">
        <div class="mb-3"><label class="form-label">Nội dung</label><textarea name="q_text" id="qText" class="form-control" rows="2" required></textarea></div>
        <div class="mb-3"><label class="form-label">Hình ảnh câu hỏi (tùy chọn)</label><input type="file" name="q_image" class="form-control" accept="image/*"></div>
        <div class="form-check form-switch mb-3"><input class="form-check-input" type="checkbox" id="qMulti" name="q_multi"><label class="form-check-label">Cho phép chọn nhiều đáp án</label></div>
        <div><label class="form-label">Đáp án</label><div id="optList"></div><button type="button" class="btn btn-sm btn-outline-secondary mt-2" onclick="addOptRow()">+ Thêm đáp án</button></div>
      </div>
      <div class="modal-footer"><button class="btn btn-primary">Lưu</button></div>
    </form>
  </div>
</div>

<div class="modal fade" id="sModal" tabindex="-1">
  <div class="modal-dialog">
    <form method="post" id="sForm" class="modal-content">
      <div class="modal-header"><h5 id="sModalLabel" class="modal-title">Học sinh</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
      <div class="modal-body">
        <div class="mb-3"><label class="form-label">Tên</label><input name="s_name" id="sName" class="form-control" required></div>
        <div class="mb-3"><label class="form-label">Lớp</label><input name="s_class" id="sClass" class="form-control" required></div>
      </div>
      <div class="modal-footer"><button class="btn btn-primary">Lưu</button></div>
    </form>
  </div>
</div>

<script>
document.querySelectorAll('.edit-btn').forEach(btn=>{
  btn.addEventListener('click', function(){
    const id=this.dataset.id, title=this.dataset.title, dur=this.dataset.duration, code=this.dataset.code;
    document.getElementById('editTitle').value=title;
    document.getElementById('editDuration').value=dur;
    document.getElementById('editCode').value=code;
    document.getElementById('editForm').action=`/admin/update/${id}`;
    new bootstrap.Modal(document.getElementById('editModal')).show();
  });
});
function addOptRow(opt={label:'', text:'', is_correct:0, image:''}){
  const wrap=document.getElementById('optList');
  const div=document.createElement('div'); div.className='row g-2 align-items-center mb-2';
  div.innerHTML=`<div class="col-2"><input class="form-control" name="opt_label" placeholder="A" value="${opt.label||''}"></div>
  <div class="col-5"><input class="form-control" name="opt_text" placeholder="Nội dung đáp án" value="${opt.text||''}" required></div>
  <div class="col-3"><input type="file" class="form-control" name="opt_image" accept="image/*"></div>
  <div class="col-2 form-check d-flex align-items-center"><input class="form-check-input me-2" type="checkbox" name="opt_correct" value="${opt.label||''}" ${opt.is_correct?'checked':''} onchange="this.value=this.parentNode.parentNode.querySelector('[name=opt_label]').value"><label class="form-check-label">Đúng</label></div>`;
  if(opt.image) div.querySelector('.col-3').innerHTML += `<small>Hiện tại: ${opt.image}</small>`;
  wrap.appendChild(div);
}
function openQModal(quizId, qId=null, data=null){
  const form=document.getElementById('qForm');
  document.getElementById('qText').value = data ? (data.text||'') : '';
  document.getElementById('qMulti').checked = data ? Boolean(data.multi) : false;
  const list=document.getElementById('optList'); list.innerHTML='';
  if (data && data.options && data.options.length){
    data.options.forEach(o=>addOptRow(o));
  } else { ['A','B','C','D'].forEach(lbl=>addOptRow({label:lbl, text:'', is_correct:0})); }
  if (qId){
    form.action = `/admin/questions/${quizId}/${qId}/update`;
    document.getElementById('qModalLabel').innerText = 'Sửa câu hỏi';
  } else {
    form.action = `/admin/questions/${quizId}/create`;
    document.getElementById('qModalLabel').innerText = 'Thêm câu hỏi';
  }
  new bootstrap.Modal(document.getElementById('qModal')).show();
}
function openSModal(sId=null, data=null){
  const form=document.getElementById('sForm');
  document.getElementById('sName').value = data ? (data.name||'') : '';
  document.getElementById('sClass').value = data ? (data.class||'') : '';
  if (sId){
    form.action = `/admin/students/${sId}/update`;
    document.getElementById('sModalLabel').innerText = 'Sửa học sinh';
  } else {
    form.action = `/admin/students/create`;
    document.getElementById('sModalLabel').innerText = 'Thêm học sinh';
  }
  new bootstrap.Modal(document.getElementById('sModal')).show();
}
function filterStudents(){
  const classFilter = document.getElementById('classFilter').value;
  const nameFilter = document.getElementById('nameFilter').value.toLowerCase();
  const rows = document.querySelectorAll('#studentTable tbody tr');
  let count = 0;
  rows.forEach(row => {
    const rowClass = row.dataset.class || 'Chưa phân lớp';
    const rowName = row.querySelectorAll('td')[1].textContent.toLowerCase();
    if ((!classFilter || rowClass === classFilter) && rowName.includes(nameFilter)) {
      row.style.display = '';
      count++;
    } else {
      row.style.display = 'none';
    }
  });
  document.getElementById('studentCount').textContent = count;
}
</script>

{% endif %}
{% endblock %}"""

LOGS_HTML = r"""{% extends 'base.html' %}{% block title %}Logs chống gian lận{% endblock %}
{% block content %}
<h3>Logs chống gian lận (rời tab / events)</h3>
{% if annotated %}
  {% for qid, info in annotated.items() %}
    <div class="card mb-3">
      <div class="card-header"><strong>{{ info.meta.title }}</strong> — mã: <code>{{ info.meta.access_code }}</code></div>
      <div class="card-body">
        {% if not info.logs %}
          <p class="text-muted">Không có sự kiện</p>
        {% else %}
          {% for student, events in info.logs.items() %}
            <h5>{{ student }}</h5>
            <ul>
              {% for e in events %}
                <li>{{ e.time }} — {{ e.event }}</li>
              {% endfor %}
            </ul>
          {% endfor %}
        {% endif %}
      </div>
    </div>
  {% endfor %}
{% else %}
  <p>Chưa có dữ liệu logs.</p>
{% endif %}
{% endblock %}"""

RESULTS_HTML = r"""{% extends 'base.html' %}{% block title %}Kết quả{% endblock %}
{% block content %}
<div class="card shadow-sm">
  <div class="card-header bg-primary text-white">Kết quả nộp bài</div>
  <div class="card-body">
    <a class="btn btn-success mb-3" href="/admin/export_results">Xuất kết quả ra Excel</a>
    <div class="table-responsive">
      <table class="table table-striped">
        <thead><tr><th>ID</th><th>Học sinh</th><th>Lớp</th><th>Kỳ thi</th><th>Điểm</th><th>Bắt đầu</th><th>Kết thúc</th></tr></thead>
        <tbody>
          {% for r in rows %}
          <tr>
            <td>{{ r.id[:8] }}...</td>
            <td>{{ r.name }}</td>
            <td>{{ r.class }}</td>
            <td>{{ r.title }}</td>
            <td>{{ r.score }}/{{ r.total }}</td>
            <td>{{ r.started_at }}</td>
            <td>{{ r.finished_at }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>
{% endblock %}"""

APP_JS = r"""
// index uses /api/classes to populate class/name dropdowns
async function loadClasses() {
  const res = await fetch('/api/classes');
  const data = await res.json();
  const classSel = document.getElementById('classSel');
  const nameSel = document.getElementById('nameSel');
  classSel.innerHTML = '<option value="">-- Chọn lớp --</option>';
  for (const cls of Object.keys(data).sort()) {
    const opt = document.createElement('option'); opt.value = cls; opt.textContent = cls;
    classSel.appendChild(opt);
  }
  nameSel.innerHTML = '<option value="">-- Chọn tên --</option>';
}
function onClassChange(){
  const cls = document.getElementById('classSel').value;
  const nameSel = document.getElementById('nameSel');
  nameSel.innerHTML = '<option value="">-- Chọn tên --</option>';
  if (!cls) return;
  fetch('/api/classes').then(r=>r.json()).then(data=>{
    const arr = data[cls] || [];
    arr.sort((a,b)=>a.name.localeCompare(b.name));
    arr.forEach(s=>{
      const opt = document.createElement('option'); opt.value = s.name; opt.textContent = s.name;
      nameSel.appendChild(opt);
    });
  });
}

async function registerName(name, cls){
  const res = await fetch('/api/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name, class:cls})});
  return res.json();
}

let currentCode = '';
async function checkCode(){
  const code = document.getElementById('access_code').value.trim();
  if(!code){ document.getElementById('codeStatus').textContent='Vui lòng nhập mã kỳ thi!'; return; }
  const res = await fetch('/api/check_code',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({access_code: code})});
  const data = await res.json();
  if(data.ok){ 
    currentCode = code;
    document.getElementById('codeStatus').textContent = '';
    document.getElementById('student-code-form').style.display = 'none';
    document.getElementById('student-form').style.display = 'block';
  }
  else { document.getElementById('codeStatus').textContent = data.error || 'Lỗi kiểm tra mã'; }
}

async function joinQuiz(){
  const cls = document.getElementById('classSel').value;
  const name = document.getElementById('nameSel').value;
  if(!cls){ document.getElementById('joinStatus').textContent='Vui lòng chọn lớp!'; return; }
  if(!name){ document.getElementById('joinStatus').textContent='Vui lòng chọn tên!'; return; }
  const rc = await registerName(name, cls);
  if(!rc.ok){ document.getElementById('joinStatus').textContent = rc.error || 'Lỗi lưu tên'; return; }
  const res = await fetch('/api/join',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({access_code: currentCode})});
  const data = await res.json();
  if(data.ok){ location.href = '/quiz/' + data.quiz_id; }
  else { document.getElementById('joinStatus').textContent = data.error || 'Lỗi tham gia'; }
}

function toggleStudentCodeForm(){
  document.getElementById('student-code-form').style.display = 'block';
}

function toggleTeacherForm(){
  document.getElementById('teacher-form').style.display = 'block';
}

// Quiz page logic
if (typeof QUIZ_ID !== 'undefined') {
  try { document.getElementById('timer').style.display = 'block'; } catch(e) {}

  let remaining = DURATION;
  const timerEl = document.getElementById('timer');
  const formEl = document.getElementById('quizForm');
  const submitBtn = document.getElementById('submitBtn');
  function renderTimer(){
    const m = Math.floor(remaining/60).toString().padStart(2,'0');
    const s = (remaining%60).toString().padStart(2,'0');
    timerEl.textContent = m + ':' + s;
  }
  function tick(){
    remaining -= 1;
    renderTimer();
    if(remaining <= 0){
      submitQuiz(QUIZ_ID);
    } else {
      setTimeout(tick, 1000);
    }
  }
  async function loadQuiz(){
    const res = await fetch('/api/quiz/' + QUIZ_ID);
    const data = await res.json();
    if (data.error){ document.getElementById('submitStatus').textContent = data.error; return; }
    data.questions.forEach((q, idx) => {
      const card = document.createElement('div'); card.className='card mb-3';
      const header = document.createElement('div'); header.className='card-header'; header.innerHTML=`Câu ${idx+1}/${data.questions.length}: ${q.text}`;
      if(q.image) header.innerHTML += `<br><img src="${q.image}" class="img-fluid" alt="Question image">`;
      card.appendChild(header);
      const body = document.createElement('div'); body.className='card-body';
      q.options.forEach(opt => {
        const id = `q${q.id}_${opt.label}`;
        const div = document.createElement('div'); div.className='form-check mb-2';
        const input = document.createElement('input'); input.className='form-check-input';
        input.type = q.multi ? 'checkbox' : 'radio';
        input.name = 'q_' + q.id + (q.multi ? '[]' : '');
        input.value = opt.label;
        input.id = id;
        const label = document.createElement('label'); label.className='form-check-label'; label.htmlFor = id; 
        label.innerHTML = `${opt.label}) ${opt.text}`;
        if(opt.image) label.innerHTML += `<br><img src="${opt.image}" class="img-fluid" alt="Option image">`;
        div.appendChild(input); div.appendChild(label); body.appendChild(div);
      });
      card.appendChild(body); formEl.appendChild(card);
    });
    MathJax.typeset();
    document.getElementById('timer').style.display = 'block'; renderTimer(); setTimeout(tick, 1000);
  }
  loadQuiz();
  window.submitQuiz = async function(quizId){
    if (!confirm('Bạn chắc chắn muốn nộp bài?')) return;
    submitBtn.disabled = true;
    const answers = {};
    const inputs = document.querySelectorAll('input[name^="q_"]');
    inputs.forEach(inp => {
      const name = inp.name.replace('[]', '');
      if (!answers[name]) answers[name] = [];
      if (inp.checked) answers[name].push(inp.value);
    });
    const formatted = {};
    Object.keys(answers).forEach(k=>{
      const qid = k.split('_')[1];
      formatted[qid] = answers[k];
    });
    const res = await fetch('/api/submit/' + quizId, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({answers: formatted})});
    const data = await res.json();
    const statusEl = document.getElementById('submitStatus');
    
    if (data.ok){
      statusEl.textContent = `Đã nộp! Điểm: ${data.score}/${data.total}`;
      statusEl.className = 'text-success';
      // Hiện modal xác nhận
      const modalHtml = `
        <div class="modal fade" id="resultModal" tabindex="-1">
          <div class="modal-dialog">
            <div class="modal-content">
              <div class="modal-header bg-success text-white">
                <h5 class="modal-title">Nộp bài thành công</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
              </div>
              <div class="modal-body">
                <p>Bạn đã hoàn thành bài thi.</p>
                <p><strong>Điểm: ${data.score}/${data.total}</strong></p>
              </div>
              <div class="modal-footer">
                <button class="btn btn-primary" data-bs-dismiss="modal">Đóng</button>
              </div>
            </div>
          </div>
        </div>`;
      document.body.insertAdjacentHTML('beforeend', modalHtml);
      new bootstrap.Modal(document.getElementById('resultModal')).show();
    } else {
      statusEl.textContent = data.error || 'Lỗi nộp bài';
      statusEl.className = 'text-danger';
    }

    submitBtn.disabled = false;
  };
  // anti-cheat: show small banner on blur (non-blocking) and send log to server
  function showAntiCheat(msg){
    try {
      const banner = document.getElementById('anticheat-banner');
      if (!banner) return;
      banner.innerHTML = '<div class="alert alert-warning shadow-sm mb-0" role="alert">' + msg + '</div>';
      banner.style.display = 'block';
      setTimeout(()=>{ banner.style.display='none'; banner.innerHTML=''; }, 3000);
    } catch(e){}
  }
  window.addEventListener('blur', function(){
    showAntiCheat('⚠️ Bạn vừa rời khỏi tab — hành động có thể bị ghi nhận.');
    try {
      fetch('/log_event', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({quiz_id: QUIZ_ID, student: STUDENT_NAME, event: 'blur'})
      }).catch(()=>{});
    } catch(e){}
  });
  document.addEventListener("contextmenu", e=>e.preventDefault());
  document.onkeydown = function(e){ if(e.keyCode==123 || (e.ctrlKey&&e.shiftKey&&e.keyCode==73)) { e.preventDefault(); return false; } };
}

// Initialize index class list when page loads
try { if (document.readyState !== 'loading') loadClasses(); else document.addEventListener('DOMContentLoaded', loadClasses); } catch(e){}
"""

APP_CSS = r"""
body { background:#f8f9fa; }
.card-header { font-weight:500; }
.table-responsive{ max-height:500px; overflow:auto; }
.modal-dialog { max-width:900px; }
"""

# ---------------- App start ----------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    ensure_dirs_and_assets()
    print(f"{APP_TITLE} starting. Open http://{args.host}:{args.port}/")
    app.run(host=args.host, port=args.port, debug=True)