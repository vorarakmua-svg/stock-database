"""Pydantic model for a single period of standardized (canonical) financials.

The model is built from the ``CANONICAL_FIELDS`` registry so it can never drift
from the extractor. It provides type coercion and validation; failures are
surfaced as warnings rather than crashing collection.
"""

from typing import Any, Dict, List, Optional, Tuple

from pydantic import ConfigDict, ValidationError, create_model

from ..mappings.canonical import CANONICAL_FIELDS

# Period-identifying metadata the parser attaches alongside the canonical values.
_META_FIELDS: Dict[str, Any] = {
    "fiscal_year": (Optional[int], None),
    "fiscal_period": (Optional[str], None),
    "period_end": (Optional[str], None),
    "filed_date": (Optional[str], None),
    "form": (Optional[str], None),
}

# One Optional[float] field per canonical line item, plus the metadata fields.
_field_defs: Dict[str, Any] = {f.key: (Optional[float], None) for f in CANONICAL_FIELDS}
_field_defs.update(_META_FIELDS)

# Dynamically-built model: attributes mirror canonical keys exactly.
CanonicalFinancials = create_model(  # type: ignore[call-overload]
    "CanonicalFinancials",
    __config__=ConfigDict(extra="forbid"),
    **_field_defs,
)


def validate_period(raw: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Validate/coerce one period dict against :data:`CanonicalFinancials`.

    The provenance map (``_source_tags``) is preserved verbatim and not validated.

    Returns:
        ``(clean, errors)`` — ``clean`` is the coerced dict (only present values,
        plus ``_source_tags`` if it was given); ``errors`` lists human-readable
        validation messages (empty when valid). On failure the original ``raw`` is
        returned so collection is never lost.
    """
    source_tags = raw.get("_source_tags")
    payload = {k: v for k, v in raw.items() if k != "_source_tags"}

    try:
        model = CanonicalFinancials.model_validate(payload)
    except ValidationError as exc:
        messages = [
            f"{'/'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        ]
        return raw, messages

    clean = model.model_dump(exclude_none=True)
    if source_tags is not None:
        clean["_source_tags"] = source_tags
    return clean, []
