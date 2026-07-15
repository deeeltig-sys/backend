# Matches the reaction_type enum in db/schema.sql exactly.
VALID_REACTIONS = {"fire", "cosign", "doubt", "yawa"}


def is_valid_reaction(reaction_type: str) -> bool:
    return reaction_type in VALID_REACTIONS
