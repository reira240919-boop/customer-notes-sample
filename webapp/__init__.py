import os
import resource

from dotenv import load_dotenv
from flask import Flask, request

# ローカル開発時は .env.local に環境変数を書いておけば、毎回exportしなくても
# 自動で読み込まれる（本番のVercelには .env.local が無いので何も起きない）
load_dotenv(".env.local")


def create_app() -> Flask:
    app = Flask(__name__)
    # SPREADSHEET_ID が無いときは見本（作り物のデータ。src/sample_sheets.py）として動く
    app.config["SAMPLE_MODE"] = not os.environ.get("SPREADSHEET_ID") or os.environ["SPREADSHEET_ID"] == "sample"
    app.secret_key = os.environ.get("FLASK_SECRET_KEY") or ("sample-only-secret" if app.config["SAMPLE_MODE"] else os.environ["FLASK_SECRET_KEY"])

    @app.after_request
    def _log_memory(response):
        rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        app.logger.warning(f"[mem] {request.method} {request.path} -> {rss_mb:.1f} MB (peak)")
        return response
    # 初回起動時にスプレッドシート側へパスワードのハッシュを作るための初期値。
    # 以後の実際の認証・変更はスプレッドシート側の値が使われる（設定画面から変更可能）。
    app.config["INITIAL_APP_PASSWORD"] = os.environ.get("APP_PASSWORD", "sample") if app.config["SAMPLE_MODE"] else os.environ["APP_PASSWORD"]
    app.config["SPREADSHEET_ID"] = "sample" if app.config["SAMPLE_MODE"] else os.environ["SPREADSHEET_ID"]
    app.config["DEFAULT_CONTACT_FREQUENCY_DAYS"] = int(os.environ.get("DEFAULT_CONTACT_FREQUENCY_DAYS", "14"))
    app.config["BIRTHDAY_LOOKAHEAD_DAYS"] = int(os.environ.get("BIRTHDAY_LOOKAHEAD_DAYS", "14"))

    from . import auth, routes

    app.register_blueprint(auth.bp)
    app.register_blueprint(routes.bp)

    return app
