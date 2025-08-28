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

BASE_DIR = Path(__file__).parent
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

def ensure_dirs():
    UPLOAD_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)
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
        flash("Mật khẩu mới không khớp hoặc rỗng", "danger")
        return redirect(url_for("admin_dashboard"))
    cfg = get_config()
    cfg["ADMIN_PASSWORD"] = newpw
    save_config(cfg)
    flash("Đã đổi mật khẩu admin", "success")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/create_quiz")
def admin_create_quiz():
    if not require_admin():
        abort(403)
    title = request.form.get("title","").strip()
    duration = int(request.form.get("duration_seconds", 1800))
    access_code = secrets.token_hex(4).upper()
    quiz_id = str(uuid.uuid4())
    quizzes = get_quizzes()
    quizzes.append({
        "id": quiz_id,
        "title": title,
        "duration_seconds": duration,
        "access_code": access_code,
        "is_active": True,
        "created_at": datetime.utcnow().isoformat()
    })
    save_quizzes(quizzes)
    questions = get_questions()
    if quiz_id not in questions:
        questions[quiz_id] = []
    save_questions(questions)
    flash(f"Đã tạo quiz: {title}", "success")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/update/<quiz_id>")
def admin_update_quiz(quiz_id):
    if not require_admin():
        abort(403)
    title = request.form.get("title","").strip()
    duration = int(request.form.get("duration_seconds", 1800))
    access_code = request.form.get("access_code","").strip().upper()
    quizzes = get_quizzes()
    quiz = next((q for q in quizzes if q["id"]==quiz_id), None)
    if not quiz:
        abort(404)
    quiz["title"] = title
    quiz["duration_seconds"] = duration
    quiz["access_code"] = access_code
    save_quizzes(quizzes)
    flash(f"Đã cập nhật quiz: {title}", "success")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/toggle/<quiz_id>")
def admin_toggle_quiz(quiz_id):
    if not require_admin():
        abort(403)
    quizzes = get_quizzes()
    quiz = next((q for q in quizzes if q["id"]==quiz_id), None)
    if not quiz:
        abort(404)
    quiz["is_active"] = not quiz.get("is_active", True)
    save_quizzes(quizzes)
    status = "mở" if quiz["is_active"] else "đóng"
    flash(f"Đã {status} quiz: {quiz['title']}", "success")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/delete/<quiz_id>")
def admin_delete_quiz(quiz_id):
    if not require_admin():
        abort(403)
    quizzes = [q for q in get_quizzes() if q["id"] != quiz_id]
    save_quizzes(quizzes)
    questions = get_questions()
    if quiz_id in questions:
        del questions[quiz_id]
    save_questions(questions)
    flash("Đã xóa quiz", "success")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/questions/<quiz_id>/create")
def admin_create_question(quiz_id):
    if not require_admin():
        abort(403)
    quizzes = get_quizzes()
    if not next((q for q in quizzes if q["id"]==quiz_id), None):
        abort(404)
    text = request.form.get("q_text","").strip()
    multi = bool(request.form.get("q_multi"))
    opts_labels = request.form.getlist("opt_label")
    opts_texts = request.form.getlist("opt_text")
    opts_corrects = request.form.getlist("opt_correct")
    opts_images = request.files.getlist("opt_image")
    opts = []
    for i in range(len(opts_labels)):
        label = opts_labels[i].strip().upper() or chr(65 + i)
        text_opt = opts_texts[i].strip()
        if not text_opt:
            continue
        is_correct = label in opts_corrects
        image = None
        if opts_images[i] and opts_images[i].filename:
            fn = secure_filename(opts_images[i].filename)
            path = STATIC_UPLOADS_DIR / fn
            opts_images[i].save(path)
            image = url_for("static", filename=f"uploads/{fn}")
        opts.append({"label": label, "text": text_opt, "is_correct": 1 if is_correct else 0, "image": image})
    q_id = str(uuid.uuid4())
    questions = get_questions()
    if quiz_id not in questions:
        questions[quiz_id] = []
    questions[quiz_id].append({
        "id": q_id,
        "text": text,
        "multi": 1 if multi else 0,
        "options": opts,
        "image": None  # TODO: support question image if needed
    })
    save_questions(questions)
    flash("Đã thêm câu hỏi", "success")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/questions/<quiz_id>/<q_id>/update")
