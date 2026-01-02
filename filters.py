from datetime import datetime

def fmt_dt(value):
    if not value:
        return "-"

    # handle datetime or ISO-ish strings
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).replace("Z", "").replace("T", " ")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return s  # fallback: show raw if unexpected

    return dt.strftime("%d.%m.%Y %H:%M")


def register_filters(app):
    app.jinja_env.filters["fmt_dt"] = fmt_dt
