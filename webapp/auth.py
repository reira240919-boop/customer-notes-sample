from functools import wraps

from flask import Blueprint, current_app, redirect, render_template, request, session, url_for

import src.sheets_client as sc

bp = Blueprint("auth", __name__)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        # 見本ではログインを省く（作り物のデータしか無いため）
        if not session.get("logged_in") and not current_app.config.get("SAMPLE_MODE"):
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        sid = current_app.config["SPREADSHEET_ID"]
        initial_password = current_app.config["INITIAL_APP_PASSWORD"]
        if sc.verify_password(sid, password, initial_password):
            session["logged_in"] = True
            next_path = request.args.get("next") or url_for("routes.reminders")
            return redirect(next_path)
        error = "パスワードが違います"
    return render_template("login.html", error=error)


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
