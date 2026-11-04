from django import template
from django.utils import timezone
from django.utils.html import format_html

from ..services.register import LABELS

register = template.Library()


@register.filter
def stamp(value, fmt="%H:%M"):
    """An attendance time set as a small ruled stamp, or an em dash."""
    if not value:
        return format_html('<span class="stamp stamp-empty" aria-label="not recorded">—</span>')
    local = timezone.localtime(value)
    return format_html('<time class="stamp" datetime="{}">{}</time>', local.isoformat(), local.strftime(fmt))


@register.filter
def status_dot(code, label=None):
    label = label or LABELS.get(code, code)
    return format_html(
        '<span class="status status-{}"><span class="dot" aria-hidden="true"></span>{}</span>', code, label
    )


@register.filter
def day(value):
    return f"{value.day} {value:%b %Y}" if value else ""


@register.filter
def weekday_day(value):
    return f"{value:%a} {value.day} {value:%b}" if value else ""


@register.filter
def get(mapping, key):
    return mapping.get(key, 0) if mapping else 0
