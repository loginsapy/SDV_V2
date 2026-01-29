
# vacations/routes/auth.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from werkzeug.security import check_password_hash
from ..db import get_db

bp = Blueprint('auth', __name__, url_prefix='/auth')

@bp.route('/login', methods=('GET', 'POST'))
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        db = get_db()
        user = db.execute(
            """SELECT e.id, e.full_name, e.role, e.password, r.base_role 
               FROM employees e 
               LEFT JOIN roles r ON e.role = r.name 
               WHERE e.username = ? AND e.is_active = 1;""",
            (username,)
        ).fetchone()

        if user and check_password_hash(user["password"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["full_name"] = user["full_name"]
            session["role"] = user["role"]
            # Usar base_role para permisos, fallback al rol normal si no existe
            session["base_role"] = user["base_role"] if user["base_role"] else user["role"]
            flash(f"¡Bienvenido/a {session['full_name']}!", "success")
            return redirect(url_for("main.dashboard"))
        else:
            flash("Usuario o contraseña incorrectos.", "danger")

    return render_template("auth/login.html")

@bp.route('/logout')
def logout():
    session.clear()
    flash("Has cerrado sesión.", "info")
    return redirect(url_for("auth.login"))