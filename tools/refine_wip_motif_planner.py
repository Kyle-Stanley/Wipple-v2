from pathlib import Path


path = Path("wipple/accounting/wip.py")
text = path.read_text()

signed_marker = """    # One signed net-position column. It has no standalone visual signature, but
    # E - N must still reproduce a separate physical B vector across the table.
"""
early_return = """    if bridges:
        best = {}
        for bridge in bridges:
            col = bridge[\"b_col\"]
            if col not in best or bridge[\"rank\"] > best[col][\"rank\"]:
                best[col] = bridge
        return sorted(best.values(), key=lambda item: item[\"rank\"],
                      reverse=True)

"""
assert signed_marker in text
text = text.replace(signed_marker, early_return + signed_marker, 1)

percent_marker = """    # Optional percent-billed bridge. Quantization uncertainty is propagated
    # into the predicted money vector; full-row peel/certification remains final.
"""
assert percent_marker in text
text = text.replace(percent_marker, early_return + percent_marker, 1)
path.write_text(text)


test_path = Path("tests/test_wip_motif_planner.py")
tests = test_path.read_text()
append = r'''


def test_single_signed_net_position_drives_billing_bridge():
    base = make_rich_wip(n=52, decoys=16)
    net = base[:, 8] + base[:, 9]  # U plus signed O equals E - B
    matrix = np.column_stack([base[:, :8], net, base[:, 10:]])
    labels = [f"J-{i}" for i in range(matrix.shape[0])]
    result = validate_wip(
        matrix, labels,
        Config(max_anchor_pairs=8, motif_rank_rows=44))

    planner = result.diagnostics["motif_planner"]
    assert planner["used"] is True
    assert any(item["billing"] == "signed_net" for item in planner["top"])
    assert result.mapping[0] == "V"
    assert result.mapping[1] == "C"
    assert result.mapping[2] == "G"
    assert result.mapping[3] == "E"
    assert result.mapping[4] == "D"
    assert result.mapping[6] == "B"
    assert result.mapping[8] == "N"
'''
assert "test_single_signed_net_position_drives_billing_bridge" not in tests
test_path.write_text(tests + append)
