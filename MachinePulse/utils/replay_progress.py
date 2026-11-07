REPLAY_STAGES: tuple[str, ...] = (
    "Restarting the simulator",
    "Fetching historical data",
    "Training the timeseries model",
    "Finalizing...",
)


def get_replay_stage_index(elapsed_seconds: float) -> int:
    if elapsed_seconds < 5:
        return 0
    if elapsed_seconds < 15:
        return 1
    if elapsed_seconds < 45:
        return 2
    return 3
