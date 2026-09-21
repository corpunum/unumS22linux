#!/usr/bin/env python3
"""Pure boundary model for the repaired __range_close bound."""
UINT_MAX = (1 << 32) - 1

def visited(start, requested_max, table_max):
    bound = min(table_max - 1, requested_max)
    return [] if start > bound else list(range(start, bound + 1))

assert visited(3, UINT_MAX, 4) == [3]
assert visited(4, UINT_MAX, 4) == []
assert visited(2, 7, 4) == [2, 3]
assert visited(UINT_MAX, UINT_MAX, 4) == []
print("close_range boundaries: PASS (UINT_MAX clamp, empty and finite ranges)")
