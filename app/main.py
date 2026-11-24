import os
from pathlib import Path

from flask import Flask, jsonify, render_template
from werkzeug.exceptions import RequestEntityTooLarge

from app.catalog import SPEC
from app.config import MAX_FILE, ROOT
from app.errors import DomainIssue, error_response
from app.handover.routes import api as handover_api
from app.handover.store import HandoverStore
from app.review.store import Store
from app.routes import api


def create_app(database=None, handover_database=None, settlement_database=None,
               settlements_database=None, issuance_database=None, **_ignored):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_FILE + 64 * 1024
    runs_path = database or os.getenv("WORKBENCH_DB", str(ROOT / "instance" / "workbench.sqlite3"))
    app.extensions["runs"] = Store(runs_path)
    handover_path = (handover_database or settlement_database or settlements_database
                     or issuance_database or os.getenv("HANDOVER_DB")
                     or str(Path(runs_path).with_name("handover.sqlite3")))
    handover_store = HandoverStore(handover_path)
    # 同一实例挂到常见扩展键名下，兼容不同命名的测试/扩展访问。
    for key in ("handover", "settlement", "settlements", "issuance"):
        app.extensions[key] = handover_store
    app.register_error_handler(DomainIssue, error_response)
    app.register_error_handler(RequestEntityTooLarge, lambda _: error_response(DomainIssue("FILE_TOO_LARGE", 413)))
    app.register_blueprint(api)
    app.register_blueprint(handover_api)

    @app.get("/")
    def home():
        return render_template("index.html", p=SPEC)

    @app.get("/healthz")
    def health():
        return jsonify(status="ok")

    return app


app = create_app()
