"""The chat panel (QDockWidget)."""

from __future__ import annotations

import html
import json
import threading

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import qt_settings
from .agent import ChatAgent
from .llm import OpenAICompatClient
from .mcp_bridge import McpBridge, default_server_command
from .qgis_adapter import MainThread, QgisAdapter
from .settings_dialog import SettingsDialog

NOTICE = (
    "What is sent to the AI model you configured:\n\n"
    "- your messages;\n- layer names, field names/types, CRS, extent and feature counts;\n"
    "- up to 3 example attribute rows per layer (can be switched off in the settings);\n"
    "- numeric summaries of analysis results.\n\n"
    "Geometries are never sent. Analysis runs locally in the s3geo MCP server, and results are "
    "added to your project as new layers; your own layers are not modified."
)


class _Input(QPlainTextEdit):
    submit = pyqtSignal()

    def keyPressEvent(self, event):  # noqa: N802 (Qt API)
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
            self.submit.emit()
            return
        super().keyPressEvent(event)


class ChatDock(QDockWidget):
    _event = pyqtSignal(str, object)
    _finished = pyqtSignal(str, str)

    def __init__(self, iface, parent=None) -> None:
        super().__init__("S3Geo chat", parent or iface.mainWindow())
        self.setObjectName("S3GeoChatDock")
        self.iface = iface
        self.main = MainThread()
        self.qgis = QgisAdapter(self.main, iface)
        self.cfg = qt_settings.load_config()
        self.agent: ChatAgent | None = None
        self.bridge: McpBridge | None = None
        self.cancel = threading.Event()
        self.busy = False

        body = QWidget()
        lay = QVBoxLayout(body)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.input = _Input()
        self.input.setPlaceholderText("Ask about your layers…  (Enter = send, Shift+Enter = new line)")
        self.input.setFixedHeight(70)
        self.send_btn = QPushButton("Send")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.new_btn = QPushButton("New chat")
        self.cfg_btn = QPushButton("Model settings")
        self.status = QLabel("")
        row = QHBoxLayout()
        for b in (self.send_btn, self.stop_btn, self.new_btn, self.cfg_btn):
            row.addWidget(b)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.input)
        lay.addLayout(row)
        lay.addWidget(self.status)
        self.setWidget(body)

        self.send_btn.clicked.connect(self.send)
        self.input.submit.connect(self.send)
        self.stop_btn.clicked.connect(self.stop)
        self.new_btn.clicked.connect(self.new_chat)
        self.cfg_btn.clicked.connect(self.open_settings)
        self._event.connect(self._on_event)
        self._finished.connect(self._on_finished)
        self._say("assistant", "Hi! Ask me about the layers in this project, e.g. “which homes "
                  "are within 500 m of a metro station?”. Set your model first via Model settings.")

    # ------------------------------------------------------------------ rendering
    def _say(self, kind: str, text: str) -> None:
        colors = {"user": "#0f766e", "assistant": "#1f2937", "step": "#6b7280",
                  "error": "#b91c1c", "ok": "#15803d"}
        weight = "600" if kind == "user" else "400"
        size = "small" if kind in ("step", "ok") else "medium"
        body = html.escape(text).replace("\n", "<br>")
        self.view.append(f'<div dir="auto" style="color:{colors.get(kind, "#111")};'
                         f'font-weight:{weight};font-size:{size};margin:4px 0">{body}</div>')

    # ------------------------------------------------------------------ actions
    def send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text or self.busy:
            return
        if not qt_settings.notice_acknowledged():
            ok = QMessageBox.question(self, "S3Geo chat", NOTICE + "\n\nContinue?")
            if ok != QMessageBox.Yes:
                return
            qt_settings.acknowledge_notice()
        problems = self.cfg.problems()
        if problems:
            self._say("error", "\n".join(problems))
            self.open_settings()
            return
        self.input.clear()
        self._say("user", text)
        self._set_busy(True, "Starting…" if self.agent is None else "Thinking…")
        self.cancel.clear()
        threading.Thread(target=self._worker, args=(text,), daemon=True, name="s3geo-chat").start()

    def stop(self) -> None:
        self.cancel.set()
        self.status.setText("Stopping after the current step…")

    def new_chat(self) -> None:
        if self.busy:
            return
        if self.agent is not None:
            self.agent.reset()
        self.view.clear()
        self._say("assistant", "New conversation. The layers in your project are still available.")

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec_():
            self.cfg = dlg.config()
            qt_settings.save_config(self.cfg)
            self._drop_agent()
            self._say("step", f"Model set to {self.cfg.model or '(none)'} @ {self.cfg.base_url}")

    def _set_busy(self, busy: bool, status: str = "") -> None:
        self.busy = busy
        self.send_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)
        self.status.setText(status)

    # ------------------------------------------------------------------ worker thread
    def _ensure_agent(self) -> ChatAgent:
        if self.agent is not None:
            return self.agent
        cfg = self.cfg
        cmd, env = default_server_command(cfg.server_command)
        limit = str(int(cfg.max_input_mb) * 1_000_000)
        env = {**env, "SMART_SPATIAL_MCP_MAX_INPUT_BYTES": limit,
               "SMART_SPATIAL_MCP_MAX_OUTPUT_BYTES": limit}
        bridge = McpBridge(cmd, env)
        bridge.start()
        self.bridge = bridge
        llm = OpenAICompatClient(cfg.base_url, cfg.resolve_api_key(), cfg.model,
                                 temperature=cfg.temperature, timeout=cfg.request_timeout)
        self.agent = ChatAgent(llm, bridge, self.qgis, max_steps=cfg.max_steps,
                               share_sample_rows=cfg.share_sample_rows,
                               on_event=lambda kind, **data: self._event.emit(kind, data))
        return self.agent

    def _worker(self, text: str) -> None:
        try:
            agent = self._ensure_agent()
            answer = agent.run_turn(text, self.cancel)
            self._finished.emit("answer", answer)
        except Exception as exc:  # noqa: BLE001 - shown in the chat
            self._finished.emit("error", f"{type(exc).__name__}: {exc}")

    def _drop_agent(self) -> None:
        bridge, self.agent, self.bridge = self.bridge, None, None
        if bridge is not None:
            threading.Thread(target=bridge.stop, daemon=True).start()

    # ------------------------------------------------------------------ GUI thread slots
    def _on_event(self, kind: str, data: dict) -> None:
        if kind == "thinking":
            self.status.setText("Thinking…")
        elif kind == "note":
            self._say("assistant", data["text"])
        elif kind == "tool_start":
            args = data.get("args", {})
            label = data["name"]
            if label == "run_tool":
                label = f"run_tool {args.get('tool', '')}"
                if args.get("metric"):
                    label += " (metres)"
            elif label in ("qgis_layer_info", "zoom_to_layer"):
                label += f" {args.get('layer', '')}"
            self._say("step", f"▸ {label}")
            self.status.setText(f"Running {label}…")
        elif kind == "tool_end":
            try:
                parsed = json.loads(data.get("result", "{}"))
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, dict) and "error" in parsed:
                self._say("error", str(parsed["error"])[:300])
        elif kind == "layer_added":
            self._say("ok", f"✓ layer “{data['name']}” added ({data['count']} features)")

    def _on_finished(self, kind: str, text: str) -> None:
        self._set_busy(False)
        self._say("error" if kind == "error" else "assistant", text or "(no answer)")

    def shutdown(self) -> None:
        self.cancel.set()
        bridge, self.bridge, self.agent = self.bridge, None, None
        if bridge is not None:
            bridge.stop()
