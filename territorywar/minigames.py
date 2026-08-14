import random
from typing import Dict, Tuple


MINIGAMES = ("codebreaker", "memory")


def compute_power_gain(previous_best: int, new_score: int, max_score: int = 100) -> Tuple[int, int]:
    prev = max(0, min(max_score, int(previous_best)))
    new = max(0, min(max_score, int(new_score)))
    if new <= prev:
        return prev, 0
    return new, new - prev


def generate_codebreaker_secret() -> str:
    digits = list("0123456789")
    random.shuffle(digits)
    return "".join(digits[:4])


def validate_codebreaker_guess(guess: str) -> bool:
    token = str(guess).strip()
    return len(token) == 4 and token.isdigit() and len(set(token)) == 4


def codebreaker_feedback(secret: str, guess: str) -> Dict[str, int]:
    exact = 0
    partial = 0
    for idx, char in enumerate(guess):
        if secret[idx] == char:
            exact += 1
        elif char in secret:
            partial += 1
    return {"exact": exact, "partial": partial}


def codebreaker_score(max_attempts: int, attempts_used: int, solved: bool) -> int:
    if not solved:
        return 0
    max_attempts = max(1, int(max_attempts))
    used = max(1, min(max_attempts, int(attempts_used)))
    remaining = max_attempts - used + 1
    score = int(round((remaining / max_attempts) * 100))
    return max(1, min(100, score))


def generate_memory_sequence(length: int = 7, alphabet: str = "ABCD") -> str:
    length = max(3, int(length))
    chars = [random.choice(alphabet) for _ in range(length)]
    return "".join(chars)


def memory_score(sequence: str, answer: str) -> int:
    sequence = str(sequence).strip().upper()
    answer = str(answer).strip().upper()
    if not sequence:
        return 0

    matches = 0
    for idx, char in enumerate(sequence):
        if idx < len(answer) and answer[idx] == char:
            matches += 1

    return max(0, min(100, int(round((matches / len(sequence)) * 100))))
