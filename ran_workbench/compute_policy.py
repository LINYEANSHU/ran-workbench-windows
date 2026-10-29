"""Bound receiver fan-out while allowing useful GPU batches above 64 points."""

# Sionna keeps ray states, candidate paths and dense output tensors. These
# conservative work budgets are scheduling estimates, not GPU memory promises.
MAX_RAY_RECEIVER_PAIRS = 256_000_000
MAX_CANDIDATE_BYTES = 512 * 1024 * 1024


def effective_batch_size(settings):
    depth = max(1, settings['max_depth'])
    # Extra interactions need proportionally more retained state.
    complexity = max(1, depth / 3)
    fanout = max(1, int(MAX_RAY_RECEIVER_PAIRS / (settings['samples_per_src'] * complexity)))
    return min(settings['batch_size'], fanout)


def candidate_path_budget(settings, receivers):
    # Separate the stored-path budget from launched rays. At low ray counts,
    # all receiver LoS/specular chains still need room. Never silently allocate
    # 10 million depth-10 candidates merely because 10 million rays are shot.
    bytes_per_path = 128 + 96 * max(1, settings['max_depth'])
    memory_cap = max(receivers, MAX_CANDIDATE_BYTES // bytes_per_path)
    desired = max(settings['samples_per_src'], receivers * 256)
    return min(desired, memory_cap)
