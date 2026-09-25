"""
ood_detectors.py
================
Out-of-Distribution (OOD) detection models.
Ensures fold-safe scaling, thresholding, and score normalization.
"""
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.neighbors import NearestNeighbors
from sklearn.ensemble import IsolationForest

class FoldSafeOODDetector:
    def __init__(self, method="KNN", n_neighbors=5, contamination=0.05):
        self.method = method
        self.n_neighbors = n_neighbors
        self.contamination = contamination
        self.scaler = StandardScaler()
        self.detector = None
        self.q_95_threshold = None
        
    def fit(self, X_train: pd.DataFrame):
        """Fits scaler and detector ONLY on training domain data."""
        X_scaled = self.scaler.fit_transform(X_train)
        
        if self.method == "KNN":
            self.detector = NearestNeighbors(n_neighbors=self.n_neighbors)
            self.detector.fit(X_scaled)
            
            # Compute distance to kth nearest neighbor for thresholding
            distances, _ = self.detector.kneighbors(X_scaled)
            k_distances = distances[:, -1]
            self.q_95_threshold = np.percentile(k_distances, 95)
            
        elif self.method == "IsolationForest":
            self.detector = IsolationForest(contamination=self.contamination, random_state=42)
            self.detector.fit(X_scaled)
            
            # IF returns anomaly score (negative is anomaly, positive is normal)
            # We invert it so higher is more OOD
            scores = -self.detector.score_samples(X_scaled)
            self.q_95_threshold = np.percentile(scores, 95)
            
        else:
            raise ValueError(f"Unknown OOD method: {self.method}")
            
    def compute_ood_score(self, X_eval: pd.DataFrame):
        """Returns raw OOD score and percentile-normalized OOD score."""
        if self.detector is None:
            raise ValueError("OOD detector not fitted.")
            
        X_scaled = self.scaler.transform(X_eval)
        
        if self.method == "KNN":
            distances, _ = self.detector.kneighbors(X_scaled)
            raw_scores = distances[:, -1]
        elif self.method == "IsolationForest":
            raw_scores = -self.detector.score_samples(X_scaled)
            
        # Normalization (0 to 1 based on q95 threshold, or just a percentile mapping if we kept training scores)
        # Simple heuristic for now: score / q_95_threshold. Clamped at 1.0. 
        # (Percentile would require saving all training scores)
        normalized_scores = np.clip(raw_scores / (self.q_95_threshold + 1e-9), 0, 1.0)
        
        is_ood_flag = raw_scores > self.q_95_threshold
        
        return pd.DataFrame({
            "ood_raw_score": raw_scores,
            "ood_normalized_score": normalized_scores,
            "ood_flag": is_ood_flag
        }, index=X_eval.index)

def evaluate_high_error_detection(ood_scores, abs_errors):
    """
    Evaluates OOD detector as a risk detector for high errors.
    high_error = abs_error > Q_75 (of OOF errors)
    """
    q_75_error = np.percentile(abs_errors, 75)
    is_high_error = abs_errors > q_75_error
    
    # This would typically be evaluated using sklearn.metrics (Precision, Recall, AUROC)
    # Returning the boolean array for downstream analysis
    return is_high_error
