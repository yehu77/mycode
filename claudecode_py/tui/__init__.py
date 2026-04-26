def run_tui_app(session):
    from .app import run_tui_app as _run_tui_app

    return _run_tui_app(session)


__all__ = ["run_tui_app"]
