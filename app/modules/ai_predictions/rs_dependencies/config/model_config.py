MODEL_NAMES = [
    "Linear Regression", "Ridge", "Lasso", "ElasticNet",
    "Decision Tree", "ExtraTrees", "Random Forest",
    "XGBoost", "LightGBM", "CatBoost"
]

LINEAR_MODELS = ["Linear Regression", "Ridge", "Lasso", "ElasticNet"]

# Basic Hyperparameter grids for GridSearchCV
PARAM_GRIDS = {
    "Linear Regression": {
        # 'fit_intercept': [True, False] # Not usually tuned, but could be
    },
    "Ridge": {
        'model__alpha': [0.1, 1.0, 10.0]
    },
    "Lasso": {
        'model__alpha': [0.01, 0.1, 1.0]
    },
    "ElasticNet": {
        'model__alpha': [0.01, 0.1, 1.0],
        'model__l1_ratio': [0.2, 0.5, 0.8]
    },
    "Decision Tree": {
        'model__max_depth': [None, 5, 10, 20],
        'model__min_samples_split': [2, 5, 10]
    },
    "ExtraTrees": {
        'model__n_estimators': [50, 100],
        'model__max_depth': [None, 10, 20]
    },
    "Random Forest": {
        'model__n_estimators': [50, 100],
        'model__max_depth': [None, 10, 20]
    },
    "XGBoost": {
        'model__n_estimators': [50, 100],
        'model__learning_rate': [0.01, 0.1, 0.2],
        'model__max_depth': [3, 5, 7]
    },
    "LightGBM": {
        'model__n_estimators': [50, 100],
        'model__learning_rate': [0.01, 0.1, 0.2],
        'model__num_leaves': [31, 50]
    },
    "CatBoost": {
        'model__iterations': [50, 100],
        'model__learning_rate': [0.01, 0.1, 0.2],
        'model__depth': [4, 6, 8]
    }
}
