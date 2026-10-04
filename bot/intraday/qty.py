"""Share quantities: whole numbers stay ints (so US whole-share behaviour is unchanged); fractional ones are floats
rounded to 8 places so repeated subtraction never leaves dust like 0.30000000000000004."""


def clean(q: float):
    q = round(float(q), 8)
    return int(q) if q.is_integer() else q


def fmt(q: float) -> str:
    """Quantity as the API wants it: '100', '0.13' (never '100.0' or '1e-05')."""
    q = clean(q)
    return str(q) if isinstance(q, int) else f"{q:.8f}".rstrip("0").rstrip(".")
