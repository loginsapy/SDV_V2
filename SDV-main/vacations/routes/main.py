# vacations/routes/main.py (CORREGIDO)
from flask import Blueprint, render_template, session, redirect, url_for, json
from datetime import datetime, date, timedelta
from ..db import get_db
from ..utils import get_paraguay_holidays

bp = Blueprint('main', __name__)

@bp.route('/')
def index():
    return redirect(url_for('main.dashboard'))

@bp.route('/dashboard')
def dashboard():
    if "user_id" not in session:
        return redirect(url_for('auth.login'))

    role = session.get("base_role", "").lower()
    if role == "asistente rrhh":
        template_name = "rrhh/dashboard.html"
    else:
        template_name = f"{role}/dashboard.html"
    
    
    user_info = {
        "name": session.get("full_name"),
        "role": session.get("role")
    }

    db = get_db()

    # Actualizar estados de solicitudes
    today = date.today()
    # 1. Aprobado por RRHH -> Activo (si ya llegó la fecha de inicio)
    db.execute(
        "UPDATE vacation_requests SET status = 'Activo' WHERE status = 'Aprobado por RRHH' AND start_date <= ? AND end_date >= ?",
        (today, today)
    )
    # 2. Activo/Aprobado -> Finalizado (si ya pasó la fecha de fin)
    db.execute(
        "UPDATE vacation_requests SET status = 'Finalizado' WHERE (status = 'Aprobado por RRHH' OR status = 'Activo') AND end_date < ?",
        (today,)
    )
    db.commit()

    approved_requests = db.execute(
        """
        SELECT e.full_name, vr.start_date, vr.end_date, lt.name as type_name
        FROM vacation_requests vr
        JOIN employees e ON vr.employee_id = e.id
        LEFT JOIN leave_types lt ON vr.leave_type_id = lt.id
        WHERE vr.status IN ('Aprobado por RRHH', 'Activo', 'Finalizado')
        """
    ).fetchall()

    calendar_events = []
    for req in approved_requests:
        calendar_events.append({
            'title': f"{req['full_name']} ({req['type_name'] or 'Vacaciones'})",
            'start': req['start_date'].strftime('%Y-%m-%d'),
            'end': (req['end_date'] + timedelta(days=1)).strftime('%Y-%m-%d'),
            'allDay': True
        })

    # Agregar Sábados LIBRES al calendario del dashboard
    saturdays = db.execute("SELECT effective_date FROM saturday_config WHERE is_working = 0").fetchall()
    for sat in saturdays:
        calendar_events.append({
            'title': 'Sábado Libre',
            'start': sat['effective_date'].strftime('%Y-%m-%d'),
            'allDay': True,
            'display': 'background',
            'backgroundColor': '#ffc107'
        })

    # Agregar Feriados (Nacionales y Personalizados)
    holidays = get_paraguay_holidays()
    for date_obj, name in holidays.items():
        calendar_events.append({
            'title': name,
            'start': date_obj.strftime('%Y-%m-%d'),
            'allDay': True,
            'display': 'background',
            'backgroundColor': '#ffc107'
        })

    if session.get("base_role") in ["Empleado", "Jefe", "RRHH", "Asistente RRHH"]:
        employee_id = session["user_id"]

        # --- LÓGICA CORREGIDA ---
        # Se obtienen todos los periodos del empleado para mostrar en la tabla.
        periods = db.execute(
            """
            SELECT vp.id, vp.year, vp.total_days_accrued, vp.days_taken, lt.name as leave_name
            FROM vacation_periods vp
            LEFT JOIN leave_types lt ON vp.leave_type_id = lt.id
            WHERE vp.employee_id = ? 
            ORDER BY vp.year ASC
            """,
            (employee_id,)
        ).fetchall()
        
        requests_raw = db.execute(
            """
            SELECT vr.*, lt.name as leave_name 
            FROM vacation_requests vr 
            LEFT JOIN leave_types lt ON vr.leave_type_id = lt.id
            WHERE employee_id = ? 
            ORDER BY request_date DESC
            """,
            (employee_id,)
        ).fetchall()
        
        pending_days_result = db.execute(
            "SELECT SUM(days_requested) FROM vacation_requests WHERE employee_id = ? AND (status = 'Pendiente' OR status = 'Aprobado por Jefe' OR status = 'Anulación Pendiente Jefe')",
            (employee_id,)
        ).fetchone()
        pending_days = pending_days_result[0] if pending_days_result and pending_days_result[0] is not None else 0
        
        is_also_manager = False
        if session.get("base_role") in ["RRHH", "Asistente RRHH"]:
            team_count_result = db.execute(
                "SELECT COUNT(id) FROM employees WHERE manager_id = ?",
                (employee_id,)
            ).fetchone()
            if team_count_result and team_count_result[0] > 0:
                is_also_manager = True
        
        today = date.today()
        requests_processed = []
        for req_row in requests_raw:
            req_dict = dict(req_row)
            start_date_obj = req_dict['start_date']
            
            # La base de datos ya devuelve objetos date gracias a los conversores
            if isinstance(start_date_obj, date):
                req_dict['can_be_cancelled'] = (req_dict['status'] == 'Aprobado por RRHH' and start_date_obj > today)
            else:
                req_dict['can_be_cancelled'] = False # No se puede determinar si no es una fecha

            requests_processed.append(req_dict)
        
        return render_template(template_name, user=user_info, periods=periods, requests=requests_processed, pending_days=pending_days, is_also_manager=is_also_manager, calendar_events=json.dumps(calendar_events))

    # Lógica específica para el Dashboard de RRHH (KPIs)
    if session.get("base_role") in ["RRHH", "Asistente RRHH"]:
        today = date.today()
        
        # 1. Solicitudes Pendientes (Requieren Acción de RRHH)
        # Consideramos: Aprobado por Jefe (esperando RRHH) y Anulación Pendiente RRHH
        pending_count = db.execute(
            "SELECT COUNT(*) FROM vacation_requests WHERE status IN ('Aprobado por Jefe', 'Anulación Pendiente RRHH')"
        ).fetchone()[0]

        # 2. En Vacaciones Hoy
        active_vacations_count = db.execute(
            "SELECT COUNT(*) FROM vacation_requests WHERE status = 'Activo'",
            ()
        ).fetchone()[0]

        # 3. Próximas Salidas (Próximos 7 días)
        next_week = today + timedelta(days=7)
        upcoming_vacations_count = db.execute(
            "SELECT COUNT(*) FROM vacation_requests WHERE status = 'Aprobado por RRHH' AND start_date > ? AND start_date <= ?",
            (today, next_week)
        ).fetchone()[0]

        # 4. Tasa de Aprobación (Últimos 30 días)
        last_month = today - timedelta(days=30)
        stats = db.execute(
            "SELECT status, COUNT(*) as cnt FROM vacation_requests WHERE hr_approval_date >= ? GROUP BY status",
            (last_month,)
        ).fetchall()
        
        approved = sum(row['cnt'] for row in stats if row['status'] in ['Aprobado por RRHH', 'Activo', 'Finalizado'])
        rejected = sum(row['cnt'] for row in stats if row['status'] == 'Rechazado')
        total_processed = approved + rejected
        approval_rate = int((approved / total_processed) * 100) if total_processed > 0 else 100

        return render_template(template_name, user=user_info, calendar_events=json.dumps(calendar_events),
                               pending_count=pending_count,
                               active_vacations_count=active_vacations_count,
                               upcoming_vacations_count=upcoming_vacations_count,
                               approval_rate=approval_rate,
                               is_also_manager=False) # RRHH dashboard doesn't typically show manager specific team view in main area unless requested

    return render_template(template_name, user=user_info, calendar_events=json.dumps(calendar_events))