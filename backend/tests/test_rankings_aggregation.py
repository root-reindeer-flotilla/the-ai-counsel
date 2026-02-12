from backend.council import calculate_aggregate_rankings


def _ranking_text(labels):
    lines = ["FINAL RANKING:"]
    for idx, label in enumerate(labels, start=1):
        lines.append(f"{idx}. {label}")
    return "\n".join(lines)


def test_full_ballots_match_unweighted_behavior():
    label_to_model = {
        "Response A": "model-a",
        "Response B": "model-b",
        "Response C": "model-c",
    }
    stage2_results = [
        {"ranking": _ranking_text(["Response A", "Response B", "Response C"])},
        {"ranking": _ranking_text(["Response B", "Response C", "Response A"])},
    ]

    aggregate = calculate_aggregate_rankings(stage2_results, label_to_model)
    by_model = {item["model"]: item for item in aggregate}

    assert by_model["model-a"]["average_rank"] == 2.0
    assert by_model["model-b"]["average_rank"] == 1.5
    assert by_model["model-c"]["average_rank"] == 2.5


def test_partial_ballots_are_soft_weighted():
    label_to_model = {
        "Response A": "model-a",
        "Response B": "model-b",
        "Response C": "model-c",
        "Response D": "model-d",
    }
    stage2_results = [
        {"ranking": _ranking_text(["Response A", "Response B", "Response C", "Response D"])},
        {"ranking": _ranking_text(["Response B", "Response A"])},  # completion=0.5
    ]

    aggregate = calculate_aggregate_rankings(stage2_results, label_to_model)
    by_model = {item["model"]: item for item in aggregate}

    # model-a: (1*1 + 0.5*2) / (1 + 0.5) = 1.33
    assert by_model["model-a"]["average_rank"] == 1.33
    # model-b: (1*2 + 0.5*1) / (1 + 0.5) = 1.67
    assert by_model["model-b"]["average_rank"] == 1.67


def test_ballots_below_hard_cap_are_dropped():
    label_to_model = {
        "Response A": "model-a",
        "Response B": "model-b",
        "Response C": "model-c",
        "Response D": "model-d",
        "Response E": "model-e",
    }
    stage2_results = [
        {"ranking": _ranking_text(["Response A", "Response B", "Response C", "Response D", "Response E"])},
        {"ranking": _ranking_text(["Response E"])},  # completion=0.2, dropped
    ]

    aggregate, diagnostics = calculate_aggregate_rankings(
        stage2_results,
        label_to_model,
        return_diagnostics=True,
    )
    by_model = {item["model"]: item for item in aggregate}

    assert diagnostics["ballots_total"] == 2
    assert diagnostics["ballots_used"] == 1
    assert diagnostics["ballots_dropped_hard_cap"] == 1
    assert by_model["model-a"]["average_rank"] == 1.0
    assert by_model["model-e"]["average_rank"] == 5.0


def test_mixed_quality_ballots_do_not_penalize_top_model_when_bad_ballot_dropped():
    label_to_model = {
        "Response A": "model-a",
        "Response B": "model-b",
        "Response C": "model-c",
        "Response D": "model-d",
        "Response E": "model-e",
    }
    stage2_results = [
        {"ranking": _ranking_text(["Response A", "Response B", "Response C", "Response D", "Response E"])},
        {"ranking": _ranking_text(["Response B", "Response A", "Response C", "Response D", "Response E"])},
        # Omits Response A, but completion=0.2 so dropped
        {"ranking": _ranking_text(["Response E"])},
    ]

    aggregate, diagnostics = calculate_aggregate_rankings(
        stage2_results,
        label_to_model,
        return_diagnostics=True,
    )
    by_model = {item["model"]: item for item in aggregate}

    assert diagnostics["ballots_used"] == 2
    assert diagnostics["ballots_dropped_hard_cap"] == 1
    assert by_model["model-a"]["average_rank"] == 1.5


def test_duplicate_labels_are_deduped_per_ballot():
    label_to_model = {
        "Response A": "model-a",
        "Response B": "model-b",
        "Response C": "model-c",
        "Response D": "model-d",
    }
    stage2_results = [
        # Duplicate Response A appears twice; second instance should be ignored.
        {"ranking": _ranking_text(["Response A", "Response A", "Response B", "Response C"])},
        {"ranking": _ranking_text(["Response B", "Response C", "Response D", "Response A"])},
    ]

    aggregate = calculate_aggregate_rankings(stage2_results, label_to_model)
    by_model = {item["model"]: item for item in aggregate}

    assert by_model["model-a"]["rankings_count"] == 2
    # model-a: (0.75*1 + 1.0*4) / (0.75 + 1.0) = 2.71
    assert by_model["model-a"]["average_rank"] == 2.71
