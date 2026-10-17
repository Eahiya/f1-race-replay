import sys
sys.path.insert(0, '.')
from src.f1_data import enable_cache, load_session
enable_cache()

# All 2024 races except Round 14 (already cached)
for rnd in range(1, 25):
    if rnd == 14:
        continue
    try:
        print(f"2024 Round {rnd}...", end=" ")
        load_session(2024, rnd, 'R')
        print("OK")
    except Exception as e:
        print(f"FAILED: {e}")

# All 2025 races except Rounds 12, 27, 33 (already cached)
for rnd in range(1, 25):
    if rnd in (12, 27, 33):
        continue
    try:
        print(f"2025 Round {rnd}...", end=" ")
        load_session(2025, rnd, 'R')
        print("OK")
    except Exception as e:
        print(f"FAILED: {e}")

print("\nDone! Copy .fastf1-cache/ to the other machine.")