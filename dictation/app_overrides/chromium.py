from talon import app, ui


def enable_manual_accessibility(active_app: ui.App):
    try:
        # This is sufficient to enable accessibility in Chromium apps, including
        # Electron apps.
        _ = active_app.element.AXRole
    except Exception:
        pass


def on_ready():
    ui.register("app_activate", enable_manual_accessibility)
    enable_manual_accessibility(ui.active_app())


if app.platform == "mac":
    app.register("ready", on_ready)
