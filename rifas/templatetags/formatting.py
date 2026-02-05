from __future__ import annotations

from django import template

register = template.Library()


@register.filter
def digits_only(value: str) -> str:
    s = str(value or "")
    return "".join(ch for ch in s if ch.isdigit())

