"""Desktop text and the language-neutral boundary around export results."""

from locale_engine import engine_localize
from locale_gui import GUI_MESSAGES


LANGUAGES = {"zh-CN": "简体中文", "en": "English", "ja": "日本語"}


def resolve_language(choice, preferred_locales):
    if choice in LANGUAGES:
        return choice
    if isinstance(preferred_locales, str):
        preferred_locales = [preferred_locales]
    for locale in preferred_locales:
        primary = locale.lower().replace("_", "-").split("-")[0]
        if primary in ("zh", "en", "ja"):
            return "zh-CN" if primary == "zh" else primary
    return "en"


class Translator:
    def __init__(self, code):
        self.code = code

    def text(self, source, **fields):
        versions = GUI_MESSAGES.get(source)
        if versions and self.code != "zh-CN":
            source = versions[0 if self.code == "en" else 1]
        return source.format(**fields) if fields else source

    def message(self, source):
        if source in GUI_MESSAGES:
            return self.text(source)
        return engine_localize(source, self.code)
