import re

#transforms TAM-voice MSA text into direct customer-facing phrasing.

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


_LEADIN_RE = re.compile(
    r"^\s*(?:to\s+)?"
    r"(?:inform|notify|advise|encourage|ask|remind|tell)\s+"
    r"customers?\s+(?:that|to)\s+",
    re.IGNORECASE,
)


_CUSTOMERS_TO_RE = re.compile(r"\bcustomers?\s+to\b", re.IGNORECASE)


_CUSTOMERS_MODAL_RE = re.compile(
    r"\b(?:the\s+)?customers?\s+"
    r"(should|need(?:s)? to|will need to|must|are required to|are expected to|"
    r"are advised to|can|will)\b",
    re.IGNORECASE,
)


def _capitalize(s: str) -> str:
    return s[0].upper() + s[1:] if s else s


def to_customer_voice(text: str) -> str:
    #Rewrite TAM-voice MSA text into direct customer-facing phrasing.
   
    if not text:
        return text

    sentences = _SENTENCE_SPLIT_RE.split(text.strip())
    sentences = [_capitalize(_LEADIN_RE.sub("", s)) for s in sentences]
    out = " ".join(sentences)

    out = _CUSTOMERS_TO_RE.sub("you to", out)
    out = _CUSTOMERS_MODAL_RE.sub(lambda m: f"you {m.group(1)}", out)

    return _capitalize(out)