def admin_update_question(quiz_id, q_id):
    if not require_admin():
        abort(403)
    # similar to create, but find and replace
    text = request.form.get("q_text","").strip()
    multi = bool(request.form.get("q_multi"))
    opts_labels = request.form.getlist("opt_label")
    opts_texts = request.form.getlist("opt_text")
    opts_corrects = request.form.getlist("opt_correct")
    opts_images = request.files.getlist("opt_image")
    opts = []
    for i in range(len(opts_labels)):
        label = opts_labels[i].strip().upper() or chr(65 + i)
        text_opt = opts_texts[i].strip()
        if not text_opt:
            continue
        is_correct = label in opts_corrects
        image = None
        if opts_images[i] and opts_images[i].filename:
            fn = secure_filename(opts_images[i].filename)
            path = STATIC_UPLOADS_DIR / fn
            opts_images[i].save(path)
            image = url_for("static", filename=f"uploads/{fn}")
        opts.append({"label": label, "text": text_opt, "is_correct": 1 if is_correct else 0, "image": image})
    questions = get_questions()
    qs = questions.get(quiz_id, [])
    q = next((qq for qq in qs if qq["id"]==q_id), None)
    if not q:
        abort(404)
    q["text"] = text
    q["multi"] = 1 if multi else 0
    q["options"] = opts
    save_questions(questions)
    flash("Đã cập nhật câu hỏi", "success")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/questions/<quiz_id>/<q_id>/delete")
def admin_delete_question(quiz_id, q_id):
    if not require_admin():
        abort(403)
    questions = get_questions()
    if quiz_id in questions:
        questions[quiz_id] = [q for q in questions[quiz_id] if q["id"] != q_id]
    save_questions(questions)
    flash("Đã xóa câu hỏi", "success")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/import_questions/<quiz_id>")
def admin_import_questions(quiz_id):
    if not require_admin():
        abort(403)
    if Document is None:
        flash("Chưa cài python-docx", "danger")
        return redirect(url_for("admin_dashboard"))
    file = request.files.get("docx_file")
    if not file or not file.filename.endswith(".docx"):
        flash("Vui lòng upload file .docx", "danger")
        return redirect(url_for("admin_dashboard"))
    try:
        docx = BytesIO(file.read())
        new_qs = parse_docx_questions(docx)
        if not new_qs:
            flash("Không tìm thấy câu hỏi hợp lệ trong file", "warning")
            return redirect(url_for("admin_dashboard"))
        questions = get_questions()
        if quiz_id not in questions:
            questions[quiz_id] = []
        for q in new_qs:
            q["id"] = str(uuid.uuid4())
        questions[quiz_id].extend(new_qs)
        save_questions(questions)
        flash(f"Đã import {len(new_qs)} câu hỏi", "success")
    except Exception as e:
        flash(f"Lỗi import: {str(e)}", "danger")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/students/create")
def admin_create_student():
    if not require_admin():
        abort(403)
    name = request.form.get("s_name","").strip()
    cls = request.form.get("s_class","").strip()
    if not name or not cls:
        flash("Thiếu tên hoặc lớp", "danger")
        return redirect(url_for("admin_dashboard"))
    students = get_students()
    student_id = str(uuid.uuid4())
    students.append({"id": student_id, "name": name, "class": cls, "created_at": datetime.utcnow().isoformat()})
    save_students(students)
    flash(f"Đã thêm học sinh: {name}", "success")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/students/<s_id>/update")
def admin_update_student(s_id):
    if not require_admin():
        abort(403)
    name = request.form.get("s_name","").strip()
    cls = request.form.get("s_class","").strip()
    if not name or not cls:
        flash("Thiếu tên hoặc lớp", "danger")
        return redirect(url_for("admin_dashboard"))
    students = get_students()
    student = next((s for s in students if s["id"]==s_id), None)
    if not student:
        abort(404)
    student["name"] = name
    student["class"] = cls
    save_students(students)
    flash(f"Đã cập nhật học sinh: {name}", "success")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/students/<s_id>/delete")
