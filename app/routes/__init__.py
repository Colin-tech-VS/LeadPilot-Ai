from app.routes.appointments import appointments_bp
from app.routes.auth import auth_bp
from app.routes.billing import billing_bp
from app.routes.health import health_bp
from app.routes.leads import leads_bp
from app.routes.quotes import quotes_bp
from app.routes.tenant import tenant_bp
from app.routes.web import web_bp
from app.routes.voice import voice_bp
from app.routes.webhook import webhook_bp


def register_blueprints(app):
    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(tenant_bp)
    app.register_blueprint(leads_bp)
    app.register_blueprint(appointments_bp)
    app.register_blueprint(webhook_bp)
    app.register_blueprint(voice_bp)
    app.register_blueprint(billing_bp)
    app.register_blueprint(quotes_bp)
    app.register_blueprint(web_bp)
