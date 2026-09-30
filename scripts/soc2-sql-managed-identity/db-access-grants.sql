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
DECLARE @migrationRole sysname = N'award_schema_migrator';
DECLARE @sql nvarchar(max);

-- Runtime group: data plane only (db_datareader + db_datawriter). No DDL.
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = @rw)
BEGIN
    SET @sql = N'CREATE USER ' + QUOTENAME(@rw) + N' FROM EXTERNAL PROVIDER;';
    EXEC sp_executesql @sql;
END;
IF ISNULL(IS_ROLEMEMBER(N'db_datareader', @rw), 0) <> 1
BEGIN
    SET @sql = N'ALTER ROLE db_datareader ADD MEMBER ' + QUOTENAME(@rw) + N';';
    EXEC sp_executesql @sql;
END;
IF ISNULL(IS_ROLEMEMBER(N'db_datawriter', @rw), 0) <> 1
BEGIN
    SET @sql = N'ALTER ROLE db_datawriter ADD MEMBER ' + QUOTENAME(@rw) + N';';
    EXEC sp_executesql @sql;
END;

-- Migration group: schema changes (db_ddladmin) + data plane. NOT db_owner.
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = @mig)
BEGIN
    SET @sql = N'CREATE USER ' + QUOTENAME(@mig) + N' FROM EXTERNAL PROVIDER;';
    EXEC sp_executesql @sql;
END;
IF ISNULL(IS_ROLEMEMBER(N'db_ddladmin', @mig), 0) <> 1
BEGIN
    SET @sql = N'ALTER ROLE db_ddladmin ADD MEMBER ' + QUOTENAME(@mig) + N';';
    EXEC sp_executesql @sql;
END;
IF ISNULL(IS_ROLEMEMBER(N'db_datareader', @mig), 0) <> 1
BEGIN
    SET @sql = N'ALTER ROLE db_datareader ADD MEMBER ' + QUOTENAME(@mig) + N';';
    EXEC sp_executesql @sql;
END;
IF ISNULL(IS_ROLEMEMBER(N'db_datawriter', @mig), 0) <> 1
BEGIN
    SET @sql = N'ALTER ROLE db_datawriter ADD MEMBER ' + QUOTENAME(@mig) + N';';
    EXEC sp_executesql @sql;
END;

-- Temporal enable/disable and ALTER SCHEMA require CONTROL on the affected
-- objects. Keep that authority inside an application-specific role rather than
-- granting db_owner to the migration identity.
IF SCHEMA_ID(N'integrity') IS NULL
    EXEC(N'CREATE SCHEMA [integrity] AUTHORIZATION [dbo];');
IF SCHEMA_ID(N'ops') IS NULL
    EXEC(N'CREATE SCHEMA [ops] AUTHORIZATION [dbo];');

IF DATABASE_PRINCIPAL_ID(@migrationRole) IS NULL
    EXEC(N'CREATE ROLE [award_schema_migrator] AUTHORIZATION [dbo];');

GRANT CONTROL ON SCHEMA::[dbo] TO [award_schema_migrator];
GRANT CONTROL ON SCHEMA::[integrity] TO [award_schema_migrator];
GRANT CONTROL ON SCHEMA::[ops] TO [award_schema_migrator];

IF NOT EXISTS (
    SELECT 1
    FROM sys.database_role_members AS drm
    JOIN sys.database_principals AS role_principal
      ON role_principal.principal_id = drm.role_principal_id
    JOIN sys.database_principals AS member_principal
      ON member_principal.principal_id = drm.member_principal_id
    WHERE role_principal.name = @migrationRole
      AND member_principal.name = @mig
)
BEGIN
    SET @sql = N'ALTER ROLE ' + QUOTENAME(@migrationRole)
             + N' ADD MEMBER ' + QUOTENAME(@mig) + N';';
    EXEC sp_executesql @sql;
END;

-- Verify: runtime remains data-plane only; migrations include db_ddladmin plus
-- the schema-control role required by temporal and schema-transfer migrations.
SELECT r.name AS role_name, m.name AS member_name
FROM sys.database_role_members drm
JOIN sys.database_principals r ON r.principal_id = drm.role_principal_id
JOIN sys.database_principals m ON m.principal_id = drm.member_principal_id
WHERE m.name IN (@rw, @mig)
ORDER BY m.name, r.name;

SELECT
    principal.name AS grantee,
    permission.state_desc,
    permission.permission_name,
    schema_name.name AS schema_name
FROM sys.database_permissions AS permission
JOIN sys.database_principals AS principal
  ON principal.principal_id = permission.grantee_principal_id
JOIN sys.schemas AS schema_name
  ON permission.class_desc = N'SCHEMA'
 AND schema_name.schema_id = permission.major_id
WHERE principal.name = @migrationRole
ORDER BY schema_name.name, permission.permission_name;
