"""Smoke test for the Streamlit shell.

AppTest cannot drive ``st.file_uploader``, so this only covers rendering: it guards
against import errors and misused Streamlit APIs, which unit tests never reach.
"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from core.run import create_run

APP_PATH = str(Path(__file__).parent.parent / "app.py")


def test_renders_without_a_run(run_root):
    app = AppTest.from_file(APP_PATH, default_timeout=30).run()

    assert not app.exception
    assert app.title[0].value == "Data Upload"
    assert any("No run started yet" in caption.value for caption in app.sidebar.caption)
    assert not app.sidebar.code


def test_sidebar_shows_the_active_run(run_root):
    run_id = create_run().run_id

    app = AppTest.from_file(APP_PATH, default_timeout=30)
    app.session_state["run_id"] = run_id
    app.run()

    assert not app.exception
    assert app.sidebar.code[0].value == run_id
    assert any(run_id in caption.value for caption in app.sidebar.caption)
