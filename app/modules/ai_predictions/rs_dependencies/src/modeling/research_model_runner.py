"""
research_model_runner.py
========================
Executes model training and evaluation using predefined evaluation protocols.
Logs results and predictions to the Experiment Registry.
"""
import time
import numpy as np
import pandas as pd
from typing import Dict, List, Any

from src.modeling.metrics import compute_metrics
from src.utils.experiment_registry import log_experiment_result, log_predictions

def run_experiment(
    experiment_id: str,
    df: pd.DataFrame,
    features: List[str],
    target: str,
    protocol: Any,
    models: Dict[str, Any],
    feature_set_name: str,
    seeds: List[int] = [42],
    dataset_hash: str = "N/A",
    feature_hash: str = "N/A",
    config_hash: str = "N/A",
    git_commit: str = "N/A"
):
    """
    Runs an experiment evaluating multiple models on a specific feature set using a defined protocol.
    """
    print(f"Running Experiment {experiment_id} | Protocol: {protocol.name} | Feature Set: {feature_set_name}")
    
    # Check if target is not in features
    if target in features:
        raise ValueError(f"Target '{target}' is present in the feature list!")
        
    for seed in seeds:
        print(f"  Seed: {seed}")
        # Here we rely on the protocol to give us splits
        # Protocols yield (train_idx, test_idx)
        splits = list(protocol.split(df))
        
        n_groups = len(splits)
        
        for model_name, model_class_or_instance in models.items():
            print(f"    Model: {model_name}")
            
            # Initialize metrics accumulators for cross-validation
            all_actuals = []
            all_preds = []
            
            # For saving predictions
            fold_predictions = []
            
            fold_idx = 1
            train_times = []
            infer_times = []
            
            train_n_total = 0
            eval_n_total = 0
            
            for train_idx, test_idx in splits:
                X_train, y_train = df.iloc[train_idx][features], df.iloc[train_idx][target]
                X_test, y_test = df.iloc[test_idx][features], df.iloc[test_idx][target]
                
                train_n_total += len(X_train)
                eval_n_total += len(X_test)
                
                # Model initialization (assuming scikit-learn API)
                # If model_class_or_instance is a class, instantiate it. Otherwise, use as is (might not be safe for CV if it keeps state).
                if callable(model_class_or_instance):
                    model = model_class_or_instance(random_state=seed) if 'random_state' in model_class_or_instance().get_params() else model_class_or_instance()
                else:
                    import copy
                    model = copy.deepcopy(model_class_or_instance)
                    if hasattr(model, 'random_state'):
                        model.random_state = seed
                    elif hasattr(model, 'set_params'):
                        try:
                            model.set_params(random_state=seed)
                        except ValueError:
                            pass
                
                # Train
                t0 = time.time()
                model.fit(X_train, y_train)
                t1 = time.time()
                train_times.append(t1 - t0)
                
                # Predict
                t2 = time.time()
                preds = model.predict(X_test)
                t3 = time.time()
                infer_times.append(t3 - t2)
                
                all_actuals.extend(y_test.values)
                all_preds.extend(preds)
                
                # Create fold predictions dataframe
                fold_df = pd.DataFrame({
                    "flight": df.iloc[test_idx].get("flight", np.nan),
                    "route": df.iloc[test_idx].get("route", np.nan),
                    "date": df.iloc[test_idx].get("date", np.nan),
                    "domain": "TEMPORAL" if "TEMPORAL" in protocol.name else "CROSS_ROUTE",
                    "fold": fold_idx,
                    "seed": seed,
                    "actual": y_test.values,
                    "predicted": preds,
                    "residual": preds - y_test.values,
                    "absolute_error": np.abs(preds - y_test.values)
                })
                fold_predictions.append(fold_df)
                fold_idx += 1
            
            # Compute aggregate metrics over all folds (OOF for Temporal, Single for Cross-Route)
            metrics = compute_metrics(all_actuals, all_preds, include_r2=True)
            
            # Log results
            avg_train_n = train_n_total / n_groups if n_groups > 0 else 0
            avg_eval_n = eval_n_total / n_groups if n_groups > 0 else 0
            
            log_experiment_result(
                experiment_id=experiment_id,
                protocol=protocol.name,
                feature_set=feature_set_name,
                model_name=model_name,
                target=target,
                seed=seed,
                train_n=avg_train_n,
                eval_n=avg_eval_n,
                n_groups=n_groups,
                n_features=len(features),
                r2=metrics["R2"],
                mae=metrics["MAE"],
                medae=metrics["MedAE"],
                rmse=metrics["RMSE"],
                wmape=metrics["WMAPE"],
                bias=metrics["Bias"],
                training_time=np.mean(train_times) if train_times else 0,
                inference_time=np.mean(infer_times) if infer_times else 0,
                dataset_hash=dataset_hash,
                feature_hash=feature_hash,
                config_hash=config_hash,
                git_commit=git_commit
            )
            
            # Log predictions
            if fold_predictions:
                all_fold_preds_df = pd.concat(fold_predictions, ignore_index=True)
                log_predictions(all_fold_preds_df, experiment_id, protocol.name, feature_set_name, model_name, target)

    print(f"Finished Experiment {experiment_id} for {feature_set_name}")
