from talon import app, ui


def enable_manual_accessibility(active_app: ui.App):
    try:
        # Electron documents this AX attribute as its third-party macOS opt-in.
        active_app.element["AXManualAccessibility"] = True
    except Exception:
        pass


def on_ready():
    ui.register("app_activate", enable_manual_accessibility)
    enable_manual_accessibility(ui.active_app())


if app.platform == "mac":
    app.register("ready", on_ready)
