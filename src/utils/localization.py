"""
Localization utilities
"""

import gettext
from ..config.paths import Paths
from ..constants import LANGUAGE_OPTIONS


def setup_translation(language: str):
    """Setup gettext translation for the given language."""
    lang = gettext.translation("base", localedir=Paths.locales_dir, languages=[language])
    lang.install()
    return lang.gettext, lang.ngettext


def get_available_languages():
    """Get list of available languages."""
    return LANGUAGE_OPTIONS
