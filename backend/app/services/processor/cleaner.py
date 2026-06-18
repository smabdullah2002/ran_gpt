import trafilatura


def extract_text(html: str) -> str | None:
    return trafilatura.extract(html)
