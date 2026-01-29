from datetime import datetime, date, timedelta
from .db import get_db
from flask_mail import Message
from .extensions import mail
from flask import render_template_string, current_app, url_for

def send_email(subject, recipients, body, cc=None):
    try:
        # Generar enlace dinámico al login
        try:
            system_link = url_for('auth.login', _external=True)
        except Exception:
            system_link = "#"

        # Plantilla HTML simple para el correo
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; color: #333; line-height: 1.6; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 8px; background-color: #f9f9f9; }}
                .header {{ background-color: #004AAD; color: white; padding: 10px; text-align: center; border-radius: 8px 8px 0 0; }}
                .content {{ padding: 20px; background-color: white; }}
                .button {{ display: inline-block; padding: 10px 20px; background-color: #009FFD; color: white; text-decoration: none; border-radius: 5px; font-weight: bold; margin-top: 20px; }}
                .footer {{ font-size: 12px; color: #777; text-align: center; margin-top: 20px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>Sistema de Gestión de Vacaciones</h2>
                </div>
                <div class="content">
                    <p>{body.replace(chr(10), '<br>')}</p>
                    <center><a href="{system_link}" class="button">Acceder al Sistema</a></center>
                </div>
                <div class="footer">Este es un mensaje automático, por favor no responder.</div>
            </div>
        </body>
        </html>
        """

        # recipients debe ser una lista de correos
        msg = Message(subject, recipients=recipients, html=html_body, cc=cc) 
        mail.send(msg)
    except Exception as e:
        server = current_app.config.get('MAIL_SERVER', 'localhost (default)')
        port = current_app.config.get('MAIL_PORT', 25)
        print(f"\n--- ERROR DE ENVÍO DE CORREO ---")
        print(f"Intentando conectar a: {server}:{port}")
        print(f"Detalle del error: {e}")
        
        # Sugerencia automática para errores de autenticación
        if "535" in str(e) or "Authentication unsuccessful" in str(e):
            print("\n[PISTA] Error 535: Credenciales inválidas.")
            print("1. Verifica que el correo y la contraseña sean correctos.")
            print("2. Si usas Office 365 o Gmail con verificación en dos pasos (MFA), NO uses tu contraseña normal.")
            print("   Debes generar y usar una 'Contraseña de Aplicación'.")
            print("3. Asegúrate de que 'SMTP Autenticado' esté habilitado para este usuario en el panel de administración de Microsoft 365.")
            
        print(f"--------------------------------\n")

def format_date_filter(date_val, include_time=False):
    if not date_val:
        return ''
    if isinstance(date_val, (datetime, date)):
        fmt = '%d/%m/%Y %H:%M' if include_time else '%d/%m/%Y'
        return date_val.strftime(fmt)
    if isinstance(date_val, str):
        try:
            if ' ' in date_val:
                if '.' in date_val:
                    date_val = date_val.split('.')[0]
                dt_obj = datetime.strptime(date_val, '%Y-%m-%d %H:%M:%S')
                fmt = '%d/%m/%Y %H:%M' if include_time else '%d/%m/%Y'
                return dt_obj.strftime(fmt)
            else:
                dt_obj = datetime.strptime(date_val, '%Y-%m-%d')
                return dt_obj.strftime('%d/%m/%Y')
        except (ValueError, TypeError):
            return date_val
    return date_val

def get_paraguay_holidays(start_date=None, end_date=None):
    """
    Obtiene los feriados personalizados. Si se proveen fechas, proyecta los feriados
    recurrentes para cubrir todo el rango de años.
    """
    py_holidays = {}
    conn = get_db()
    custom_holidays_rows = conn.execute("SELECT holiday_date, description, is_recurring FROM custom_holidays").fetchall()
    
    # Determinar el rango de años a cubrir para los feriados recurrentes
    if start_date and end_date:
        years_to_check = set(range(start_date.year, end_date.year + 1))
    else:
        # Si no se provee un rango, usar el año actual y el siguiente como default
        today = date.today()
        years_to_check = {today.year, today.year + 1}

    for row in custom_holidays_rows:
        h_date = row['holiday_date']
        desc = row['description']
        
        if row['is_recurring']:
            # Si es recurrente, lo proyectamos para todos los años del rango de interés
            for year in years_to_check:
                try:
                    projected_date = date(year, h_date.month, h_date.day)
                    py_holidays[projected_date] = desc
                except ValueError:
                    # Maneja el caso de 29 de febrero en años no bisiestos
                    pass
        else:
            # Si no es recurrente, simplemente lo agregamos
            py_holidays[h_date] = desc
                        
    return py_holidays

def calculate_accrued_days(hire_date):
    today = date.today()
    seniority_years = (today - hire_date).days / 365.25
    if seniority_years <= 5:
        return 12
    elif seniority_years <= 10:
        return 18
    else:
        return 30

def is_working_saturday(check_date):
    """
    Determina si un sábado específico es laboral basado en la configuración cíclica.
    """
    if check_date.weekday() != 5:
        return False
        
    db = get_db()
    # Busca configuración específica para la fecha exacta
    row = db.execute(
        "SELECT is_working FROM saturday_config WHERE effective_date = ?",
        (check_date,)
    ).fetchone()
    
    if row:
        return bool(row['is_working'])

    # Si no hay configuración explícita, asumimos NO laboral (False)
    return False

def calculate_working_days(start_date, end_date):
    # Pasar el rango de fechas para que los feriados recurrentes se calculen correctamente
    py_holidays = get_paraguay_holidays(start_date, end_date)
    days_count = 0
    current_date = start_date
    while current_date <= end_date:
        # Contar días de Lunes a Sábado (weekday 0 a 5), excluyendo Domingos y feriados.
        if current_date.weekday() < 6 and current_date not in py_holidays:
            days_count += 1
        
        current_date += timedelta(days=1)
    return days_count