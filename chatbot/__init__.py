"""MSAi matching and conversational-agent package."""

from .matching import (
    CustomerProfile,
    FeedImpact,
    FeedItem,
    MsaMatch,
    MsaProfile,
    build_feed,
    build_matches,
    load_customer_profiles,
    load_msa_profiles,
)

__all__ = [
    "CustomerProfile",
    "FeedImpact",
    "FeedItem",
    "MsaMatch",
    "MsaProfile",
    "build_feed",
    "build_matches",
    "load_customer_profiles",
    "load_msa_profiles",
]
