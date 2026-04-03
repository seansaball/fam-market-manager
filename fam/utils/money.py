"""Integer-cents monetary helpers.

All internal monetary values are stored and computed as integer cents
(e.g. $89.99 → 8999).  Conversion to/from display dollars happens
ONLY at UI and export boundaries via the helpers in this module.
"""


def dollars_to_cents(dollars: float) -> int:
    """Convert a dollar float to integer cents, rounding to nearest cent.

    >>> dollars_to_cents(89.99)
    8999
    >>> dollars_to_cents(0.005)
    1
    """
    return int(round(dollars * 100))


def cents_to_dollars(cents: int) -> float:
    """Convert integer cents to a dollar float.

    >>> cents_to_dollars(8999)
    89.99
    """
    return cents / 100


def format_dollars(cents: int) -> str:
    """Format integer cents as a dollar string with 2 decimal places.

    >>> format_dollars(8999)
    '$89.99'
    >>> format_dollars(0)
    '$0.00'
    >>> format_dollars(100)
    '$1.00'
    """
    return f"${cents / 100:.2f}"


def format_dollars_comma(cents: int) -> str:
    """Format integer cents as a dollar string with comma separators.

    >>> format_dollars_comma(123456)
    '$1,234.56'
    """
    return f"${cents / 100:,.2f}"
