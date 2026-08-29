"""OpenAI client and settings, read from the environment.

The environment is populated by the entrypoint -- from .env locally, from
st.secrets on Streamlit Cloud -- which keeps this layer free of both dotenv
loading and Streamlit imports.
"""

import os

from openai import OpenAI

DEFAULT_MODEL = "gpt-5-mini"
DEFAULT_TIMEOUT = 120.0


class MissingApiKeyError(RuntimeError):
    """Raised when no OpenAI credentials are configured."""


def api_key_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def model_name() -> str:
    return os.getenv("OPENAI_MODEL") or DEFAULT_MODEL


def build_client() -> OpenAI:
    if not api_key_configured():
        raise MissingApiKeyError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    timeout = float(os.getenv("OPENAI_TIMEOUT") or DEFAULT_TIMEOUT)
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=timeout)
