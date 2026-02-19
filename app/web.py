from __future__ import annotations

import logging

from flask import Flask, jsonify, render_template, request, send_file

from app.config_store import ConfigError, ConfigStore
from app.service import BridgeService

LOGGER = logging.getLogger(__name__)


def create_web_app(store: ConfigStore, service: BridgeService) -> Flask:
    app = Flask(__name__, template_folder="../templates")

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/config")
    def get_config():
        return jsonify(store.config.to_dict())

    @app.post("/api/config")
    def update_config():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Payload JSON non valido"}), 400

        try:
            store.update_from_dict(payload)
            service.reload()
            return jsonify({"status": "ok", "message": "Configurazione salvata e caricata"})
        except ConfigError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            LOGGER.exception("Errore salvataggio configurazione")
            return jsonify({"error": "Errore interno salvataggio configurazione"}), 500

    @app.get("/api/config/download")
    def download_config():
        return send_file(
            store.path,
            as_attachment=True,
            download_name=store.path.name,
            mimetype="application/json",
        )

    @app.post("/api/poll")
    def run_poll():
        service.trigger_poll_all()
        return jsonify({"status": "ok", "message": "Polling richiesto"})

    @app.post("/api/restart")
    def restart():
        payload = request.get_json(silent=True) or {}
        mode = str(payload.get("mode", "app")).strip().lower()

        try:
            if mode == "service":
                message = service.run_restart_command()
            else:
                message = service.restart_self()
            return jsonify({"status": "ok", "message": message})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    return app
