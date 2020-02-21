# ansible-role-mssql-backup

An Ansible Role that helps manage Microsoft SQL Server Backup jobs

## Dependencies

A Microsoft SQL Server on Linux installation.

## NOTES

As of SQL Server 2019 on Linux, it is not currently possible to use `xp_cmdshell`, nor utilize the Maintenance Plan features available on a Windows installation of SQL Server.

## Role Variables

Available variables are listed below, along with default values (see `defaults/main.yml`):

    mssql_port: 1433
    
The port to use when connecting to the mssql server

    mssql_admin_user: sa
    mssql_admin_password: "P@sswOrd!"

The administrative credentials of the mssql server

    mssql_home: /var/opt/mssql
    mssql_user: mssql
    mssql_group: mssql

The mssql user definition. The backup rotation script will be placed here

    mssql_backup_path: /var/opt/mssql/backups

The path to where the backups should be stored

    mssql_backup_count: 14

How many backups to keep. The backup rotation script will sort by newest first, then remove any additional backups. Depending on how you have the schedule set, this could be 14 days worth of backups, or 14 weeks.

    mssql_schedule_type: daily
    mssql_schedule_interval: 1
    mssql_schedule_start_time: '003000'

What time to run the schedule at, default is set to 12:30 am

Available Schedule types: `once`, `daily`, `weekly`, `monthly`, `onstart`, `idle`

    mssql_backup_cron_minute: 0
    mssql_backup_cron_hour: 2
    mssql_backup_cron_month: *
    mssql_backup_cron_weekday: *

When should the cron job run to handle backup rotation. This is set for 2:00 am   

## Testing

Testing requires Molecule. 

## License

MIT / BSD

## Author Information

This role was created in 2020s by [David Lundgren](https://www.davidscode.com/).
