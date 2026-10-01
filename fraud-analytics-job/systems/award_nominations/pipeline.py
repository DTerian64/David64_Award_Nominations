"""Production stage registration for the Award Nomination system."""

GRAPH_MODULE = "systems.award_nominations.modeling.graph"
TABULAR_MODULE = "systems.award_nominations.modeling.tabular"
GNN_MODULE = "systems.award_nominations.modeling.gnn"
FORECAST_MODULE = "systems.award_nominations.forecasting.models"
HOLIDAY_MODULE = "systems.award_nominations.forecasting.holidays"

TENANT_STAGES = [
    {
        "key": "graph_analytics",
        "stage": "GRAPH",
        "label": "Graph Analytics",
        "module": GRAPH_MODULE,
    },
    {
        "key": "train_tabular_model",
        "stage": "TABULAR",
        "label": "Tabular model training",
        "module": TABULAR_MODULE,
    },
    {
        "key": "train_gnn_model",
        "stage": "GNN",
        "label": "GNN model training",
        "module": GNN_MODULE,
    },
    {
        "key": "forecast_models",
        "stage": "FORECAST",
        "label": "Forecast models",
        "module": FORECAST_MODULE,
    },
]

STANDALONE_STAGES = [
    {"key": "graph_analytics", "label": "Graph Analytics", "module": GRAPH_MODULE},
    {
        "key": "train_tabular_model",
        "label": "Tabular model training",
        "module": TABULAR_MODULE,
    },
    {"key": "train_gnn_model", "label": "GNN model training", "module": GNN_MODULE},
    {"key": "sync_holidays", "label": "Holiday sync", "module": HOLIDAY_MODULE},
    {"key": "forecast_models", "label": "Forecast models", "module": FORECAST_MODULE},
]

GLOBAL_PREPARATION_MODULES = (GRAPH_MODULE, HOLIDAY_MODULE)

__all__ = [
    "GLOBAL_PREPARATION_MODULES",
    "STANDALONE_STAGES",
    "TENANT_STAGES",
]
