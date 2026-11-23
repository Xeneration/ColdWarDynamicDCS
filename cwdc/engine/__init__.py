import random


def rng(seed: int, turn: int, purpose: str) -> random.Random:
    """Deterministic RNG per (campaign seed, turn, purpose)."""
    return random.Random(f"{seed}:{turn}:{purpose}")
