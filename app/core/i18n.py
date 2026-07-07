from flask import current_app, g, redirect, request, session, url_for

from app.utils.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, get_lang


def register_i18n(app):
    @app.before_request
    def load_language():
        g.lang = get_lang()

    @app.context_processor
    def inject_i18n():
        from app.utils.i18n import (
            acceptance_label,
            booking_action_label,
            issue_label,
            next_action_label,
            status_label,
            translate,
            urgency_label,
        )

        from app.utils.seo import canonical_url, hreflang_alternates, site_base_url

        lang = get_lang()
        return {
            "lang": lang,
            "site_base_url": site_base_url,
            "canonical_url": canonical_url,
            "hreflang_alternates": hreflang_alternates,
            "google_places_api_key": current_app.config.get("GOOGLE_PLACES_API_KEY", ""),
            "_": lambda key, **kwargs: translate(key, lang, **kwargs),
            "status_label": lambda s: status_label(s, lang),
            "urgency_label": lambda u: urgency_label(u, lang),
            "issue_label": lambda i: issue_label(i, lang),
            "next_action_label": lambda a: next_action_label(a, lang),
            "booking_action_label": lambda a: booking_action_label(a, lang),
            "acceptance_label": lambda s: acceptance_label(s, lang),
        }


def set_language_preference(lang):
    if lang not in SUPPORTED_LANGUAGES:
        lang = DEFAULT_LANGUAGE
    session["lang"] = lang
    return lang
