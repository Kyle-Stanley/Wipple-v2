from pathlib import Path

wip = Path('wipple/accounting/wip.py')
text = wip.read_text()
old = '''    motif_pairs = filtered if filtered else pairs
    billing_ranked = _rank_billing_motif_pairs(
        cols_m, d_cache, motif_pairs, billing_motifs or [],
        vcol, xcol, cfg)
'''
new = '''    # Keep the motif pass genuinely cheap even when one independent axis has
    # weak evidence. Missing the true placement here is harmless: the complete
    # pair-dependent ranker below remains the fallback.
    motif_d_keep = {
        col for _, col in d_scores[:cfg.anchor_rank_axis_keep]
    }
    motif_b_keep = {
        col for _, col in b_scores[:cfg.anchor_rank_axis_keep]
    }
    motif_pairs = [
        (d, b) for d, b in pairs
        if d in motif_d_keep and b in motif_b_keep
    ]
    billing_ranked = _rank_billing_motif_pairs(
        cols_m, d_cache, motif_pairs, billing_motifs or [],
        vcol, xcol, cfg)
'''
assert text.count(old) == 1
wip.write_text(text.replace(old, new))

test = Path('tests/test_billing_motif_fast_path.py')
t = test.read_text()
t = t.replace('import dataclasses\n\n', '')
test.write_text(t)
