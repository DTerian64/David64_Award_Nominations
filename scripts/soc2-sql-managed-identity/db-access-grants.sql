-- db-access-grants.sql -- ADR-0001 one-time bootstrap grants.
--
-- The SQL server is behind a private endpoint + firewall, so run this from a
-- firewall-whitelisted machine (your dev box, via my_ips), connected to the
-- app database as an Entra admin (a member of sql-admins-<env>) -- NOT SQL auth.
--
-- Works in SSMS / Azure Data Studio / sqlcmd with no special mode: set @env
-- below and run. Idempotent -- safe to re-run.
SET NOCOUNT ON;

-- >>> Set your environment here (sandbox, prod, ...) <<<
DECLARE @env sysname = N'sandbox';

DECLARE @rw  sysname = N'sql-app-readwrite-' + @env;
DECLARE @mig sysname = N'sql-migrations-'    + @env;
DECLARE @sql nvarchar(max);

-- Runtime group: data plane only (db_datareader + db_datawriter). No DDL.
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = @rw)
BEGIN
    SET @sql = N'CREATE USER ' + QUOTENAME(@rw) + N' FROM EXTERNAL PROVIDER;';
    EXEC sp_executesql @sql;
END;
SET @sql = N'ALTER ROLE db_datareader ADD MEMBER ' + QUOTENAME(@rw) + N';'; EXEC sp_executesql @sql;
SET @sql = N'ALTER ROLE db_datawriter ADD MEMBER ' + QUOTENAME(@rw) + N';'; EXEC sp_executesql @sql;

-- Migration group: schema changes (db_ddladmin) + data plane. NOT db_owner.
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = @mig)
BEGIN
    SET @sql = N'CREATE USER ' + QUOTENAME(@mig) + N' FROM EXTERNAL PROVIDER;';
    EXEC sp_executesql @sql;
END;
SET @sql = N'ALTER ROLE db_ddladmin   ADD MEMBER ' + QUOTENAME(@mig) + N';'; EXEC sp_executesql @sql;
SET @sql = N'ALTER ROLE db_datareader ADD MEMBER ' + QUOTENAME(@mig) + N';'; EXEC sp_executesql @sql;
SET @sql = N'ALTER ROLE db_datawriter ADD MEMBER ' + QUOTENAME(@mig) + N';'; EXEC sp_executesql @sql;

-- Verify: runtime must show reader/writer only; migrations must include db_ddladmin.
SELECT r.name AS role_name, m.name AS member_name
FROM sys.database_role_members drm
JOIN sys.database_principals r ON r.principal_id = drm.role_principal_id
JOIN sys.database_principals m ON m.principal_id = drm.member_principal_id
WHERE m.name IN (@rw, @mig)
ORDER BY m.name, r.name;
