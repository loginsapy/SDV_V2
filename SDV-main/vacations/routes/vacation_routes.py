from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, send_from_directory
from datetime import datetime, date, timedelta
from werkzeug.utils import secure_filename
import os
from ..db import get_db
from ..utils import calculate_working_days, send_email, get_paraguay_holidays

bp = Blueprint('vacation_routes', __name__, url_prefix='/vacations')

@bp.route('/new', methods=('GET', 'POST'))
def new():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    db = get_db()
    employee_id = session["user_id"]

    # Obtener tipos de licencia ASIGNADOS al empleado (que tengan registro en vacation_periods)
    leave_types_raw = db.execute("""
        SELECT DISTINCT lt.* 
        FROM leave_types lt
        JOIN vacation_periods vp ON lt.id = vp.leave_type_id
        WHERE vp.employee_id = ?
    """, (employee_id,)).fetchall()

    leave_types = []
    for row in leave_types_raw:
        lt = dict(row)
        lt['requires_balance'] = 1 # Forzar visualización de saldo si tiene periodo asignado
        leave_types.append(lt)

    if not leave_types:
        flash("No tienes tipos de licencia asignados (sin saldo). Por favor contacta a RRHH para que te asignen un periodo.", "warning")

    # Obtener todos los saldos disponibles por tipo de licencia
    all_balances = db.execute(
        "SELECT leave_type_id, SUM(total_days_accrued - days_taken) as balance FROM vacation_periods WHERE employee_id = ? GROUP BY leave_type_id",
        (employee_id,)
    ).fetchall()
    balances_map = {row['leave_type_id']: row['balance'] for row in all_balances}
    total_balance = sum(row['balance'] for row in all_balances) if all_balances else 0

    # Usar base_role de la sesión para la lógica de permisos
    current_role = session.get("base_role")

    # Construir consulta de reemplazos según reglas de negocio
    # Se une con la tabla roles para filtrar por el nivel (base_role)
    rep_query = """
        SELECT e.full_name 
        FROM employees e 
        JOIN roles r ON e.role = r.name 
        WHERE e.is_active = 1 AND e.id != ?
    """
    rep_params = [employee_id]

    if current_role == 'Jefe':
        rep_query += " AND r.base_role = 'Jefe'"
    elif current_role in ['RRHH', 'Asistente RRHH']:
        rep_query += " AND r.base_role IN ('RRHH', 'Asistente RRHH')"
    else:
        # Empleados (y otros roles) solo pueden seleccionar pares del mismo rol
        rep_query += " AND r.base_role = ?"
        rep_params.append(current_role)
    
    rep_query += " ORDER BY full_name"
    employees = db.execute(rep_query, rep_params).fetchall()

    # Obtener feriados y sábados laborales para el cálculo en el frontend
    holidays_dict = get_paraguay_holidays()
    holidays_list = [d.strftime('%d/%m/%Y') for d in holidays_dict.keys()]
    
    working_saturdays_rows = db.execute("SELECT effective_date FROM saturday_config WHERE is_working = 1").fetchall()
    working_saturdays = [row['effective_date'].strftime('%d/%m/%Y') for row in working_saturdays_rows]

    # Obtener feriados recurrentes (MM-DD) para cálculo en frontend (cualquier año)
    recurring_holidays_rows = db.execute("SELECT holiday_date FROM custom_holidays WHERE is_recurring = 1").fetchall()
    recurring_holidays = [row['holiday_date'].strftime('%d/%m') for row in recurring_holidays_rows]

    # Obtener rangos de vacaciones existentes del usuario actual (para validación visual)
    existing_requests = db.execute("""
        SELECT start_date, end_date 
        FROM vacation_requests 
        WHERE employee_id = ? AND status IN ('Pendiente', 'Aprobado por Jefe', 'Aprobado por RRHH', 'Activo')
    """, (employee_id,)).fetchall()
    
    existing_ranges = []
    for req in existing_requests:
        existing_ranges.append({'start': req['start_date'].strftime('%d/%m/%Y'), 'end': req['end_date'].strftime('%d/%m/%Y')})

    # Obtener vacaciones de todos los empleados para validación de reemplazo (Client-side)
    all_vacations_rows = db.execute("""
        SELECT e.full_name, vr.start_date, vr.end_date
        FROM vacation_requests vr
        JOIN employees e ON vr.employee_id = e.id
        WHERE vr.status IN ('Aprobado por RRHH', 'Activo')
    """).fetchall()
    
    employee_vacations = {}
    for row in all_vacations_rows:
        name = row['full_name']
        if name not in employee_vacations:
            employee_vacations[name] = []
        employee_vacations[name].append({
            'start': row['start_date'].strftime('%d/%m/%Y'),
            'end': row['end_date'].strftime('%d/%m/%Y')
        })

    # Obtener compromisos donde el usuario actual es reemplazo
    current_user_name = session.get("full_name")
    if not current_user_name:
        curr_emp = db.execute("SELECT full_name FROM employees WHERE id = ?", (employee_id,)).fetchone()
        current_user_name = curr_emp['full_name']

    my_commitments_rows = db.execute("""
        SELECT start_date, end_date
        FROM vacation_requests
        WHERE replacement_name = ? AND status IN ('Aprobado por RRHH', 'Activo')
    """, (current_user_name,)).fetchall()
    
    my_commitments = []
    for row in my_commitments_rows:
        my_commitments.append({
            'start': row['start_date'].strftime('%d/%m/%Y'),
            'end': row['end_date'].strftime('%d/%m/%Y')
        })

    if request.method == "POST":
        request_type = request.form["request_type"]
        leave_type_id = request.form.get("leave_type_id")
        start_date_str = request.form.get("start_date")
        replacement_name = request.form.get("replacement_name")
        start_time = request.form.get("start_time")
        end_time = request.form.get("end_time")

        # Obtener configuración del tipo de licencia seleccionado
        selected_leave = db.execute("SELECT * FROM leave_types WHERE id = ?", (leave_type_id,)).fetchone()
        if not selected_leave:
            flash("Tipo de licencia inválido.", "danger")
            return redirect(url_for("vacation_routes.new"))

        if request_type == "HalfDay":
            end_date_str = start_date_str
            half_day_turn = request.form.get("half_day_turn")
            if half_day_turn:
                start_time = half_day_turn
        else:
            if selected_leave['consumption_type'] == 'Fixed':
                # Lógica para licencias fijas (Corridos):
                # 1. Siempre es FullDay
                # 2. Se usa la totalidad del saldo disponible
                # 3. La fecha fin se calcula automáticamente
                request_type = "FullDay"
                current_balance = balances_map.get(int(leave_type_id), 0)
                
                try:
                    start_dt = datetime.strptime(start_date_str, "%d/%m/%Y")
                    # Calcular fecha fin basada en el saldo total (días corridos)
                    end_dt = start_dt + timedelta(days=max(0, current_balance - 1))
                    end_date_str = end_dt.strftime("%d/%m/%Y")
                except (ValueError, TypeError):
                    flash("Fecha de inicio inválida.", "danger")
                    return redirect(url_for("vacation_routes.new"))
            else:
                end_date_str = request.form.get("end_date")

        try:
            if not start_date_str or not end_date_str:
                raise ValueError("Las fechas no pueden estar vacías.")
            
            start_date = datetime.strptime(start_date_str, "%d/%m/%Y").date()
            end_date = datetime.strptime(end_date_str, "%d/%m/%Y").date()

        except (ValueError, TypeError):
            flash("Formato de fecha inválido. Por favor, usa DD/MM/YYYY.", "danger")
            return redirect(url_for("vacation_routes.new"))

        if end_date < start_date:
            flash("La fecha de fin no puede ser anterior a la fecha de inicio.", "danger")
            return redirect(url_for("vacation_routes.new"))

        days_requested = 0
        if request_type == "FullDay":
            if selected_leave['consumption_type'] == 'Fixed':
                # Usar la totalidad del saldo
                days_requested = balances_map.get(int(leave_type_id), 0)
            else:
                days_requested = calculate_working_days(start_date, end_date)
        elif request_type == "HalfDay":
            days_requested = 0.5

        if days_requested <= 0:
            flash("No has seleccionado días laborables. Revisa las fechas.", "warning")
            return redirect(url_for("vacation_routes.new"))

        # Verificar si requiere saldo
        selected_leave = db.execute("SELECT requires_balance FROM leave_types WHERE id = ?", (leave_type_id,)).fetchone()
        requires_balance = selected_leave['requires_balance'] if selected_leave else 1

        if requires_balance:
            current_balance = balances_map.get(int(leave_type_id), 0)
            if current_balance < days_requested:
                flash(f"No tienes suficientes días disponibles para esta licencia. Saldo: {current_balance}, Solicitados: {days_requested}", "danger")
                return redirect(url_for("vacation_routes.new"))

        # Validación: Verificar si el usuario actual es reemplazo de alguien en esas fechas
        overlap_commitment = db.execute("""
            SELECT id FROM vacation_requests
            WHERE replacement_name = ? 
            AND status IN ('Aprobado por RRHH', 'Activo')
            AND start_date <= ? AND end_date >= ?
        """, (current_user_name, end_date, start_date)).fetchone()
        
        if overlap_commitment:
            flash("No puedes solicitar vacaciones en estas fechas porque eres el reemplazo asignado de otro empleado.", "danger")
            return redirect(url_for("vacation_routes.new"))

        # Validación: Verificar si el reemplazo seleccionado está de vacaciones
        overlap_replacement_vacation = db.execute("""
            SELECT vr.id 
            FROM vacation_requests vr
            JOIN employees e ON vr.employee_id = e.id
            WHERE e.full_name = ?
            AND vr.status IN ('Aprobado por RRHH', 'Activo')
            AND vr.start_date <= ? AND vr.end_date >= ?
        """, (replacement_name, end_date, start_date)).fetchone()

        if overlap_replacement_vacation:
            flash(f"El empleado {replacement_name} está de vacaciones en el rango seleccionado y no puede ser tu reemplazo.", "danger")
            return redirect(url_for("vacation_routes.new"))

        # Manejo de Adjunto Obligatorio
        attachment_path = None
        if selected_leave['requires_attachment']:
            if 'attachment' not in request.files:
                flash("Este tipo de licencia requiere un documento adjunto.", "danger")
                return redirect(url_for("vacation_routes.new"))
            
            file = request.files['attachment']
            if file.filename == '':
                flash("No se seleccionó ningún archivo adjunto.", "danger")
                return redirect(url_for("vacation_routes.new"))
            
            if file:
                filename = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
                file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                attachment_path = filename

        db.execute(
            """
            INSERT INTO vacation_requests (employee_id, leave_type_id, start_date, end_date, start_time, end_time, request_type, days_requested, replacement_name, attachment_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (employee_id, leave_type_id, start_date, end_date, start_time, end_time, request_type, days_requested, replacement_name, attachment_path)
        )
        db.commit()

        # --- NOTIFICACIÓN: Al Jefe Directo ---
        try:
            manager = db.execute(
                "SELECT m.email, m.full_name, e.email as emp_email FROM employees e JOIN employees m ON e.manager_id = m.id WHERE e.id = ?", 
                (employee_id,)
            ).fetchone()
            
            employee_name = session.get("full_name")
            if manager and manager['email']:
                subject = f"Nueva Solicitud de Vacaciones: {employee_name}"
                body = f"Estimado/a {manager['full_name']},\n\nEl empleado {employee_name} ha solicitado vacaciones con los siguientes datos:\n\n- Inicio: {start_date.strftime('%d/%m/%Y')}\n- Fin: {end_date.strftime('%d/%m/%Y')}\n- Días: {days_requested}\n\nPor favor ingresa al sistema para aprobar o rechazar."
                # Actor (Empleado) en CC
                cc_list = [manager['emp_email']] if manager['emp_email'] else []
                send_email(subject, [manager['email']], body, cc=cc_list)
        except Exception as e:
            print(f"Error enviando email al jefe: {e}")
        
        flash("Tu solicitud de vacaciones ha sido enviada correctamente.", "success")
        return redirect(url_for("main.dashboard"))

    return render_template("requests/new_request_form.html", 
                           total_balance=total_balance,
                           balances_map=balances_map,
                           leave_types=leave_types,
                           employees=employees,
                           holidays_list=holidays_list,
                           working_saturdays=working_saturdays,
                           employee_vacations=employee_vacations,
                           my_commitments=my_commitments,
                           existing_ranges=existing_ranges,
                           recurring_holidays=recurring_holidays)

@bp.route("/manage")
def manage():
    if session.get("base_role") not in ["Jefe", "RRHH", "Asistente RRHH"]:
        flash("Acceso no autorizado.", "danger")
        return redirect(url_for("main.dashboard"))
    
    manager_id = session["user_id"]
    db = get_db()
    
    # Mostrar pendientes de aprobación Y pendientes de anulación por jefe
    team_requests = db.execute(
        """
        SELECT vr.id, vr.start_date, vr.end_date, vr.days_requested, vr.status, vr.request_type, vr.start_time, vr.replacement_name, e.full_name
        FROM vacation_requests vr
        JOIN employees e ON vr.employee_id = e.id
        WHERE e.manager_id = ? AND (vr.status = 'Pendiente' OR vr.status = 'Anulación Pendiente Jefe')
        ORDER BY vr.request_date
        """,
        (manager_id,)
    ).fetchall()
    
    return render_template("requests/manage_requests.html", requests=team_requests)

@bp.route('/approve/<int:request_id>', methods=('POST',))
def approve(request_id):
    if session.get("base_role") not in ["Jefe", "RRHH"]:
        flash("Acceso no autorizado.", "danger")
        return redirect(url_for("main.dashboard"))

    manager_id = session["user_id"]
    db = get_db()
    
    request_to_approve = db.execute(
        "SELECT id, hr_approval_date FROM vacation_requests WHERE id = ? AND employee_id IN (SELECT id FROM employees WHERE manager_id = ?)",
        (request_id, manager_id)
    ).fetchone()

    if request_to_approve:
        # Si la solicitud fue creada por RRHH (tiene hr_approval_date), la aprobación del jefe es la final.
        if request_to_approve['hr_approval_date']:
            new_status = 'Aprobado por RRHH'
            flash_message = "Solicitud aprobada. Al haber sido creada por RRHH, la aprobación es final y la solicitud queda activa."
        else:
            new_status = 'Aprobado por Jefe'
            flash_message = "Solicitud aprobada. Pasa a RRHH para la aprobación final."

        db.execute(
            "UPDATE vacation_requests SET status = ?, manager_approval_date = ? WHERE id = ?",
            (new_status, datetime.now(), request_id)
        )
        db.commit()

        # --- NOTIFICACIÓN: A RRHH y Empleado (Actor: Jefe en CC) ---
        try:
            # Obtener emails de usuarios con rol base RRHH
            hr_users = db.execute("SELECT e.email FROM employees e JOIN roles r ON e.role = r.name WHERE r.base_role = 'RRHH' AND e.is_active = 1").fetchall()
            recipients = [u['email'] for u in hr_users if u['email']]
            
            req_info = db.execute("SELECT e.full_name, e.email, vr.start_date, vr.end_date, vr.days_requested FROM vacation_requests vr JOIN employees e ON vr.employee_id = e.id WHERE vr.id = ?", (request_id,)).fetchone()
            
            # Agregar email del empleado a destinatarios
            if req_info and req_info['email']:
                recipients.append(req_info['email'])

            # Obtener email del Jefe (Actor) para CC
            manager_email = db.execute("SELECT email FROM employees WHERE id = ?", (manager_id,)).fetchone()
            cc_list = [manager_email['email']] if manager_email and manager_email['email'] else []
            
            if recipients and req_info:
                manager_name = session.get("full_name")
                if new_status == 'Aprobado por RRHH':
                    subject = f"Solicitud Aprobada Final (Creada por RRHH): {req_info['full_name']}"
                    body = f"La solicitud de vacaciones de {req_info['full_name']} ha sido aprobada por el jefe {manager_name}. Al haber sido iniciada por RRHH, esta aprobación es final y la solicitud está activa."
                else:
                    subject = f"Solicitud Aprobada por Jefe: {req_info['full_name']}"
                    body = f"Su solicitud de vacaciones con los siguientes datos:\n\n- Empleado: {req_info['full_name']}\n- Desde: {req_info['start_date'].strftime('%d/%m/%Y')}\n- Hasta: {req_info['end_date'].strftime('%d/%m/%Y')}\n- Días: {req_info['days_requested']}\n\nFueron aprobados por el jefe {manager_name} y queda pendiente de aprobación por parte de RRHH."
                
                send_email(subject, recipients, body, cc=cc_list)
        except Exception as e:
            print(f"Error enviando email a RRHH: {e}")

        flash(flash_message, "success")
    else:
        flash("No se pudo encontrar la solicitud o no tienes permiso para esta acción.", "danger")
        
    return redirect(url_for("vacation_routes.manage"))

@bp.route('/reject/<int:request_id>', methods=('POST',))
def reject(request_id):
    if session.get("base_role") not in ["Jefe", "RRHH"]:
        flash("Acceso no autorizado.", "danger")
        return redirect(url_for("main.dashboard"))

    manager_id = session["user_id"]
    db = get_db()

    request_to_reject = db.execute(
        "SELECT id FROM vacation_requests WHERE id = ? AND employee_id IN (SELECT id FROM employees WHERE manager_id = ?)",
        (request_id, manager_id)
    ).fetchone()

    if request_to_reject:
        db.execute("UPDATE vacation_requests SET status = 'Rechazado' WHERE id = ?", (request_id,))
        db.commit()

        # --- NOTIFICACIÓN: Al Empleado ---
        try:
            req_info = db.execute("SELECT e.email, e.full_name FROM vacation_requests vr JOIN employees e ON vr.employee_id = e.id WHERE vr.id = ?", (request_id,)).fetchone()
            
            # Obtener email del Jefe (Actor) para CC
            manager_email = db.execute("SELECT email FROM employees WHERE id = ?", (manager_id,)).fetchone()
            cc_list = [manager_email['email']] if manager_email and manager_email['email'] else []

            if req_info and req_info['email']:
                subject = "Solicitud de Vacaciones Rechazada"
                body = f"Estimado/a {req_info['full_name']},\n\nSu solicitud de vacaciones ha sido rechazada por su jefe directo."
                send_email(subject, [req_info['email']], body, cc=cc_list)
        except Exception as e:
            print(f"Error enviando email al empleado: {e}")

        flash("La solicitud ha sido rechazada.", "info")
    else:
        flash("No se pudo encontrar la solicitud o no tienes permiso para esta acción.", "danger")

    return redirect(url_for("vacation_routes.manage"))

@bp.route('/request_cancellation/<int:request_id>', methods=('POST',))
def request_cancellation(request_id):
    db = get_db()
    req = db.execute(
        'SELECT id FROM vacation_requests WHERE id = ? AND employee_id = ? AND status = ?',
        (request_id, session['user_id'], 'Aprobado por RRHH')
    ).fetchone()

    reason = request.form.get('cancellation_reason')
    if not reason:
        flash("Es obligatorio indicar el motivo de la anulación.", "danger")
        return redirect(url_for('main.dashboard'))

    if req:
        db.execute(
            "UPDATE vacation_requests SET status = 'Anulación Pendiente Jefe', cancellation_reason = ? WHERE id = ?",
            (reason, request_id)
        )
        db.commit()

        # --- NOTIFICACIÓN: Al Jefe Directo (Solicitud de Anulación) ---
        try:
            manager = db.execute("SELECT m.email, m.full_name, e.email as emp_email FROM employees e JOIN employees m ON e.manager_id = m.id WHERE e.id = ?", (session['user_id'],)).fetchone()
            employee_name = session.get("full_name")
            
            cc_list = [manager['emp_email']] if manager['emp_email'] else []

            if manager and manager['email']:
                subject = f"Solicitud de Anulación de Vacaciones: {employee_name}"
                body = f"Estimado/a {manager['full_name']},\n\nEl empleado {employee_name} ha solicitado ANULAR sus vacaciones aprobadas.\nMotivo: {reason}\n\nPor favor ingresa al sistema para gestionar esta anulación."
                send_email(subject, [manager['email']], body, cc=cc_list)
        except Exception as e:
            print(f"Error enviando email de anulación al jefe: {e}")

        flash('Se ha solicitado la anulación. Debe ser aprobada por tu Jefe y luego por RRHH.', 'info')
    else:
        flash('No se puede solicitar la anulación para esta solicitud.', 'danger')
    
    return redirect(url_for('main.dashboard'))

@bp.route('/approve_cancellation_manager/<int:request_id>', methods=('POST',))
def approve_cancellation_manager(request_id):
    if session.get("base_role") not in ["Jefe", "RRHH"]:
        flash("Acceso no autorizado.", "danger")
        return redirect(url_for("main.dashboard"))

    manager_id = session["user_id"]
    db = get_db()
    
    req = db.execute(
        "SELECT id FROM vacation_requests WHERE id = ? AND employee_id IN (SELECT id FROM employees WHERE manager_id = ?) AND status = 'Anulación Pendiente Jefe'",
        (request_id, manager_id)
    ).fetchone()

    if req:
        db.execute(
            "UPDATE vacation_requests SET status = 'Anulación Pendiente RRHH' WHERE id = ?",
            (request_id,)
        )
        db.commit()

        # --- NOTIFICACIÓN: A RRHH (Anulación aprobada por Jefe) ---
        try:
            hr_users = db.execute("SELECT e.email FROM employees e JOIN roles r ON e.role = r.name WHERE r.base_role = 'RRHH' AND e.is_active = 1").fetchall()
            recipients = [u['email'] for u in hr_users if u['email']]
            req_info = db.execute("SELECT e.full_name, e.email FROM vacation_requests vr JOIN employees e ON vr.employee_id = e.id WHERE vr.id = ?", (request_id,)).fetchone()
            
            if req_info and req_info['email']:
                recipients.append(req_info['email'])

            manager_email = db.execute("SELECT email FROM employees WHERE id = ?", (manager_id,)).fetchone()
            cc_list = [manager_email['email']] if manager_email and manager_email['email'] else []

            if recipients and req_info:
                subject = f"Anulación Aprobada por Jefe: {req_info['full_name']}"
                body = f"La solicitud de ANULACIÓN de vacaciones de {req_info['full_name']} ha sido aprobada por su jefe y requiere confirmación de RRHH para devolver el saldo."
                send_email(subject, recipients, body, cc=cc_list)
        except Exception as e:
            print(f"Error enviando email de anulación a RRHH: {e}")

        flash("Anulación aprobada por Jefe. Pendiente de RRHH.", "success")
    else:
        flash("No se pudo procesar la anulación.", "danger")
        
    return redirect(url_for("vacation_routes.manage"))

@bp.route('/print/<int:request_id>')
def print_request(request_id):
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    db = get_db()
    req = db.execute(
        "SELECT vr.*, e.full_name, e.department, e.job_title FROM vacation_requests vr JOIN employees e ON vr.employee_id = e.id WHERE vr.id = ?", 
        (request_id,)
    ).fetchone()
    return render_template("requests/print_request.html", req=req, now=datetime.now())

@bp.route('/uploads/<filename>')
def download_attachment(filename):
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)