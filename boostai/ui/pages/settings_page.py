"""Settings: appearance, optimisation mode, monitoring, AI provider and privacy, data."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from boostai.ai import credentials
from boostai.config import paths
from boostai.config.settings import AIProviderName, OptimizationMode, Theme
from boostai.ui import theme
from boostai.ui.pages.base import Page
from boostai.ui.widgets import Banner, label, page_header
from boostai.ui.workers import run_async

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def set_start_with_windows(enabled: bool) -> None:
    """Register/unregister BoostAI itself in the current user's Run key (only our own entry)."""
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            if getattr(sys, "frozen", False):
                cmd = f'"{sys.executable}" --minimized'
            else:
                pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                cmd = f'"{pythonw}" -m boostai --minimized'
            winreg.SetValueEx(key, "BoostAI", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, "BoostAI")
            except FileNotFoundError:
                pass


class SettingsPage(Page):
    key = "settings"
    title = "Settings"

    def __init__(self, bridge) -> None:
        super().__init__(bridge)
        self.body.addWidget(page_header("Settings"))
        s = self.engine.settings

        # Appearance & behaviour
        g = QGroupBox("Appearance and behaviour")
        f = QFormLayout(g)
        self.theme_box = QComboBox()
        self.theme_box.addItem("Dark", Theme.DARK.value)
        self.theme_box.addItem("Light", Theme.LIGHT.value)
        self.tray = QCheckBox("Keep running in the system tray when the window is closed")
        self.notify = QCheckBox("Show tray notifications for serious problems (e.g. a likely memory leak)")
        self.autostart = QCheckBox("Start BoostAI when I sign in (minimized to tray)")
        f.addRow("Theme", self.theme_box)
        f.addRow(self.tray)
        f.addRow(self.notify)
        f.addRow(self.autostart)
        self.body.addWidget(g)

        # Optimisation
        g = QGroupBox("Optimization")
        v = QVBoxLayout(g)
        self.mode_group = QButtonGroup(self)
        self.safe = QRadioButton("Safe mode – only low-risk changes can be applied; everything else is advice")
        self.balanced = QRadioButton("Balanced mode – low- and medium-risk changes (restart apps, startup items, "
                                     "safe cleanup, optional services) after approval")
        self.mode_group.addButton(self.safe)
        self.mode_group.addButton(self.balanced)
        v.addWidget(self.safe)
        v.addWidget(self.balanced)
        form = QFormLayout()
        self.settle = QDoubleSpinBox()
        self.settle.setRange(3, 120)
        self.settle.setSuffix(" s")
        self.min_age = QDoubleSpinBox()
        self.min_age.setRange(1, 720)
        self.min_age.setSuffix(" h")
        self.restore = QCheckBox("Offer a System Restore point before administrator-level changes")
        form.addRow("Settling time before re-measuring", self.settle)
        form.addRow("Only clean temporary files older than", self.min_age)
        form.addRow(self.restore)
        v.addLayout(form)
        self.body.addWidget(g)

        # Monitoring
        g = QGroupBox("Monitoring")
        f = QFormLayout(g)
        self.sys_iv = QDoubleSpinBox()
        self.sys_iv.setRange(1, 60)
        self.sys_iv.setSuffix(" s")
        self.proc_iv = QDoubleSpinBox()
        self.proc_iv.setRange(3, 300)
        self.proc_iv.setSuffix(" s")
        self.bg_iv = QDoubleSpinBox()
        self.bg_iv.setRange(5, 600)
        self.bg_iv.setSuffix(" s")
        f.addRow("System sampling interval", self.sys_iv)
        f.addRow("Process sampling interval", self.proc_iv)
        f.addRow("Interval while hidden in tray", self.bg_iv)
        self.body.addWidget(g)

        # AI
        g = QGroupBox("AI advisor (optional)")
        v = QVBoxLayout(g)
        v.addWidget(label("BoostAI's detection and safety never depend on AI. AI only adds plain-language explanations "
                          "and prioritisation, and can only pick from actions BoostAI already proposed.", muted=True,
                          wrap=True))
        form = QFormLayout()
        self.provider = QComboBox()
        self.provider.addItem("Off (rule-based only)", AIProviderName.NONE.value)
        self.provider.addItem("Local – Ollama (free, private)", AIProviderName.OLLAMA.value)
        self.provider.addItem("Cloud – Google Gemini (free tier)", AIProviderName.GEMINI.value)
        self.provider.addItem("Cloud – Groq (free tier)", AIProviderName.GROQ.value)
        self.local_only = QCheckBox("Local-only mode: never send anything off this computer (blocks cloud AI)")
        self.ollama_url = QLineEdit()
        self.ollama_model = QLineEdit()
        self.gemini_model = QLineEdit()
        self.groq_model = QLineEdit()
        self.gemini_key = QLineEdit()
        self.gemini_key.setEchoMode(QLineEdit.Password)
        self.groq_key = QLineEdit()
        self.groq_key.setEchoMode(QLineEdit.Password)
        form.addRow("Provider", self.provider)
        form.addRow(self.local_only)
        form.addRow("Ollama URL", self.ollama_url)
        form.addRow("Ollama model", self.ollama_model)
        form.addRow("Gemini model", self.gemini_model)
        form.addRow("Gemini API key", self.gemini_key)
        form.addRow("Groq model", self.groq_model)
        form.addRow("Groq API key", self.groq_key)
        v.addLayout(form)
        self.cloud_banner = Banner("", warn=True)
        v.addWidget(self.cloud_banner)
        trow = QHBoxLayout()
        self.test_btn = QPushButton("Test AI connection")
        self.test_btn.clicked.connect(self.test_ai)
        self.test_result = label("", muted=True, wrap=True)
        trow.addWidget(self.test_btn)
        trow.addWidget(self.test_result, 1)
        v.addLayout(trow)
        self.body.addWidget(g)
        self.provider.currentIndexChanged.connect(self._update_cloud_banner)
        self.local_only.toggled.connect(self._update_cloud_banner)

        # Data
        g = QGroupBox("Data and privacy")
        v = QVBoxLayout(g)
        self.data_label = label("", muted=True, wrap=True)
        v.addWidget(self.data_label)
        drow = QHBoxLayout()
        open_btn = QPushButton("Open data folder")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.data_dir()))))
        drow.addWidget(open_btn)
        drow.addStretch(1)
        v.addLayout(drow)
        self.body.addWidget(g)

        brow = QHBoxLayout()
        brow.addStretch(1)
        save = QPushButton("Save settings")
        save.setObjectName("Primary")
        save.clicked.connect(self.save)
        brow.addWidget(save)
        self.body.addLayout(brow)
        self.body.addStretch(1)
        self.load(s)

    def load(self, s) -> None:
        self.theme_box.setCurrentIndex(0 if s.theme == Theme.DARK else 1)
        self.tray.setChecked(s.minimize_to_tray)
        self.notify.setChecked(s.tray_notifications)
        self.autostart.setChecked(s.start_with_windows)
        (self.safe if s.mode == OptimizationMode.SAFE else self.balanced).setChecked(True)
        self.settle.setValue(s.settle_seconds)
        self.min_age.setValue(s.cleanup_min_age_hours)
        self.restore.setChecked(s.offer_restore_point)
        self.sys_iv.setValue(s.monitor.system_interval_s)
        self.proc_iv.setValue(s.monitor.process_interval_s)
        self.bg_iv.setValue(s.monitor.background_interval_s)
        self.provider.setCurrentIndex(self.provider.findData(s.ai.provider.value))
        self.local_only.setChecked(s.ai.local_only)
        self.ollama_url.setText(s.ai.ollama_url)
        self.ollama_model.setText(s.ai.ollama_model)
        self.gemini_model.setText(s.ai.gemini_model)
        self.groq_model.setText(s.ai.groq_model)
        for box, provider in ((self.gemini_key, "gemini"), (self.groq_key, "groq")):
            box.clear()
            box.setPlaceholderText(f"stored ({credentials.key_source(provider)})"
                                   if credentials.get_api_key(provider) else "not set")
        self._update_cloud_banner()

    def on_show(self) -> None:
        counts = self.engine.repo.table_counts()
        size = 0
        try:
            size = os.path.getsize(paths.database_path())
        except OSError:
            pass
        self.data_label.setText(
            f"Data folder: {paths.data_dir()}\nDatabase: {size / 1024 / 1024:.1f} MB · "
            + ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in counts.items())
            + "\nEverything stays on this computer. Old telemetry is compacted after 24 hours and expired after "
              f"{self.engine.settings.retention.compacted_system_samples_days} days.")

    def _update_cloud_banner(self) -> None:
        cloud = self._provider().is_cloud
        self.cloud_banner.setVisible(cloud)
        if cloud and self.local_only.isChecked():
            self.cloud_banner.set_text("Local-only mode is on, so this cloud provider is blocked. Untick local-only "
                                       "mode to use it.")
        elif cloud:
            p = theme.current()
            self.cloud_banner.set_text(f"<b style='color:{p.warn}'>Cloud AI enabled.</b> System performance metadata "
                                       "may be sent to the configured AI provider when you press an AI button. BoostAI "
                                       "never sends file paths, file contents, browser data, emails or passwords.")

    def _provider(self) -> AIProviderName:
        return AIProviderName(self.provider.currentData() or AIProviderName.NONE.value)

    def save(self) -> None:
        s = self.engine.settings.model_copy(deep=True)
        old_theme = s.theme
        s.theme = Theme(self.theme_box.currentData())
        s.minimize_to_tray = self.tray.isChecked()
        s.tray_notifications = self.notify.isChecked()
        s.mode = OptimizationMode.SAFE if self.safe.isChecked() else OptimizationMode.BALANCED
        s.settle_seconds = self.settle.value()
        s.cleanup_min_age_hours = self.min_age.value()
        s.offer_restore_point = self.restore.isChecked()
        s.monitor.system_interval_s = self.sys_iv.value()
        s.monitor.process_interval_s = self.proc_iv.value()
        s.monitor.background_interval_s = self.bg_iv.value()
        s.ai.provider = self._provider()
        s.ai.local_only = self.local_only.isChecked()
        s.ai.ollama_url = self.ollama_url.text().strip() or "http://localhost:11434"
        s.ai.ollama_model = self.ollama_model.text().strip() or "qwen2.5:7b"
        s.ai.gemini_model = self.gemini_model.text().strip() or "gemini-2.5-flash"
        s.ai.groq_model = self.groq_model.text().strip() or "llama-3.3-70b-versatile"
        for box, provider in ((self.gemini_key, "gemini"), (self.groq_key, "groq")):
            if box.text().strip():
                if not credentials.set_api_key(provider, box.text().strip()):
                    QMessageBox.warning(self, "API key", f"Could not store the {provider} key in Credential Manager.")
        if s.start_with_windows != self.autostart.isChecked():
            try:
                set_start_with_windows(self.autostart.isChecked())
                s.start_with_windows = self.autostart.isChecked()
            except OSError as exc:
                QMessageBox.warning(self, "Start with Windows", f"Could not update the setting: {exc}")
        self.engine.save_settings(s)
        self.load(s)
        self.bridge.settings_changed.emit()
        if s.theme != old_theme:
            self.window().apply_theme(s.theme.value)
        QMessageBox.information(self, "Settings", "Settings saved.")

    def test_ai(self) -> None:
        self.save_silently()
        self.test_btn.setEnabled(False)
        self.test_result.setText("Testing...")
        run_async(self.engine.advisor.status,
                  on_result=lambda r: self.test_result.setText(("✔ " if r[0] else "✖ ") + r[1]),
                  on_error=lambda e: self.test_result.setText(e),
                  on_finished=lambda: self.test_btn.setEnabled(True))

    def save_silently(self) -> None:
        s = self.engine.settings.model_copy(deep=True)
        s.ai.provider = self._provider()
        s.ai.local_only = self.local_only.isChecked()
        s.ai.ollama_url = self.ollama_url.text().strip() or s.ai.ollama_url
        s.ai.ollama_model = self.ollama_model.text().strip() or s.ai.ollama_model
        s.ai.gemini_model = self.gemini_model.text().strip() or s.ai.gemini_model
        s.ai.groq_model = self.groq_model.text().strip() or s.ai.groq_model
        for box, provider in ((self.gemini_key, "gemini"), (self.groq_key, "groq")):
            if box.text().strip():
                credentials.set_api_key(provider, box.text().strip())
        self.engine.save_settings(s)
        self.bridge.settings_changed.emit()
