from flask import Flask, render_template, request, redirect, url_for, session

app = Flask(__name__)
app.secret_key = "quizlan_secret_key"

# Trang chủ
@app.route("/")
def index():
    return render_template("index.html")

# Trang học sinh
@app.route("/student")
def student():
    return render_template("student.html")

# Trang đăng nhập Admin
@app.route("/admin", methods=["GET", "POST"])
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = request.form.get("password")
        if password == "admin123":  # mật khẩu mặc định
            session["admin"] = True
            return redirect(url_for("admin_dashboard"))
        else:
            return render_template("login.html", error="Sai mật khẩu!")
    return render_template("login.html")

# Trang quản trị
@app.route("/admin/dashboard")
def admin_dashboard():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    return render_template("admin.html")

# Logout
@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True)
