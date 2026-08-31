[33mcommit 5325e25e73bcdec72d8caf70f67be975ecfbc1ba[m
Author: David Terian <dterian64@outlook.com>
Date:   Sat Jul 11 20:51:05 2026 -0700

    move alembic to schema-migration add entra sql groups

[1mdiff --git a/backend/main.py b/backend/main.py[m
[1mindex d4b0875..d781cc9 100644[m
[1m--- a/backend/main.py[m
[1m+++ b/backend/main.py[m
[36m@@ -111,9 +111,9 @@[m [masync def lifespan(app: FastAPI):[m
 [m
     logging.getLogger("uvicorn.access").addFilter(_HealthCheckLogFilter())[m
 [m
[31m-    # Startup: ensure all ORM-defined tables exist in the database[m
[31m-    sqlhelper.create_all_tables()[m
[31m-    logger.info("Database tables verified on startup.")[m
[32m+[m[32m    # Schema is owned by the standalone `schema-migration` project (Alembic) and[m
[32m+[m[32m    # applied by its pipeline (ADR-0001). The backend does not create/alter tables[m
[32m+[m[32m    # at startup — its runtime identity holds no DDL rights.[m
 [m
     # Start background task that evicts idle per-tenant fraud models[m
     eviction_task = asyncio.create_task(_model_eviction_loop())[m
