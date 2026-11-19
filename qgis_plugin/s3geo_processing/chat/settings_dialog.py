from __future__ import annotations

from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .config import PRESETS, ChatConfig
from .llm import LLMError, OpenAICompatClient


class SettingsDialog(QDialog):
    def __init__(self, cfg: ChatConfig, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("S3Geo chat - model settings")
        self.setMinimumWidth(520)
        self.provider = QComboBox()
        self.provider.addItems(list(PRESETS))
        self.base_url = QLineEdit(cfg.base_url)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setCurrentText(cfg.model)
        self.fetch = QPushButton("Fetch models")
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.model, 1)
        h.addWidget(self.fetch)
        self.api_key = QLineEdit(cfg.api_key)
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("or leave empty and use the environment variable below")
        self.api_key_env = QLineEdit(cfg.api_key_env)
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.1)
        self.temperature.setValue(cfg.temperature)
        self.max_steps = QSpinBox()
        self.max_steps.setRange(2, 40)
        self.max_steps.setValue(cfg.max_steps)
        self.share = QCheckBox("Send up to 3 example rows per layer to the model")
        self.share.setChecked(cfg.share_sample_rows)
        self.server_cmd = QLineEdit(cfg.server_command)
        self.server_cmd.setPlaceholderText("empty = auto-detect smart-spatial-api")
        self.max_mb = QSpinBox()
        self.max_mb.setRange(1, 500)
        self.max_mb.setValue(cfg.max_input_mb)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.test = QPushButton("Test connection")

        form = QFormLayout()
        form.addRow("Provider preset", self.provider)
        form.addRow("API base URL", self.base_url)
        form.addRow("Model", row)
        form.addRow("API key", self.api_key)
        form.addRow("Key from env var", self.api_key_env)
        form.addRow("Temperature", self.temperature)
        form.addRow("Max steps per question", self.max_steps)
        form.addRow("", self.share)
        form.addRow("MCP server command", self.server_cmd)
        form.addRow("Max layer size (MB)", self.max_mb)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        note = QLabel("Any OpenAI-compatible endpoint works (OpenAI, AvalAI, OpenRouter, Ollama, "
                      "LM Studio, vLLM, ...). A typed key is stored in the QGIS profile in plain "
                      "text; prefer the environment variable on shared machines. Layer geometries "
                      "are never sent to the model, only names, fields and summaries.")
        note.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.test)
        lay.addWidget(self.status)
        lay.addWidget(note)
        lay.addWidget(buttons)

        idx = self.provider.findText(cfg.provider)
        self.provider.setCurrentIndex(max(idx, 0))
        self.provider.currentTextChanged.connect(self._preset)
        self.fetch.clicked.connect(self._fetch)
        self.test.clicked.connect(self._test)

    def _preset(self, name: str) -> None:
        url, env = PRESETS.get(name, ("", ""))
        if url:
            self.base_url.setText(url)
        if env:
            self.api_key_env.setText(env)

    def _client(self) -> OpenAICompatClient:
        cfg = self.config()
        return OpenAICompatClient(cfg.base_url, cfg.resolve_api_key(), cfg.model,
                                  temperature=cfg.temperature, timeout=30, retries=0)

    def _fetch(self) -> None:
        try:
            models = self._client().list_models()
        except (LLMError, Exception) as exc:  # noqa: BLE001 - shown to the user
            self.status.setText(f"Could not list models: {exc}")
            return
        current = self.model.currentText()
        self.model.clear()
        self.model.addItems(models)
        self.model.setCurrentText(current or (models[0] if models else ""))
        self.status.setText(f"{len(models)} models available.")

    def _test(self) -> None:
        try:
            msg = self._client().chat([{"role": "user", "content": "Reply with the word ok."}])
            self.status.setText("Connected. The model answered: "
                                f"{(msg.get('content') or '').strip()[:60]!r}")
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"Connection failed: {exc}")

    def config(self) -> ChatConfig:
        return ChatConfig(
            provider=self.provider.currentText(), base_url=self.base_url.text().strip(),
            model=self.model.currentText().strip(), api_key=self.api_key.text().strip(),
            api_key_env=self.api_key_env.text().strip(), temperature=self.temperature.value(),
            max_steps=self.max_steps.value(), share_sample_rows=self.share.isChecked(),
            server_command=self.server_cmd.text().strip(), max_input_mb=self.max_mb.value())
