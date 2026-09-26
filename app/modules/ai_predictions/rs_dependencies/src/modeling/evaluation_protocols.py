"""
evaluation_protocols.py
=======================
Single source of truth for evaluation splits in Research V2.
Defines P1 (Temporal Generalization) and P2 (Cross-Route Transfer).
"""
import pandas as pd
import numpy as np
from sklearn.model_selection import GroupKFold

class TemporalProtocol:
    """
    P1 — Temporal Generalization
    Answers: Model generalize to new date/weather on familiar route domain?
    """
    name = "P1_TEMPORAL"
    
    def __init__(self, n_splits=5, group_col="date"):
        self.n_splits = n_splits
        self.group_col = group_col
        
    def split(self, df: pd.DataFrame):
        """
        GroupKFold by date. Expected to run on Route R1 data.
        Returns generator of (train_idx, test_idx).
        """
        # Ensure df has the group column
        if self.group_col not in df.columns:
            raise ValueError(f"Missing group column: {self.group_col}")
            
        gkf = GroupKFold(n_splits=self.n_splits)
        groups = df[self.group_col]
        return gkf.split(df, groups=groups)


class CrossRouteProtocol:
    """
    P2 — Exploratory Cross-Route Transfer
    Answers: How does model trained on R1 transfer to R2–R6?
    """
    name = "P2_CROSS_ROUTE"
    
    def __init__(self, source_route="R1"):
        self.source_route = source_route
        
    def split(self, df: pd.DataFrame):
        """
        Train on source route (R1), test on non-source routes (R2-R6).
        Returns a single tuple of (train_idx, test_idx) as a generator.
        """
        if "route" not in df.columns:
            raise ValueError("Missing 'route' column for cross-route split.")
            
        train_mask = df["route"] == self.source_route
        test_mask = df["route"] != self.source_route
        
        train_idx = np.where(train_mask)[0]
        test_idx = np.where(test_mask)[0]
        
        if len(test_idx) == 0:
            import warnings
            warnings.warn("No non-source routes found in dataset for P2 evaluation.")
            
        yield train_idx, test_idx
