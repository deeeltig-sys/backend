from flask import Flask, jsonify
from flask_cors import CORS
from config import Config
from routes.auth import bp as auth_bp
from routes.posts import bp as posts_bp
from routes.reactions import bp as reactions_bp
from routes.comments import bp as comments_bp
from routes.profile import bp as profile_bp
from routes.admin import bp as admin_bp
from routes.app_meta import bp as app_meta_bp
from routes.universities import bp as universities_bp
from routes.reports import bp as reports_bp
from routes.follows import bp as follows_bp
from routes.users import bp as users_bp
from routes.notifications import bp as notifications_bp
from routes.messages import bp as messages_bp
from routes.blocks import bp as blocks_bp
from routes.stats import bp as stats_bp


def create_app():
    Config.validate()
    app = Flask(__name__)
    CORS(app, resources={r"/api/*": {"origins": Config.CORS_ORIGINS}})

    app.register_blueprint(auth_bp)
    app.register_blueprint(posts_bp)
    app.register_blueprint(reactions_bp)
    app.register_blueprint(comments_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(app_meta_bp)
    app.register_blueprint(universities_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(follows_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(messages_bp)
    app.register_blueprint(blocks_bp)
    app.register_blueprint(stats_bp)

    @app.get("/")
    def health():
        return jsonify({"status": "ok", "service": "campusmeet-backend"}), 200

    @app.errorhandler(404)
    def not_found(_e):
        return jsonify({"error": "not found"}), 404

    @app.errorhandler(500)
    def server_error(_e):
        return jsonify({"error": "internal server error"}), 500

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=Config.FLASK_ENV != "production")