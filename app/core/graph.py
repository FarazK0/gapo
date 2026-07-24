"""Correlation graph construction.

Same warning as features.py. This must match training50.py exactly,
including the value of TOP_K and whether correlations are signed or
absolute. A graph built with k=5 fed to a model trained with k=8 will
produce output without complaint.
"""

import numpy as np
import torch

TOP_K = 5
RETURN_FEATURE_INDEX = 0  # weekly return, per features.py ordering


def build_edge_index(feature_tensor: np.ndarray, top_k: int = TOP_K):
    """Build a directed top-k correlation graph.

    Args:
        feature_tensor: (n_stocks, lookback, n_features)

    Returns:
        edge_index: LongTensor of shape (2, n_edges)
        edge_weight: FloatTensor of shape (n_edges,)
    """
    n_stocks = feature_tensor.shape[0]
    returns = feature_tensor[:, :, RETURN_FEATURE_INDEX]  # (n_stocks, lookback)

    corr = np.corrcoef(returns)
    corr = np.nan_to_num(corr, nan=0.0)
    np.fill_diagonal(corr, -np.inf)  # never self-connect

    k = min(top_k, n_stocks - 1)
    sources, targets, weights = [], [], []

    for i in range(n_stocks):
        # Rank by absolute correlation. A strong negative relationship is
        # informative for diversification and should not be discarded.
        neighbours = np.argsort(-np.abs(np.where(np.isfinite(corr[i]), corr[i], 0.0)))[:k]
        for j in neighbours:
            sources.append(int(j))
            targets.append(int(i))
            weights.append(float(corr[i, j]))

    edge_index = torch.tensor([sources, targets], dtype=torch.long)
    edge_weight = torch.tensor(weights, dtype=torch.float32)
    return edge_index, edge_weight
