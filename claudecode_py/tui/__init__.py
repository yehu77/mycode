def run_tui_app(session, **kwargs):
    from .app import run_tui_app as _run_tui_app

    return _run_tui_app(session, **kwargs)


__all__ = ["run_tui_app"]
