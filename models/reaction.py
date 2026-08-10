# Matches the reaction_type enum in db/schema.sql exactly. 'doubt' was
# renamed to 'like' via db/reaction_like_rename_migration.sql — Like
# now leads the reaction bar (see PostCard.jsx REACTIONS).
VALID_REACTIONS = {"like", "fire", "cosign", "yawa"}


def is_valid_reaction(reaction_type: str) -> bool:
    return reaction_type in VALID_REACTIONS
