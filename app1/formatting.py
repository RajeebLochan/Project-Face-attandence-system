"""Small, platform-independent date formatting helpers used in messages."""


def day(value):
    return f"{value.day} {value:%b %Y}"


def long_day(value):
    return f"{value:%A} {value.day} {value:%B %Y}"
