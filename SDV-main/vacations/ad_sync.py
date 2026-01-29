import sqlite3
from ldap3 import Server, Connection, ALL
from werkzeug.security import generate_password_hash
from datetime import datetime
import os
from flask import current_app

def get_db_connection(db_path):
    """Crea una conexión a la base de datos usando la ruta proporcionada."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def sync_users_from_ad(config):
    """
    Se conecta al Directorio Activo, obtiene los usuarios y actualiza la BD local.
    """
    # --- CORRECCIÓN AQUÍ ---
    # La función ahora obtiene la ruta de la base de datos desde la configuración
    # de la aplicación, en lugar de recibirla como un argumento.
    db_path = current_app.config['DATABASE']

    server = Server(config['server'], port=config['port'], use_ssl=config['use_ssl'], get_info=ALL)
    conn = Connection(server, user=config['user'], password=config['password'], auto_bind=True)

    # Se obtienen los nombres de los atributos desde la configuración para mayor flexibilidad.
    email_attr = config.get('email_attribute', 'mail')
    hire_date_attr = config.get('hire_date_attribute', 'pager')
    job_title_attr = config.get('job_title_attribute', 'title')
    department_attr = config.get('department_attribute', 'department')
    company_attr = config.get('company_attribute', 'company')
    
    # Se construye la lista de atributos a solicitar al AD dinámicamente.
    attributes_to_fetch = [
        'sAMAccountName', 'givenName', 'sn', 'whenCreated',
        email_attr, hire_date_attr, job_title_attr, department_attr, company_attr
    ]

    conn.search(
        search_base=config['search_base'],
        search_filter='(objectClass=person)',
        attributes=attributes_to_fetch
    )

    ad_users = conn.entries
    conn.unbind()

    if not ad_users:
        raise Exception("No se encontraron usuarios en el Directorio Activo con los filtros proporcionados.")

    db_conn = get_db_connection(db_path)
    db_cur = db_conn.cursor()

    # Obtener solo los usuarios gestionados por AD para la comparación
    db_cur.execute("SELECT username FROM employees WHERE is_ad_managed = 1")
    local_ad_usernames = {row['username'] for row in db_cur.fetchall()}
    
    ad_usernames = set()
    created_count = 0
    updated_count = 0

    for user_entry in ad_users:
        username = str(user_entry.sAMAccountName)
        if not username:
            continue
        
        ad_usernames.add(username)
        full_name = f"{user_entry.givenName or ''} {user_entry.sn or ''}".strip()
        
        # Se leen los valores de los atributos dinámicos.
        email = str(user_entry[email_attr]) if user_entry[email_attr] else None
        hire_date_str = str(user_entry[hire_date_attr]) if user_entry[hire_date_attr] else None
        job_title = str(user_entry[job_title_attr]) if user_entry[job_title_attr] else None
        department = str(user_entry[department_attr]) if user_entry[department_attr] else None
        company = str(user_entry[company_attr]) if user_entry[company_attr] else None
        
        # Lógica de fecha de contratación mejorada
        hire_date = None
        if hire_date_str:
            try:
                date_format = config.get('hire_date_format', '%d/%m/%Y')
                hire_date = datetime.strptime(hire_date_str, date_format).date()
            except (ValueError, TypeError):
                print(f"Advertencia: No se pudo procesar la fecha '{hire_date_str}' para el usuario {username} con el formato proporcionado. Se intentará con 'whenCreated'.")
                hire_date = None

        if not hire_date and user_entry.whenCreated:
            hire_date = user_entry.whenCreated.value.date()
        
        if not hire_date:
            hire_date = datetime.now().date()

        db_cur.execute("SELECT id FROM employees WHERE username = ?", (username,))
        existing_user = db_cur.fetchone()

        if existing_user:
            # Si el usuario existe, se actualizan sus datos desde el AD y se marca como gestionado por AD.
            db_cur.execute(
                """
                UPDATE employees 
                SET full_name = ?, email = ?, hire_date = ?, department = ?, job_title = ?, company = ?, is_active = 1, is_ad_managed = 1 
                WHERE id = ?
                """,
                (full_name, email, hire_date, department, job_title, company, existing_user['id'])
            )
            updated_count += 1
        else:
            # Si no existe, se crea con todos los datos del AD, rol por defecto y marcado como gestionado por AD.
            default_password = generate_password_hash(os.urandom(16).hex())
            db_cur.execute(
                """
                INSERT INTO employees (username, password, full_name, email, hire_date, role, department, job_title, company, is_active, is_ad_managed) 
                VALUES (?, ?, ?, ?, ?, 'Empleado', ?, ?, ?, 1, 1)
                """,
                (username, default_password, full_name, email, hire_date, department, job_title, company)
            )
            created_count += 1

    # Desactivar usuarios locales gestionados por AD que ya no están en el AD
    deactivated_count = 0
    users_to_deactivate = local_ad_usernames - ad_usernames
    for username in users_to_deactivate:
        db_cur.execute("UPDATE employees SET is_active = 0 WHERE username = ? AND is_ad_managed = 1", (username,))
        deactivated_count += 1

    db_conn.commit()
    db_conn.close()

    return {
        "created": created_count,
        "updated": updated_count,
        "deactivated": deactivated_count
    }