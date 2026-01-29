
import sqlite3
from flask import current_app, g
from datetime import datetime, date, timedelta
from werkzeug.security import generate_password_hash

def adapt_datetime_iso(val):
    return val.isoformat()
def convert_timestamp(val):
    return datetime.fromisoformat(val.decode())
def convert_date(val):
    return date.fromisoformat(val.decode())

sqlite3.register_adapter(datetime, adapt_datetime_iso)
sqlite3.register_converter("timestamp", convert_timestamp)
sqlite3.register_adapter(date, adapt_datetime_iso)
sqlite3.register_converter("date", convert_date)

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(
            current_app.config['DATABASE'],
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row
    return g.db

def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def calculate_accrued_days(hire_date):
    today = date.today()
    seniority_years = (today - hire_date).days / 365.25
    if seniority_years <= 5:
        return 12
    elif seniority_years <= 10:
        return 18
    else:
        return 30

def setup_database():
    print("Configurando la base de datos...")
    db = get_db()
    cur = db.cursor()

    # --- MIGRACIÓN AUTOMÁTICA: Eliminar restricción CHECK obsoleta en status ---
    try:
        schema_row = cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='vacation_requests'").fetchone()
        if schema_row:
            schema_sql = schema_row['sql']
            # Si existe la restricción CHECK que limita los estados
            if "CHECK" in schema_sql and "status IN" in schema_sql:
                print("Migrando tabla vacation_requests para permitir nuevos estados...")
                cur.execute("DROP TABLE IF EXISTS vacation_requests_old")
                cur.execute("ALTER TABLE vacation_requests RENAME TO vacation_requests_old")
                
                # Crear la tabla nueva sin la restricción
                cur.execute("""
                CREATE TABLE vacation_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_id INTEGER NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    start_time TEXT,
                    end_time TEXT,
                    request_type TEXT NOT NULL,
                    leave_type_id INTEGER,
                    days_requested REAL NOT NULL,
                    replacement_name TEXT,
                    status TEXT NOT NULL DEFAULT 'Pendiente',
                    cancellation_reason TEXT,
                    interruption_reason TEXT,
                    request_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    manager_approval_date TIMESTAMP,
                    hr_approval_date TIMESTAMP
                );
                """)
                
                # Copiar datos
                old_cols = [row['name'] for row in cur.execute("PRAGMA table_info(vacation_requests_old)").fetchall()]
                new_cols = [row['name'] for row in cur.execute("PRAGMA table_info(vacation_requests)").fetchall()]
                common_cols = [col for col in new_cols if col in old_cols]
                cols_str = ", ".join(common_cols)
                
                cur.execute(f"INSERT INTO vacation_requests ({cols_str}) SELECT {cols_str} FROM vacation_requests_old")
                cur.execute("DROP TABLE vacation_requests_old")
                db.commit()
                print("Migración de esquema completada.")
    except Exception as e:
        print(f"Advertencia en migración: {e}")
    # ---------------------------------------------------------------------------

    # --- MIGRACIÓN AUTOMÁTICA: Eliminar restricción CHECK obsoleta en role (employees) ---
    try:
        schema_row = cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='employees'").fetchone()
        if schema_row:
            schema_sql = schema_row['sql']
            # Si existe la restricción CHECK que limita los roles
            if "CHECK" in schema_sql and "role IN" in schema_sql:
                print("Migrando tabla employees para permitir nuevos roles...")
                cur.execute("DROP TABLE IF EXISTS employees_old")
                cur.execute("ALTER TABLE employees RENAME TO employees_old")
                
                # Crear la tabla nueva sin la restricción
                cur.execute("""
                CREATE TABLE employees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
                    full_name TEXT NOT NULL, email TEXT, hire_date DATE NOT NULL,
                    role TEXT NOT NULL,
                    manager_id INTEGER, department TEXT, job_title TEXT, company TEXT,
                    is_active BOOLEAN DEFAULT 1, is_ad_managed BOOLEAN DEFAULT 0
                );
                """)
                
                # Copiar datos
                old_cols = [row['name'] for row in cur.execute("PRAGMA table_info(employees_old)").fetchall()]
                new_cols = [row['name'] for row in cur.execute("PRAGMA table_info(employees)").fetchall()]
                common_cols = [col for col in new_cols if col in old_cols]
                cols_str = ", ".join(common_cols)
                
                cur.execute(f"INSERT INTO employees ({cols_str}) SELECT {cols_str} FROM employees_old")
                cur.execute("DROP TABLE employees_old")
                db.commit()
                print("Migración de esquema de empleados completada.")
    except Exception as e:
        print(f"Advertencia en migración de empleados: {e}")
    # ---------------------------------------------------------------------------

    # --- MIGRACIÓN AUTOMÁTICA: vacation_periods (Agregar leave_type_id y actualizar UNIQUE) ---
    try:
        # Verificar si ya tiene la columna leave_type_id
        cols = [row['name'] for row in cur.execute("PRAGMA table_info(vacation_periods)").fetchall()]
        if 'leave_type_id' not in cols:
            print("Migrando vacation_periods para soportar tipos de licencia...")
            
            # Obtener ID de 'Vacaciones' para migrar datos existentes
            vac_type = cur.execute("SELECT id FROM leave_types WHERE name = 'Vacaciones'").fetchone()
            vac_type_id = vac_type['id'] if vac_type else 1

            cur.execute("DROP TABLE IF EXISTS vacation_periods_old")
            cur.execute("ALTER TABLE vacation_periods RENAME TO vacation_periods_old")
            
            cur.execute("""
            CREATE TABLE vacation_periods (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                employee_id INTEGER NOT NULL, 
                year INTEGER NOT NULL,
                leave_type_id INTEGER NOT NULL,
                total_days_accrued REAL NOT NULL, 
                days_taken REAL DEFAULT 0.0, 
                adjustment_comment TEXT,
                UNIQUE(employee_id, year, leave_type_id)
            );
            """)
            
            # Copiar datos asumiendo que todo lo anterior era 'Vacaciones'
            cur.execute(f"""
            INSERT INTO vacation_periods (id, employee_id, year, leave_type_id, total_days_accrued, days_taken, adjustment_comment)
            SELECT id, employee_id, year, {vac_type_id}, total_days_accrued, days_taken, adjustment_comment FROM vacation_periods_old
            """)
            cur.execute("DROP TABLE vacation_periods_old")
            db.commit()
    except Exception as e:
        print(f"Advertencia en migración de periodos: {e}")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS employees (
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
        full_name TEXT NOT NULL, email TEXT, hire_date DATE NOT NULL,
        role TEXT NOT NULL,
        manager_id INTEGER, department TEXT, job_title TEXT, company TEXT,
        is_active BOOLEAN DEFAULT 1, is_ad_managed BOOLEAN DEFAULT 0
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS vacation_periods (
        id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id INTEGER NOT NULL, year INTEGER NOT NULL,
        leave_type_id INTEGER NOT NULL,
        total_days_accrued REAL NOT NULL, days_taken REAL DEFAULT 0.0, 
        adjustment_comment TEXT,
        UNIQUE(employee_id, year, leave_type_id)
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS leave_types (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        requires_balance BOOLEAN DEFAULT 1,
        default_days INTEGER DEFAULT 0,
        consumption_type TEXT DEFAULT 'Flexible', -- 'Flexible' (Hábiles) o 'Fixed' (Corridos)
        requires_attachment BOOLEAN DEFAULT 0
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS saturday_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        effective_date DATE NOT NULL,
        is_working BOOLEAN NOT NULL
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS vacation_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL,
        start_date DATE NOT NULL,
        end_date DATE NOT NULL,
        start_time TEXT,
        end_time TEXT,
        request_type TEXT NOT NULL,
        leave_type_id INTEGER,
        days_requested REAL NOT NULL,
        replacement_name TEXT,
        status TEXT NOT NULL DEFAULT 'Pendiente',
        cancellation_reason TEXT,
        interruption_reason TEXT,
        request_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        manager_approval_date TIMESTAMP,
        hr_approval_date TIMESTAMP
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS custom_holidays (
        id INTEGER PRIMARY KEY AUTOINCREMENT, holiday_date DATE NOT NULL UNIQUE, description TEXT NOT NULL,
        is_recurring BOOLEAN DEFAULT 0
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS roles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        base_role TEXT,
        is_system_role BOOLEAN DEFAULT 0
    );
    """)

    # Migración automática: Intentar agregar la columna si no existe (para bases de datos existentes)
    try:
        cur.execute("ALTER TABLE custom_holidays ADD COLUMN is_recurring BOOLEAN DEFAULT 0")
    except sqlite3.OperationalError:
        # La columna ya existe, ignoramos el error
        pass
    try:
        cur.execute("ALTER TABLE vacation_periods ADD COLUMN leave_name TEXT")
    except sqlite3.OperationalError:
        pass

    try:
        cur.execute("ALTER TABLE roles ADD COLUMN base_role TEXT")
    except sqlite3.OperationalError:
        pass

    # Migraciones manuales para columnas nuevas
    try:
        cur.execute("ALTER TABLE vacation_requests ADD COLUMN replacement_name TEXT")
    except sqlite3.OperationalError: pass

    try:
        cur.execute("ALTER TABLE vacation_requests ADD COLUMN start_time TEXT")
    except sqlite3.OperationalError: pass

    try:
        cur.execute("ALTER TABLE vacation_requests ADD COLUMN end_time TEXT")
    except sqlite3.OperationalError: pass
    
    try:
        cur.execute("ALTER TABLE vacation_requests ADD COLUMN leave_type_id INTEGER")
    except sqlite3.OperationalError: pass

    try:
        cur.execute("ALTER TABLE vacation_periods ADD COLUMN adjustment_comment TEXT")
    except sqlite3.OperationalError: pass

    try:
        cur.execute("ALTER TABLE vacation_requests ADD COLUMN cancellation_reason TEXT")
    except sqlite3.OperationalError: pass

    try:
        cur.execute("ALTER TABLE vacation_requests ADD COLUMN interruption_reason TEXT")
    except sqlite3.OperationalError: pass

    try:
        cur.execute("ALTER TABLE vacation_requests ADD COLUMN modification_reason TEXT")
    except sqlite3.OperationalError: pass
    
    try:
        cur.execute("ALTER TABLE vacation_requests ADD COLUMN attachment_path TEXT")
    except sqlite3.OperationalError: pass

    # Columnas nuevas para leave_types
    try:
        cur.execute("ALTER TABLE leave_types ADD COLUMN default_days INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass
    try:
        cur.execute("ALTER TABLE leave_types ADD COLUMN consumption_type TEXT DEFAULT 'Flexible'")
    except sqlite3.OperationalError: pass
    try:
        cur.execute("ALTER TABLE leave_types ADD COLUMN requires_attachment BOOLEAN DEFAULT 0")
    except sqlite3.OperationalError: pass

    # Inicializar Tipos de Licencia por defecto
    if cur.execute("SELECT COUNT(*) FROM leave_types").fetchone()[0] == 0:
        cur.execute("INSERT INTO leave_types (name, requires_balance, consumption_type) VALUES ('Vacaciones', 1, 'Flexible')")

    # Inicializar Roles por defecto
    default_roles = ['Empleado', 'Jefe', 'RRHH', 'Asistente RRHH']
    for role in default_roles:
        try:
            cur.execute("INSERT INTO roles (name, base_role, is_system_role) VALUES (?, ?, 1)", (role, role))
        except sqlite3.IntegrityError:
            # Asegurar que los roles del sistema tengan su base_role configurado (para migraciones)
            cur.execute("UPDATE roles SET base_role = ? WHERE name = ? AND base_role IS NULL", (role, role))

    cur.execute("""
    CREATE TABLE IF NOT EXISTS email_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        MAIL_SERVER TEXT, MAIL_PORT INTEGER, MAIL_USE_TLS BOOLEAN,
        MAIL_USE_SSL BOOLEAN, MAIL_USERNAME TEXT, MAIL_PASSWORD TEXT,
        MAIL_DEFAULT_SENDER TEXT
    );
    """)

    # Inicializar configuración de correo por defecto (SMTP2GO)
    if cur.execute("SELECT COUNT(*) FROM email_config").fetchone()[0] == 0:
        cur.execute("""
        INSERT INTO email_config (MAIL_SERVER, MAIL_PORT, MAIL_USE_TLS, MAIL_USE_SSL, MAIL_USERNAME, MAIL_PASSWORD, MAIL_DEFAULT_SENDER)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ('mail.smtp2go.com', 587, 1, 0, 'evaluacion@olam.com.py', 'bTDulR6hgOLLmwqc', 'evaluacion@olam.com.py'))
        print("Configuración de correo por defecto insertada.")

    cur.execute("SELECT COUNT(*) FROM employees;")
    if cur.fetchone()[0] == 0:
        hashed_password = generate_password_hash('123')
        cur.execute("""
        INSERT INTO employees (username, password, full_name, email, hire_date, role, manager_id, department, job_title, company, is_ad_managed) VALUES
        ('rrhh', ?, 'Recursos Humanos', 'rrhh@empresa.com', '2015-01-15', 'RRHH', NULL, 'Recursos Humanos', 'Analista de RRHH', 'Mi Empresa', 0),
        ('asistente_rrhh', ?, 'Asistente RRHH', 'asistente@empresa.com', '2023-01-15', 'Asistente RRHH', 1, 'Recursos Humanos', 'Asistente', 'Mi Empresa', 0),
        ('jefe_ventas', ?, 'Juan Perez (Jefe)', 'jperez@empresa.com', '2018-03-20', 'Jefe', 1, 'Ventas', 'Jefe de Ventas', 'Mi Empresa', 0),
        ('empleado1', ?, 'Ana Lopez', 'alopez@empresa.com', '2022-06-01', 'Empleado', 2, 'Ventas', 'Vendedora', 'Mi Empresa', 0),
        ('empleado2', ?, 'Carlos Vera', 'cvera@empresa.com', '2016-11-10', 'Empleado', 2, 'Marketing', 'Analista de Marketing', 'Mi Empresa', 0);
        """, (hashed_password, hashed_password, hashed_password, hashed_password))
        print("Datos de ejemplo de empleados insertados.")

        print("Generando periodos y solicitudes de ejemplo...")
        current_year = datetime.now().year
        
        employees_for_period = cur.execute("SELECT id, hire_date FROM employees").fetchall()
        for emp in employees_for_period:
            hire_date_obj = emp['hire_date']
            accrued_days = calculate_accrued_days(hire_date_obj)
            cur.execute(
                "INSERT INTO vacation_periods (employee_id, year, leave_type_id, total_days_accrued) VALUES (?, ?, (SELECT id FROM leave_types WHERE name='Vacaciones'), ?)",
                (emp['id'], current_year, accrued_days)
            )

        ana_id = cur.execute("SELECT id FROM employees WHERE username = 'empleado1'").fetchone()['id']
        carlos_id = cur.execute("SELECT id FROM employees WHERE username = 'empleado2'").fetchone()['id']

        cur.execute("""
            INSERT INTO vacation_requests (employee_id, start_date, end_date, request_type, days_requested, status, request_date, manager_approval_date, hr_approval_date)
            VALUES (?, ?, ?, 'FullDay', 5, 'Aprobado por RRHH', ?, ?, ?)
        """, (ana_id, date(datetime.now().year, 2, 10), date(datetime.now().year, 2, 14), datetime(datetime.now().year, 1, 15), datetime.now(), datetime.now()))
        
        cur.execute("""
            INSERT INTO vacation_requests (employee_id, start_date, end_date, request_type, days_requested, status, request_date)
            VALUES (?, ?, ?, 'FullDay', 2, 'Pendiente', ?)
        """, (ana_id, date(datetime.now().year, 8, 1), date(datetime.now().year, 8, 2), datetime(datetime.now().year, 7, 1)))

        cur.execute("""
            INSERT INTO vacation_requests (employee_id, start_date, end_date, request_type, days_requested, status, request_date, manager_approval_date, hr_approval_date)
            VALUES (?, ?, ?, 'FullDay', 3, 'Aprobado por RRHH', ?, ?, ?)
        """, (carlos_id, date(datetime.now().year, 3, 5), date(datetime.now().year, 3, 7), datetime(datetime.now().year, 2, 20), datetime.now(), datetime.now()))
        
        # Actualizar saldos manualmente para los datos de ejemplo
        db.execute("UPDATE vacation_periods SET days_taken = 5 WHERE employee_id = ?", (ana_id,))
        db.execute("UPDATE vacation_periods SET days_taken = 3 WHERE employee_id = ?", (carlos_id,))
        
        print("Datos de ejemplo de solicitudes insertados.")

    db.commit()

def get_email_config():
    db = get_db()
    config_row = db.execute("SELECT * FROM email_config ORDER BY id DESC LIMIT 1").fetchone()

    if config_row:
        return {key.upper(): config_row[key] for key in config_row.keys() if key != 'id'}
    return {}

def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        setup_database()