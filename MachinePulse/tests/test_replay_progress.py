from utils.replay_progress import REPLAY_STAGES, get_replay_stage_index


def test_replay_stage_boundaries() -> None:
    expected_stages = [
        (0, 0),
        (4.99, 0),
        (5, 1),
        (14.99, 1),
        (15, 2),
        (44.99, 2),
        (45, 3),
        (120, 3),
    ]

    for elapsed_seconds, expected_index in expected_stages:
        assert get_replay_stage_index(elapsed_seconds) == expected_index


def test_replay_stage_labels_do_not_expose_timing() -> None:
    assert REPLAY_STAGES == (
        "Restarting the simulator",
        "Fetching historical data",
        "Training the timeseries model",
        "Finalizing...",
    )
