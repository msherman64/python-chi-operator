from datetime import datetime


def column_align(rows):
    widths = [max(map(len, col)) for col in zip(*rows)]
    return [
        "  ".join(val.ljust(width) for val, width in zip(row, widths))
        for row in rows
    ]


def now():
    return datetime.now()
