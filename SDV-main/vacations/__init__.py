# vacations/extensions.py
# Este archivo centraliza la inicialización de las extensiones de Flask
# para evitar importaciones circulares.
from flask_mail import Mail

mail = Mail()

# -------------------------------------------------------------------

# vacations/__init__.py (ACTUALIZADO)
# Fábrica de la aplicación.

import os
from flask import Flask
from . import utils
# Se importa mail desde el nuevo archivo de extensiones
from .extensions import mail

def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True, template_folder='templates')
    app.config.from_mapping(
        SECRET_KEY=os.environ.get('SECRET_KEY', 'dev_key_secreta_por_defecto'),
        DATABASE=os.path.join(app.instance_path, 'vacaciones.db'),
        AD_CONFIG_PATH=os.path.join(app.instance_path, 'ad_config.json'),
    )

    # Configurar carpeta de subidas
    app.config['UPLOAD_FOLDER'] = os.path.join(app.instance_path, 'uploads')
    try:
        os.makedirs(app.config['UPLOAD_FOLDER'])
    except OSError:
        pass

    # Cargar la configuración de correo desde la base de datos
    with app.app_context():
        from . import db
        db.init_app(app)
        
        email_config = db.get_email_config()
        if email_config:
            app.config.update(email_config)

    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass

    # 1. Inicializar la base de datos y crear las tablas PRIMERO.
    from . import db
    db.init_app(app)
    
    # 2. AHORA que las tablas existen, cargar la configuración de correo.
    # Configuración desde Variables de Entorno (para Producción)
    app.config.update({
        'MAIL_SERVER': os.environ.get('MAIL_SERVER', 'mail.smtp2go.com'),
        'MAIL_PORT': int(os.environ.get('MAIL_PORT', 587)),
        'MAIL_USE_TLS': os.environ.get('MAIL_USE_TLS', 'True') == 'True',
        'MAIL_USE_SSL': os.environ.get('MAIL_USE_SSL', 'False') == 'True',
        'MAIL_USERNAME': os.environ.get('MAIL_USERNAME'),
        'MAIL_PASSWORD': os.environ.get('MAIL_PASSWORD'),
        'MAIL_DEFAULT_SENDER': os.environ.get('MAIL_DEFAULT_SENDER')
    })

    # 3. Inicializar la extensión Mail con la configuración completa.
    mail.init_app(app)

    # 4. Registrar filtros y blueprints.
    app.jinja_env.filters['format_date'] = utils.format_date_filter

    from .routes import auth, main, vacation_routes, hr
    app.register_blueprint(auth.bp)
    app.register_blueprint(main.bp)
    app.register_blueprint(vacation_routes.bp)
    app.register_blueprint(hr.bp)

    app.add_url_rule('/', endpoint='main.index')

    return app