def admin_delete_student(s_id):
    if not require_admin():
        abort(403)
    students = [s for s in get_students() if s["id"] != s_id]
    save_students(students)
    flash("Đã xóa học sinh", "success")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/import_students")
def admin_import_students():
    if not require_admin():
        abort(403)
    if pd is None:
        flash("Chưa cài pandas", "danger")
        return redirect(url_for("admin_dashboard"))
    file = request.files.get("students_file")
    if not file or not (file.filename.endswith(".xlsx") or file.filename.endswith(".csv")):
        flash("Vui lòng upload file .xlsx hoặc .csv", "danger")
        return redirect(url_for("admin_dashboard"))
    try:
        if file.filename.endswith(".xlsx"):
            df = pd.read_excel(file)
        else:
            df = pd.read_csv(file)
        required_cols = {"name", "class"}
        if not required_cols.issubset(set(df.columns)):
            flash("File phải có cột: name, class", "danger")
            return redirect(url_for("admin_dashboard"))
        students = get_students()
        existing = {(s["name"], s["class"]) for s in students}
        added = 0
        for _, row in df.iterrows():
            name = str(row["name"]).strip()
            cls = str(row["class"]).strip()
            if not name or not cls:
                continue
            if (name, cls) in existing:
                continue
            student_id = str(uuid.uuid4())
            students.append({"id": student_id, "name": name, "class": cls, "created_at": datetime.utcnow().isoformat()})
            added += 1
        save_students(students)
        flash(f"Đã import {added} học sinh mới", "success")
    except Exception as e:
        flash(f"Lỗi import: {str(e)}", "danger")
    return redirect(url_for("admin_dashboard"))

@app.get("/admin/results")
def admin_results():
    if not require_admin():
        abort(403)
    submissions = get_submissions()
    students = {s["id"]: s for s in get_students()}
    quizzes = {q["id"]: q for q in get_quizzes()}
    rows = []
    for sub in submissions:
        student = students.get(sub["student_id"], {"name": "Unknown", "class": "Unknown"})
        quiz = quizzes.get(sub["quiz_id"], {"title": "Unknown"})
        rows.append({
            "id": sub["id"],
            "name": student["name"],
            "class": student["class"],
            "title": quiz["title"],
            "score": sub.get("score", 0),
            "total": sub.get("total", 0),
            "started_at": sub.get("started_at"),
            "finished_at": sub.get("finished_at")
        })
    rows = sorted(rows, key=lambda r: r.get("started_at", ""), reverse=True)
    return render_template("results.html", rows=rows, app_title=APP_TITLE)

@app.get("/admin/export_results")
def admin_export_results():
    if not require_admin():
        abort(403)
    if pd is None:
        abort(500, "pandas not installed")
    submissions = get_submissions()
    students = {s["id"]: s for s in get_students()}
    quizzes = {q["id"]: q for q in get_quizzes()}
    data = []
    for sub in submissions:
        student = students.get(sub["student_id"], {"name": "Unknown", "class": "Unknown"})
        quiz = quizzes.get(sub["quiz_id"], {"title": "Unknown"})
        data.append({
            "Submission ID": sub["id"],
            "Student Name": student["name"],
            "Class": student["class"],
            "Quiz Title": quiz["title"],
            "Score": sub.get("score", 0),
            "Total": sub.get("total", 0),
            "Started At": sub.get("started_at"),
            "Finished At": sub.get("finished_at")
        })
    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="quiz_results.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ---------------- App start ----------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")  # Đổi để support cloud/local
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5000)))  # Support Render PORT
    args = parser.parse_args()
    ensure_dirs()
    print(f"{APP_TITLE} starting. Open http://{args.host}:{args.port}/")
    app.run(host=args.host, port=args.port, debug=True)